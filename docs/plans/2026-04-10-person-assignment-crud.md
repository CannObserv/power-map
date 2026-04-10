# Person-detail Assignment CRUD Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add inline create and edit to the Role Assignments table on the Person detail page, mirroring the pattern built for Role detail in #75.

**Architecture:** New `people_assignments.py` router (prefix `/people/{person_id}/assignments`) handles all inline CRUD. A role search typeahead endpoint is added to `roles.py`. Four new partials in `admin/people/partials/` handle rendering. The person detail query gains `role_id` so role titles can link to role detail.

**Tech Stack:** FastAPI, asyncpg, Jinja2, HTMX (row-level swap pattern), vanilla JS (typeahead + is_current↔end_date toggle)

---

## File Map

**Create:**
- `src/api/admin/people_assignments.py` — all inline CRUD routes for person-scoped assignments
- `src/templates/admin/roles/partials/_search_results.html` — role typeahead result items
- `src/templates/admin/people/partials/_assignment_row.html` — single read row (6 cols, with Edit button)
- `src/templates/admin/people/partials/_assignment_rows.html` — full tbody loop
- `src/templates/admin/people/partials/_assignment_form_row.html` — new-assignment form row (role typeahead)
- `src/templates/admin/people/partials/_assignment_edit_row.html` — edit form row (6 individual `<td>`)
- `tests/api/admin/test_people_assignments_inline.py` — integration tests

**Modify:**
- `src/api/admin/roles.py` — add `GET /roles/search/` typeahead endpoint
- `src/api/admin/people.py` — add `r.id AS role_id` to person detail role_assignments query
- `src/templates/admin/people/detail.html` — table id, 6th column header, Add button, use `{% include %}`
- `src/api/admin/router.py` — mount `people_assignments_module.router`

---

## Task 1: Role search typeahead endpoint

The new-assignment form row needs a role typeahead. Add `GET /admin/roles/search/?q=...` returning
`<li data-id="{id}" data-label="{org} — {title}">` items — the same shape as people search.

**Files:**
- Modify: `src/api/admin/roles.py`
- Create: `src/templates/admin/roles/partials/_search_results.html`
- Create: `tests/api/admin/test_roles_search.py`

- [ ] **Step 1.1: Write failing tests**

```python
# tests/api/admin/test_roles_search.py
"""Tests for role search typeahead endpoint."""
import os
import asyncpg
import pytest
from httpx import ASGITransport, AsyncClient
from src.api.admin.deps import get_db
from src.api.main import app
from src.core.db import apply_schema, generate_id

pytestmark = pytest.mark.integration

AUTH_HEADERS = {"X-ExeDev-UserID": "test-user", "X-ExeDev-Email": "test@example.com"}
HTMX_HEADERS = {**AUTH_HEADERS, "HX-Request": "true"}


@pytest.fixture
async def db():
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        pytest.skip("DATABASE_URL not set")
    conn = await asyncpg.connect(dsn)
    try:
        await apply_schema(conn)
        tr = conn.transaction()
        await tr.start()
        yield conn
        await tr.rollback()
    finally:
        await conn.close()


@pytest.fixture
async def client(db):
    async def _get_db_override():
        yield db
    app.dependency_overrides[get_db] = _get_db_override
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    app.dependency_overrides.pop(get_db, None)


@pytest.fixture
async def role_id(db):
    oid = generate_id()
    await db.execute("INSERT INTO organizations (id) VALUES ($1)", oid)
    await db.execute(
        "INSERT INTO organization_names (id, organization_id, name, is_canonical)"
        " VALUES ($1, $2, $3, TRUE)",
        generate_id(), oid, "Acme Corp",
    )
    rid = generate_id()
    await db.execute(
        "INSERT INTO roles (id, organization_id, title) VALUES ($1, $2, $3)",
        rid, oid, "Executive Director",
    )
    return rid


async def test_search_returns_matching_role(client, role_id):
    r = await client.get("/admin/roles/search/?q=Executive", headers=HTMX_HEADERS)
    assert r.status_code == 200
    assert b"Executive Director" in r.content


async def test_search_result_has_data_id(client, role_id):
    r = await client.get("/admin/roles/search/?q=Executive", headers=HTMX_HEADERS)
    assert r.status_code == 200
    assert f'data-id="{role_id}"'.encode() in r.content


async def test_search_result_label_includes_org_name(client, role_id):
    r = await client.get("/admin/roles/search/?q=Executive", headers=HTMX_HEADERS)
    assert r.status_code == 200
    assert b"Acme Corp" in r.content


async def test_search_empty_query_returns_empty(client, role_id):
    r = await client.get("/admin/roles/search/?q=", headers=HTMX_HEADERS)
    assert r.status_code == 200
    assert b"data-id" not in r.content


async def test_search_no_match_returns_empty(client, role_id):
    r = await client.get("/admin/roles/search/?q=zzznomatch", headers=HTMX_HEADERS)
    assert r.status_code == 200
    assert b"data-id" not in r.content


async def test_search_excludes_archived_roles(client, db):
    oid = generate_id()
    await db.execute("INSERT INTO organizations (id) VALUES ($1)", oid)
    await db.execute(
        "INSERT INTO organization_names (id, organization_id, name, is_canonical)"
        " VALUES ($1, $2, $3, TRUE)",
        generate_id(), oid, "Old Org",
    )
    rid = generate_id()
    await db.execute(
        "INSERT INTO roles (id, organization_id, title, archived_at)"
        " VALUES ($1, $2, $3, NOW())",
        rid, oid, "Archived Role",
    )
    r = await client.get("/admin/roles/search/?q=Archived", headers=HTMX_HEADERS)
    assert r.status_code == 200
    assert b"Archived Role" not in r.content
```

- [ ] **Step 1.2: Run to confirm failure**

```bash
export $(cat /etc/power-map/.env | xargs) 2>/dev/null && export $(cat .env | xargs) 2>/dev/null
uv run pytest tests/api/admin/test_roles_search.py -v
```

Expected: `AttributeError` or 404 — the endpoint doesn't exist yet.

- [ ] **Step 1.3: Add search endpoint to `roles.py`**

Add after the existing imports (note: `escape_like` is already imported in `roles.py`):

```python
@router.get("/search/")
async def roles_search(
    request: Request,
    q: str = "",
    user: AdminUser | RedirectResponse = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Typeahead search — returns HTML fragment of matching roles."""
    redirect, user = check_auth(user)
    if redirect:
        return redirect
    results = []
    if q.strip():
        results = await db.fetch(
            """SELECT r.id, r.title, dn.display_name AS org_name
               FROM roles r
               LEFT JOIN v_org_display_names dn ON dn.organization_id = r.organization_id
               WHERE r.archived_at IS NULL
                 AND (r.title ILIKE $1 ESCAPE '\\' OR dn.display_name ILIKE $1 ESCAPE '\\')
               ORDER BY dn.display_name NULLS LAST, r.title
               LIMIT 20""",
            f"%{escape_like(q.strip())}%",
        )
    return templates.TemplateResponse(
        request,
        "admin/roles/partials/_search_results.html",
        {"results": results},
    )
```

Place it **before** `@router.get("/{role_id}/")` to avoid path conflicts.

- [ ] **Step 1.4: Create `src/templates/admin/roles/partials/_search_results.html`**

```html
{# admin/roles/partials/_search_results.html #}
{% for r in results %}
<li id="opt-{{ r.id }}" role="option"
    data-id="{{ r.id }}"
    data-label="{{ r.org_name }} — {{ r.title }}">{{ r.org_name }} — {{ r.title }}</li>
{% endfor %}
```

- [ ] **Step 1.5: Run tests to confirm passing**

```bash
uv run pytest tests/api/admin/test_roles_search.py -v
```

Expected: all 6 pass.

- [ ] **Step 1.6: Commit**

```bash
git add src/api/admin/roles.py \
        src/templates/admin/roles/partials/_search_results.html \
        tests/api/admin/test_roles_search.py
git commit -m "#76 feat: add role search typeahead endpoint"
```

---

## Task 2: `people_assignments.py` — new-row and create routes

The new-row returns a form with a role typeahead. Create persists the assignment and returns a
re-sorted full tbody.

**Files:**
- Create: `src/api/admin/people_assignments.py`
- Create: `src/templates/admin/people/partials/_assignment_form_row.html`
- Create: `src/templates/admin/people/partials/_assignment_row.html`
- Create: `src/templates/admin/people/partials/_assignment_rows.html`
- Create: `tests/api/admin/test_people_assignments_inline.py` (partial)
- Modify: `src/api/admin/router.py`

- [ ] **Step 2.1: Write failing tests (new-row + create section)**

Create `tests/api/admin/test_people_assignments_inline.py`:

```python
"""Integration tests for inline assignment CRUD on person detail."""
import json
import os

import asyncpg
import pytest
from httpx import ASGITransport, AsyncClient

from src.api.admin.deps import get_db
from src.api.main import app
from src.core.db import apply_schema, generate_id

pytestmark = pytest.mark.integration

AUTH_HEADERS = {"X-ExeDev-UserID": "test-user", "X-ExeDev-Email": "test@example.com"}
HTMX_HEADERS = {**AUTH_HEADERS, "HX-Request": "true"}


@pytest.fixture
async def db():
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        pytest.skip("DATABASE_URL not set")
    conn = await asyncpg.connect(dsn)
    try:
        await apply_schema(conn)
        tr = conn.transaction()
        await tr.start()
        yield conn
        await tr.rollback()
    finally:
        await conn.close()


@pytest.fixture
async def client(db):
    async def _get_db_override():
        yield db
    app.dependency_overrides[get_db] = _get_db_override
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    app.dependency_overrides.pop(get_db, None)


@pytest.fixture
async def person_id(db):
    pid = generate_id()
    await db.execute("INSERT INTO people (id) VALUES ($1)", pid)
    await db.execute(
        "INSERT INTO person_names (id, person_id, name, is_canonical) VALUES ($1, $2, $3, TRUE)",
        generate_id(), pid, "Jane Doe",
    )
    return pid


@pytest.fixture
async def role_id(db):
    oid = generate_id()
    await db.execute("INSERT INTO organizations (id) VALUES ($1)", oid)
    await db.execute(
        "INSERT INTO organization_names (id, organization_id, name, is_canonical)"
        " VALUES ($1, $2, $3, TRUE)",
        generate_id(), oid, "Test Org",
    )
    rid = generate_id()
    await db.execute(
        "INSERT INTO roles (id, organization_id, title) VALUES ($1, $2, $3)",
        rid, oid, "Executive Director",
    )
    return rid


# ---------------------------------------------------------------------------
# New row form
# ---------------------------------------------------------------------------


async def test_new_row_returns_form(client, person_id):
    r = await client.get(
        f"/admin/people/{person_id}/assignments/new-row/", headers=HTMX_HEADERS
    )
    assert r.status_code == 200
    assert b"<form" in r.content
    assert b"role-search" in r.content


async def test_new_row_unknown_person_returns_404(client):
    r = await client.get(
        f"/admin/people/{generate_id()}/assignments/new-row/", headers=HTMX_HEADERS
    )
    assert r.status_code == 404


async def test_new_row_is_current_uses_pill_toggle(client, person_id):
    r = await client.get(
        f"/admin/people/{person_id}/assignments/new-row/", headers=HTMX_HEADERS
    )
    assert r.status_code == 200
    assert b'class="toggle"' in r.content
    assert b"toggle__track" in r.content


async def test_new_row_js_disables_end_date_when_is_current_checked(client, person_id):
    r = await client.get(
        f"/admin/people/{person_id}/assignments/new-row/", headers=HTMX_HEADERS
    )
    assert r.status_code == 200
    assert b"endDt.disabled = true" in r.content
    assert b"endDt.disabled = false" in r.content


# ---------------------------------------------------------------------------
# Create assignment
# ---------------------------------------------------------------------------


async def test_create_persists_assignment(client, person_id, role_id, db):
    r = await client.post(
        f"/admin/people/{person_id}/assignments/",
        headers=HTMX_HEADERS,
        data={
            "role_id": role_id,
            "start_date": "2024-01-15",
            "end_date": "",
            "is_current": "true",
        },
    )
    assert r.status_code == 200
    row = await db.fetchrow(
        "SELECT * FROM role_assignments WHERE person_id=$1 AND role_id=$2",
        person_id, role_id,
    )
    assert row is not None
    assert row["is_current"] is True
    assert str(row["start_date"]) == "2024-01-15"


async def test_create_with_end_date(client, person_id, role_id, db):
    r = await client.post(
        f"/admin/people/{person_id}/assignments/",
        headers=HTMX_HEADERS,
        data={"role_id": role_id, "start_date": "2020-01-01", "end_date": "2023-12-31"},
    )
    assert r.status_code == 200
    row = await db.fetchrow(
        "SELECT * FROM role_assignments WHERE person_id=$1 AND role_id=$2",
        person_id, role_id,
    )
    assert row is not None
    assert str(row["end_date"]) == "2023-12-31"


async def test_create_returns_tbody_with_org_and_role(client, person_id, role_id):
    r = await client.post(
        f"/admin/people/{person_id}/assignments/",
        headers=HTMX_HEADERS,
        data={"role_id": role_id, "start_date": "2024-01-01"},
    )
    assert r.status_code == 200
    assert b"Test Org" in r.content
    assert b"Executive Director" in r.content


async def test_create_returns_success_flash(client, person_id, role_id):
    r = await client.post(
        f"/admin/people/{person_id}/assignments/",
        headers=HTMX_HEADERS,
        data={"role_id": role_id, "start_date": "2024-01-01"},
    )
    trigger = json.loads(r.headers["hx-trigger"])
    assert trigger["showFlash"]["level"] == "success"


async def test_create_tbody_includes_edit_url(client, person_id, role_id):
    r = await client.post(
        f"/admin/people/{person_id}/assignments/",
        headers=HTMX_HEADERS,
        data={"role_id": role_id, "start_date": "2024-01-01"},
    )
    assert r.status_code == 200
    assert f"/admin/people/{person_id}/assignments/".encode() in r.content
    assert b"edit-row" in r.content


async def test_create_missing_role_returns_error(client, person_id):
    r = await client.post(
        f"/admin/people/{person_id}/assignments/",
        headers=HTMX_HEADERS,
        data={"role_id": "", "start_date": "2024-01-01"},
    )
    assert r.status_code == 200
    trigger = json.loads(r.headers["hx-trigger"])
    assert trigger["showFlash"]["level"] == "error"
    assert b"<form" in r.content


async def test_create_current_with_end_date_returns_error(client, person_id, role_id):
    r = await client.post(
        f"/admin/people/{person_id}/assignments/",
        headers=HTMX_HEADERS,
        data={
            "role_id": role_id,
            "start_date": "2024-01-01",
            "end_date": "2024-06-01",
            "is_current": "true",
        },
    )
    assert r.status_code == 200
    trigger = json.loads(r.headers["hx-trigger"])
    assert trigger["showFlash"]["level"] == "error"


async def test_create_duplicate_start_date_returns_error(client, person_id, role_id, db):
    """UniqueViolationError on (person_id, role_id, start_date) duplicate."""
    await db.execute(
        """INSERT INTO role_assignments (id, person_id, role_id, is_current, start_date)
           VALUES ($1, $2, $3, FALSE, '2024-01-01')""",
        generate_id(), person_id, role_id,
    )
    r = await client.post(
        f"/admin/people/{person_id}/assignments/",
        headers=HTMX_HEADERS,
        data={"role_id": role_id, "start_date": "2024-01-01"},
    )
    assert r.status_code == 200
    trigger = json.loads(r.headers["hx-trigger"])
    assert trigger["showFlash"]["level"] == "error"
    assert b"<form" in r.content


async def test_create_non_htmx_redirects(client, person_id, role_id):
    r = await client.post(
        f"/admin/people/{person_id}/assignments/",
        headers=AUTH_HEADERS,
        data={"role_id": role_id, "start_date": "2024-01-01"},
        follow_redirects=False,
    )
    assert r.status_code == 303
```

- [ ] **Step 2.2: Run to confirm failure**

```bash
uv run pytest tests/api/admin/test_people_assignments_inline.py -v -k "new_row or create"
```

Expected: 404s (router not mounted yet).

- [ ] **Step 2.3: Create `src/api/admin/people_assignments.py`**

```python
"""Inline assignment CRUD routes for the person detail page."""

import datetime

import asyncpg
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from src.api.admin.deps import AdminUser, check_auth, flash_trigger, get_admin_user, get_db, is_htmx
from src.core.db import generate_id

templates = Jinja2Templates(directory="src/templates")
router = APIRouter(prefix="/people/{person_id}/assignments", tags=["admin-people-assignments"])


def _parse_date(value: str) -> datetime.date | None:
    """Parse ISO date string, return None if empty."""
    value = value.strip()
    if not value:
        return None
    return datetime.date.fromisoformat(value)


async def _get_person_or_404(person_id: str, db):
    """Fetch person row or raise 404."""
    row = await db.fetchrow("SELECT id FROM people WHERE id = $1", person_id)
    if not row:
        raise HTTPException(status_code=404, detail="Person not found")
    return row


async def fetch_person_assignments(person_id: str, db) -> list:
    """Fetch all assignments for a person, sorted for display."""
    return await db.fetch(
        """SELECT ra.id, ra.is_current, ra.start_date, ra.end_date, ra.archived_at,
                  r.id AS role_id, r.title AS role_title,
                  o.id AS org_id,
                  dn.display_name AS org_name
           FROM role_assignments ra
           JOIN roles r ON r.id = ra.role_id
           JOIN organizations o ON o.id = r.organization_id
           LEFT JOIN v_org_display_names dn ON dn.organization_id = o.id
           WHERE ra.person_id = $1
           ORDER BY ra.is_current DESC, ra.start_date DESC NULLS LAST""",
        person_id,
    )


async def _get_assignment(assignment_id: str, person_id: str, db):
    """Fetch a single assignment with role/org info, or raise 404."""
    row = await db.fetchrow(
        """SELECT ra.id, ra.is_current, ra.start_date, ra.end_date, ra.archived_at,
                  r.id AS role_id, r.title AS role_title,
                  o.id AS org_id,
                  dn.display_name AS org_name
           FROM role_assignments ra
           JOIN roles r ON r.id = ra.role_id
           JOIN organizations o ON o.id = r.organization_id
           LEFT JOIN v_org_display_names dn ON dn.organization_id = o.id
           WHERE ra.id = $1 AND ra.person_id = $2""",
        assignment_id, person_id,
    )
    if not row:
        raise HTTPException(status_code=404, detail="Assignment not found")
    return row


@router.get("/new-row/")
async def assignment_new_row(
    person_id: str,
    request: Request,
    user: AdminUser | RedirectResponse = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Return blank inline assignment form row."""
    redirect, user = check_auth(user)
    if redirect:
        return redirect
    await _get_person_or_404(person_id, db)
    return templates.TemplateResponse(
        request,
        "admin/people/partials/_assignment_form_row.html",
        {
            "person_id": person_id,
            "start_date_input": "",
            "end_date_input": "",
            "is_current_input": False,
        },
    )


@router.post("/")
async def assignment_create(
    person_id: str,
    request: Request,
    role_id: str = Form(""),
    start_date: str = Form(""),
    end_date: str = Form(""),
    is_current: str = Form(""),
    user: AdminUser | RedirectResponse = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Create a new role assignment (inline HTMX path)."""
    redirect, user = check_auth(user)
    if redirect:
        return redirect
    await _get_person_or_404(person_id, db)

    role_id_val = role_id.strip()
    is_current_val = bool(is_current)

    def _form_error(msg: str):
        return templates.TemplateResponse(
            request,
            "admin/people/partials/_assignment_form_row.html",
            {
                "person_id": person_id,
                "start_date_input": start_date,
                "end_date_input": end_date,
                "is_current_input": is_current_val,
            },
            headers={
                **flash_trigger("error", msg),
                "HX-Retarget": "#person-assignment-row-new",
                "HX-Reswap": "outerHTML",
            },
        )

    if not role_id_val:
        if not is_htmx(request):
            return RedirectResponse(f"/admin/people/{person_id}/", status_code=303)
        return _form_error("Role is required.")

    try:
        start_date_val = _parse_date(start_date)
        end_date_val = _parse_date(end_date)
    except ValueError:
        if not is_htmx(request):
            return RedirectResponse(f"/admin/people/{person_id}/", status_code=303)
        return _form_error("Invalid date format. Use YYYY-MM-DD.")

    ra_id = generate_id()
    try:
        await db.execute(
            """INSERT INTO role_assignments
               (id, person_id, role_id, is_current, start_date, end_date)
               VALUES ($1, $2, $3, $4, $5, $6)""",
            ra_id, person_id, role_id_val, is_current_val, start_date_val, end_date_val,
        )
    except asyncpg.CheckViolationError:
        if not is_htmx(request):
            return RedirectResponse(f"/admin/people/{person_id}/", status_code=303)
        return _form_error("Current assignments cannot have an end date.")
    except asyncpg.UniqueViolationError:
        if not is_htmx(request):
            return RedirectResponse(f"/admin/people/{person_id}/", status_code=303)
        return _form_error("An assignment for this role with this start date already exists.")
    except asyncpg.ForeignKeyViolationError:
        if not is_htmx(request):
            return RedirectResponse(f"/admin/people/{person_id}/", status_code=303)
        return _form_error("Role not found.")

    if not is_htmx(request):
        return RedirectResponse(f"/admin/people/{person_id}/", status_code=303)

    assignments = await fetch_person_assignments(person_id, db)
    return templates.TemplateResponse(
        request,
        "admin/people/partials/_assignment_rows.html",
        {"assignments": assignments, "person_id": person_id},
        headers=flash_trigger("success", "Assignment added."),
    )


@router.get("/{assignment_id}/read-row/")
async def assignment_read_row(
    person_id: str,
    assignment_id: str,
    request: Request,
    user: AdminUser | RedirectResponse = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Return read partial for a single assignment row."""
    redirect, user = check_auth(user)
    if redirect:
        return redirect
    ra = await _get_assignment(assignment_id, person_id, db)
    return templates.TemplateResponse(
        request,
        "admin/people/partials/_assignment_row.html",
        {"ra": ra, "person_id": person_id},
    )


@router.get("/{assignment_id}/edit-row/")
async def assignment_edit_row_get(
    person_id: str,
    assignment_id: str,
    request: Request,
    user: AdminUser | RedirectResponse = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Return edit form partial for a single assignment row."""
    redirect, user = check_auth(user)
    if redirect:
        return redirect
    ra = await _get_assignment(assignment_id, person_id, db)
    if ra["archived_at"]:
        raise HTTPException(status_code=409, detail="Cannot edit an archived assignment")
    return templates.TemplateResponse(
        request,
        "admin/people/partials/_assignment_edit_row.html",
        {
            "ra": ra,
            "person_id": person_id,
            "start_date_input": ra["start_date"].isoformat() if ra["start_date"] else "",
            "end_date_input": ra["end_date"].isoformat() if ra["end_date"] else "",
            "is_current_input": ra["is_current"],
        },
    )


@router.post("/{assignment_id}/edit-row/")
async def assignment_edit_row_post(
    person_id: str,
    assignment_id: str,
    request: Request,
    start_date: str = Form(""),
    end_date: str = Form(""),
    is_current: str = Form(""),
    user: AdminUser | RedirectResponse = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Save assignment edits; return full sorted tbody."""
    redirect, user = check_auth(user)
    if redirect:
        return redirect
    ra = await _get_assignment(assignment_id, person_id, db)
    if ra["archived_at"]:
        raise HTTPException(status_code=409, detail="Cannot edit an archived assignment")
    is_current_val = bool(is_current)

    def _error_ctx():
        return {
            "ra": ra,
            "person_id": person_id,
            "start_date_input": start_date,
            "end_date_input": end_date,
            "is_current_input": is_current_val,
        }

    try:
        start_date_val = _parse_date(start_date)
        end_date_val = _parse_date(end_date)
    except ValueError:
        if not is_htmx(request):
            return RedirectResponse(f"/admin/people/{person_id}/", status_code=303)
        return templates.TemplateResponse(
            request,
            "admin/people/partials/_assignment_edit_row.html",
            _error_ctx(),
            headers={
                **flash_trigger("error", "Invalid date format. Use YYYY-MM-DD."),
                "HX-Retarget": f"#person-assignment-row-{assignment_id}",
                "HX-Reswap": "outerHTML",
            },
        )

    try:
        await db.execute(
            """UPDATE role_assignments
               SET is_current=$1, start_date=$2, end_date=$3
               WHERE id=$4""",
            is_current_val, start_date_val, end_date_val, assignment_id,
        )
    except asyncpg.CheckViolationError:
        if not is_htmx(request):
            return RedirectResponse(f"/admin/people/{person_id}/", status_code=303)
        return templates.TemplateResponse(
            request,
            "admin/people/partials/_assignment_edit_row.html",
            _error_ctx(),
            headers={
                **flash_trigger("error", "Current assignments cannot have an end date."),
                "HX-Retarget": f"#person-assignment-row-{assignment_id}",
                "HX-Reswap": "outerHTML",
            },
        )

    if not is_htmx(request):
        return RedirectResponse(f"/admin/people/{person_id}/", status_code=303)

    assignments = await fetch_person_assignments(person_id, db)
    return templates.TemplateResponse(
        request,
        "admin/people/partials/_assignment_rows.html",
        {"assignments": assignments, "person_id": person_id},
        headers=flash_trigger("success", "Assignment saved."),
    )
```

- [ ] **Step 2.4: Mount router in `router.py`**

Add alongside the other people sub-routers:

```python
# At top with other imports:
from src.api.admin import people_assignments as people_assignments_module

# After people_identifiers_module mount:
admin_router.include_router(people_assignments_module.router)
```

- [ ] **Step 2.5: Create `src/templates/admin/people/partials/_assignment_row.html`**

```html
{# admin/people/partials/_assignment_row.html — single assignment read row #}
<tr id="person-assignment-row-{{ ra.id }}">
  <td><a href="/admin/orgs/{{ ra.org_id }}/">{{ ra.org_name or '(unnamed)' }}</a></td>
  <td><a href="/admin/roles/{{ ra.role_id }}/">{{ ra.role_title or '(untitled)' }}</a></td>
  <td>{{ ra.start_date or '—' }}</td>
  <td>{{ ra.end_date or '—' }}</td>
  <td>
    {% if ra.archived_at %}<span class="badge badge--archived">Archived</span>
    {% elif ra.is_current %}<span class="badge badge--active">Current</span>
    {% else %}<span class="badge badge--inactive">Former</span>{% endif %}
  </td>
  <td style="text-align:right">
    {% if not ra.archived_at %}
    <button type="button" class="btn btn--sm btn--secondary"
            hx-get="/admin/people/{{ person_id }}/assignments/{{ ra.id }}/edit-row/"
            hx-target="#person-assignment-row-{{ ra.id }}"
            hx-swap="outerHTML">Edit</button>
    {% endif %}
  </td>
</tr>
```

- [ ] **Step 2.6: Create `src/templates/admin/people/partials/_assignment_rows.html`**

```html
{# admin/people/partials/_assignment_rows.html — full tbody for person assignments table #}
{% for ra in assignments %}
{% include "admin/people/partials/_assignment_row.html" %}
{% else %}
<tr><td colspan="6" style="text-align:center;color:var(--color-text-muted)">No role assignments</td></tr>
{% endfor %}
```

- [ ] **Step 2.7: Create `src/templates/admin/people/partials/_assignment_form_row.html`**

```html
{# admin/people/partials/_assignment_form_row.html — new assignment inline form row #}
<tr id="person-assignment-row-new">
  <td colspan="6" style="padding:var(--space-2) var(--space-4)">
    <form hx-post="/admin/people/{{ person_id }}/assignments/"
          hx-target="#person-assignments-table tbody"
          hx-swap="innerHTML"
          style="display:flex;gap:var(--space-2);align-items:flex-start;flex-wrap:wrap">
      <div class="form-group" style="margin-bottom:0;flex:2;min-width:12rem;position:relative">
        <input type="text" autocomplete="off" placeholder="Search for a role…"
               id="role-search-display"
               hx-get="/admin/roles/search/"
               hx-trigger="input changed delay:200ms"
               hx-target="#role-search-results"
               hx-swap="innerHTML"
               hx-params="q"
               name="q"
               role="combobox"
               aria-expanded="false"
               aria-haspopup="listbox"
               aria-controls="role-search-results"
               aria-autocomplete="list">
        <input type="hidden" name="role_id" id="role-id-hidden">
        <ul id="role-search-results" class="typeahead-results" role="listbox"></ul>
      </div>
      <div class="form-group" style="margin-bottom:0;flex:1;min-width:8rem">
        <input type="date" name="start_date" placeholder="Start date"
               title="Start date" value="{{ start_date_input or '' }}">
      </div>
      <div class="form-group" style="margin-bottom:0;flex:1;min-width:8rem">
        <input type="date" name="end_date" placeholder="End date"
               title="End date" id="end-date-input" value="{{ end_date_input or '' }}">
      </div>
      <label class="toggle" style="align-self:center">
        <input type="checkbox" name="is_current" value="true" id="is-current-cb"
               aria-label="Current"
               {% if is_current_input %}checked{% endif %}>
        <span class="toggle__track"><span class="toggle__thumb"></span></span>
      </label>
      <div style="display:flex;gap:var(--space-2);white-space:nowrap">
        <button type="submit" class="btn btn--sm btn--primary">Save</button>
        <button type="button" class="btn btn--sm btn--secondary"
                onclick="this.closest('tr').remove()">Cancel</button>
      </div>
    </form>
    <script>
    (function() {
      var inp    = document.getElementById('role-search-display');
      var ul     = document.getElementById('role-search-results');
      var hidden = document.getElementById('role-id-hidden');
      var cb     = document.getElementById('is-current-cb');
      var endDt  = document.getElementById('end-date-input');
      var activeIdx = -1;

      cb.addEventListener('change', function() {
        if (cb.checked) { endDt.value = ''; endDt.disabled = true; }
        else { endDt.disabled = false; }
      });
      if (cb.checked) endDt.disabled = true;

      function getItems() { return Array.from(ul.querySelectorAll('li[data-id]')); }

      function setActive(idx) {
        var items = getItems();
        activeIdx = Math.max(-1, Math.min(idx, items.length - 1));
        items.forEach(function(li, i) {
          li.classList.toggle('is-active', i === activeIdx);
          if (i === activeIdx) li.scrollIntoView({ block: 'nearest' });
        });
        inp.setAttribute('aria-activedescendant', activeIdx >= 0 ? (items[activeIdx].id || '') : '');
      }

      function selectItem(li) {
        hidden.value = li.dataset.id;
        inp.value = li.dataset.label;
        closeDropdown();
      }

      function openDropdown() {
        var r = inp.getBoundingClientRect();
        ul.style.top   = r.bottom + 'px';
        ul.style.left  = r.left + 'px';
        ul.style.width = r.width + 'px';
        ul.style.display = 'block';
        activeIdx = -1;
        inp.setAttribute('aria-expanded', 'true');
        document.addEventListener('click', outsideClick);
        document.addEventListener('scroll', onScroll, true);
      }

      function closeDropdown() {
        ul.style.display = 'none';
        ul.innerHTML = '';
        activeIdx = -1;
        inp.setAttribute('aria-expanded', 'false');
        inp.setAttribute('aria-activedescendant', '');
        document.removeEventListener('click', outsideClick);
        document.removeEventListener('scroll', onScroll, true);
      }

      function outsideClick(e) {
        if (!ul.contains(e.target) && e.target !== inp) closeDropdown();
      }

      function onScroll(e) { if (e.target !== ul) closeDropdown(); }

      ul.addEventListener('htmx:afterSwap', function() {
        ul.querySelectorAll('li[id]').forEach(function(li) {
          li.id = ul.id + '-' + li.id;
        });
        if (ul.children.length) openDropdown(); else closeDropdown();
      });

      inp.addEventListener('keydown', function(e) {
        var items = getItems();
        if (!items.length && e.key !== 'Escape') return;
        if (e.key === 'ArrowDown')       { e.preventDefault(); setActive(activeIdx + 1); }
        else if (e.key === 'ArrowUp')    { e.preventDefault(); setActive(activeIdx - 1); }
        else if (e.key === 'Enter' && activeIdx >= 0) { e.preventDefault(); selectItem(items[activeIdx]); }
        else if (e.key === 'Escape')     { e.preventDefault(); closeDropdown(); }
      });

      ul.addEventListener('click', function(e) {
        var li = e.target.closest('[data-id]');
        if (li) selectItem(li);
      });
    })();
    </script>
  </td>
</tr>
```

- [ ] **Step 2.8: Run tests to confirm passing**

```bash
uv run pytest tests/api/admin/test_people_assignments_inline.py -v -k "new_row or create"
```

Expected: all new-row and create tests pass.

- [ ] **Step 2.9: Commit**

```bash
git add src/api/admin/people_assignments.py \
        src/api/admin/router.py \
        src/templates/admin/people/partials/_assignment_row.html \
        src/templates/admin/people/partials/_assignment_rows.html \
        src/templates/admin/people/partials/_assignment_form_row.html \
        tests/api/admin/test_people_assignments_inline.py
git commit -m "#76 feat: people-assignments new-row and create routes"
```

---

## Task 3: read-row, edit-row routes and `_assignment_edit_row.html`

**Files:**
- Modify: `tests/api/admin/test_people_assignments_inline.py` (append tests)
- Create: `src/templates/admin/people/partials/_assignment_edit_row.html`

(Routes were already added to `people_assignments.py` in Task 2 — all routes written together for cohesion.)

- [ ] **Step 3.1: Append read-row and edit-row tests to test file**

Append to `tests/api/admin/test_people_assignments_inline.py`:

```python
# ---------------------------------------------------------------------------
# Shared fixture — existing assignment
# ---------------------------------------------------------------------------


@pytest.fixture
async def assignment_id(db, person_id, role_id):
    ra_id = generate_id()
    await db.execute(
        """INSERT INTO role_assignments (id, person_id, role_id, is_current, start_date, end_date)
           VALUES ($1, $2, $3, FALSE, '2020-01-01', '2022-12-31')""",
        ra_id, person_id, role_id,
    )
    return ra_id


@pytest.fixture
async def archived_assignment_id(db, person_id, role_id):
    ra_id = generate_id()
    await db.execute(
        """INSERT INTO role_assignments
               (id, person_id, role_id, is_current, start_date, end_date, archived_at)
           VALUES ($1, $2, $3, FALSE, '2018-01-01', '2019-12-31', NOW())""",
        ra_id, person_id, role_id,
    )
    return ra_id


@pytest.fixture
async def current_assignment_id(db, person_id, role_id):
    ra_id = generate_id()
    await db.execute(
        """INSERT INTO role_assignments (id, person_id, role_id, is_current, start_date)
           VALUES ($1, $2, $3, TRUE, '2024-01-01')""",
        ra_id, person_id, role_id,
    )
    return ra_id


# ---------------------------------------------------------------------------
# Read row
# ---------------------------------------------------------------------------


async def test_read_row_returns_org_and_role(client, person_id, assignment_id):
    r = await client.get(
        f"/admin/people/{person_id}/assignments/{assignment_id}/read-row/",
        headers=HTMX_HEADERS,
    )
    assert r.status_code == 200
    assert b"Test Org" in r.content
    assert b"Executive Director" in r.content


async def test_read_row_returns_dates(client, person_id, assignment_id):
    r = await client.get(
        f"/admin/people/{person_id}/assignments/{assignment_id}/read-row/",
        headers=HTMX_HEADERS,
    )
    assert r.status_code == 200
    assert b"2020-01-01" in r.content
    assert b"2022-12-31" in r.content


async def test_read_row_contains_edit_button(client, person_id, assignment_id):
    r = await client.get(
        f"/admin/people/{person_id}/assignments/{assignment_id}/read-row/",
        headers=HTMX_HEADERS,
    )
    assert r.status_code == 200
    assert b"edit-row" in r.content


async def test_read_row_archived_has_no_edit_button(client, person_id, archived_assignment_id):
    r = await client.get(
        f"/admin/people/{person_id}/assignments/{archived_assignment_id}/read-row/",
        headers=HTMX_HEADERS,
    )
    assert r.status_code == 200
    assert b"edit-row" not in r.content


async def test_read_row_unknown_returns_404(client, person_id):
    r = await client.get(
        f"/admin/people/{person_id}/assignments/{generate_id()}/read-row/",
        headers=HTMX_HEADERS,
    )
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# Edit row GET
# ---------------------------------------------------------------------------


async def test_edit_row_get_returns_form(client, person_id, assignment_id):
    r = await client.get(
        f"/admin/people/{person_id}/assignments/{assignment_id}/edit-row/",
        headers=HTMX_HEADERS,
    )
    assert r.status_code == 200
    assert b'name="start_date"' in r.content
    assert b'name="end_date"' in r.content
    assert b'name="is_current"' in r.content


async def test_edit_row_get_uses_individual_cells(client, person_id, assignment_id):
    """Edit row must use 6 individual <td> cells (not colspan) so controls align
    with Org / Role / Start / End / Status / Actions column headers."""
    r = await client.get(
        f"/admin/people/{person_id}/assignments/{assignment_id}/edit-row/",
        headers=HTMX_HEADERS,
    )
    assert r.status_code == 200
    assert b"colspan" not in r.content
    assert r.content.count(b"<td") == 6


async def test_edit_row_get_prepopulates_dates(client, person_id, assignment_id):
    r = await client.get(
        f"/admin/people/{person_id}/assignments/{assignment_id}/edit-row/",
        headers=HTMX_HEADERS,
    )
    assert r.status_code == 200
    assert b"2020-01-01" in r.content
    assert b"2022-12-31" in r.content


async def test_edit_row_get_shows_org_and_role(client, person_id, assignment_id):
    r = await client.get(
        f"/admin/people/{person_id}/assignments/{assignment_id}/edit-row/",
        headers=HTMX_HEADERS,
    )
    assert r.status_code == 200
    assert b"Test Org" in r.content
    assert b"Executive Director" in r.content


async def test_edit_row_get_archived_returns_409(client, person_id, archived_assignment_id):
    r = await client.get(
        f"/admin/people/{person_id}/assignments/{archived_assignment_id}/edit-row/",
        headers=HTMX_HEADERS,
    )
    assert r.status_code == 409


async def test_edit_row_get_unknown_returns_404(client, person_id):
    r = await client.get(
        f"/admin/people/{person_id}/assignments/{generate_id()}/edit-row/",
        headers=HTMX_HEADERS,
    )
    assert r.status_code == 404


async def test_edit_row_get_current_end_date_is_disabled(client, person_id, current_assignment_id):
    r = await client.get(
        f"/admin/people/{person_id}/assignments/{current_assignment_id}/edit-row/",
        headers=HTMX_HEADERS,
    )
    assert r.status_code == 200
    assert b' disabled>' in r.content


async def test_edit_row_get_non_current_end_date_not_disabled(client, person_id, assignment_id):
    r = await client.get(
        f"/admin/people/{person_id}/assignments/{assignment_id}/edit-row/",
        headers=HTMX_HEADERS,
    )
    assert r.status_code == 200
    assert b' disabled>' not in r.content


async def test_edit_row_get_is_current_uses_pill_toggle(client, person_id, assignment_id):
    r = await client.get(
        f"/admin/people/{person_id}/assignments/{assignment_id}/edit-row/",
        headers=HTMX_HEADERS,
    )
    assert r.status_code == 200
    assert b'class="toggle"' in r.content
    assert b"toggle__track" in r.content


# ---------------------------------------------------------------------------
# Edit row POST — success
# ---------------------------------------------------------------------------


async def test_edit_row_post_updates_start_date(client, person_id, assignment_id, db):
    r = await client.post(
        f"/admin/people/{person_id}/assignments/{assignment_id}/edit-row/",
        headers=HTMX_HEADERS,
        data={"start_date": "2021-03-01", "end_date": "2022-12-31"},
    )
    assert r.status_code == 200
    row = await db.fetchrow("SELECT start_date FROM role_assignments WHERE id=$1", assignment_id)
    assert str(row["start_date"]) == "2021-03-01"


async def test_edit_row_post_sets_is_current(client, person_id, assignment_id, db):
    r = await client.post(
        f"/admin/people/{person_id}/assignments/{assignment_id}/edit-row/",
        headers=HTMX_HEADERS,
        data={"start_date": "2020-01-01", "end_date": "", "is_current": "true"},
    )
    assert r.status_code == 200
    row = await db.fetchrow(
        "SELECT is_current, end_date FROM role_assignments WHERE id=$1", assignment_id
    )
    assert row["is_current"] is True
    assert row["end_date"] is None


async def test_edit_row_post_returns_all_rows(client, person_id, assignment_id):
    r = await client.post(
        f"/admin/people/{person_id}/assignments/{assignment_id}/edit-row/",
        headers=HTMX_HEADERS,
        data={"start_date": "2020-01-01", "end_date": "2022-12-31"},
    )
    assert r.status_code == 200
    assert b"Test Org" in r.content
    assert b"Executive Director" in r.content


async def test_edit_row_post_returns_success_flash(client, person_id, assignment_id):
    r = await client.post(
        f"/admin/people/{person_id}/assignments/{assignment_id}/edit-row/",
        headers=HTMX_HEADERS,
        data={"start_date": "2020-01-01", "end_date": "2022-12-31"},
    )
    trigger = json.loads(r.headers["hx-trigger"])
    assert trigger["showFlash"]["level"] == "success"


# ---------------------------------------------------------------------------
# Edit row POST — errors
# ---------------------------------------------------------------------------


async def test_edit_row_post_current_with_end_date_returns_error(
    client, person_id, assignment_id
):
    r = await client.post(
        f"/admin/people/{person_id}/assignments/{assignment_id}/edit-row/",
        headers=HTMX_HEADERS,
        data={"start_date": "2020-01-01", "end_date": "2022-12-31", "is_current": "true"},
    )
    assert r.status_code == 200
    trigger = json.loads(r.headers["hx-trigger"])
    assert trigger["showFlash"]["level"] == "error"
    assert b'name="start_date"' in r.content


async def test_edit_row_post_bad_date_returns_error(client, person_id, assignment_id):
    r = await client.post(
        f"/admin/people/{person_id}/assignments/{assignment_id}/edit-row/",
        headers=HTMX_HEADERS,
        data={"start_date": "not-a-date", "end_date": ""},
    )
    assert r.status_code == 200
    trigger = json.loads(r.headers["hx-trigger"])
    assert trigger["showFlash"]["level"] == "error"
    assert b"not-a-date" in r.content


async def test_edit_row_post_check_violation_preserves_end_date_input(
    client, person_id, assignment_id
):
    r = await client.post(
        f"/admin/people/{person_id}/assignments/{assignment_id}/edit-row/",
        headers=HTMX_HEADERS,
        data={"start_date": "2020-01-01", "end_date": "2023-06-15", "is_current": "true"},
    )
    assert r.status_code == 200
    assert b"2023-06-15" in r.content


async def test_edit_row_post_archived_returns_409(client, person_id, archived_assignment_id):
    r = await client.post(
        f"/admin/people/{person_id}/assignments/{archived_assignment_id}/edit-row/",
        headers=HTMX_HEADERS,
        data={"start_date": "2018-01-01", "end_date": "2019-12-31"},
    )
    assert r.status_code == 409


async def test_edit_row_post_non_htmx_redirects(client, person_id, assignment_id):
    r = await client.post(
        f"/admin/people/{person_id}/assignments/{assignment_id}/edit-row/",
        headers=AUTH_HEADERS,
        data={"start_date": "2020-01-01", "end_date": "2022-12-31"},
        follow_redirects=False,
    )
    assert r.status_code == 303


async def test_edit_row_post_unknown_returns_404(client, person_id):
    r = await client.post(
        f"/admin/people/{person_id}/assignments/{generate_id()}/edit-row/",
        headers=HTMX_HEADERS,
        data={"start_date": "2020-01-01", "end_date": ""},
    )
    assert r.status_code == 404
```

- [ ] **Step 3.2: Run to confirm failure**

```bash
uv run pytest tests/api/admin/test_people_assignments_inline.py -v \
    -k "read_row or edit_row"
```

Expected: read-row and edit-row GET tests fail with `TemplateNotFound` (template missing).

- [ ] **Step 3.3: Create `src/templates/admin/people/partials/_assignment_edit_row.html`**

```html
{# admin/people/partials/_assignment_edit_row.html — inline edit for one assignment #}
<tr id="person-assignment-row-{{ ra.id }}">
  <td style="font-size:var(--font-size-sm);vertical-align:middle">
    <a href="/admin/orgs/{{ ra.org_id }}/">{{ ra.org_name or '(unnamed)' }}</a>
  </td>
  <td style="font-size:var(--font-size-sm);vertical-align:middle">
    <a href="/admin/roles/{{ ra.role_id }}/">{{ ra.role_title or '(untitled)' }}</a>
  </td>
  <td>
    <div class="form-group" style="margin-bottom:0">
      <input type="date" name="start_date" title="Start date"
             id="start-date-input-{{ ra.id }}"
             value="{{ start_date_input }}">
    </div>
  </td>
  <td>
    <div class="form-group" style="margin-bottom:0">
      <input type="date" name="end_date" title="End date"
             id="end-date-input-{{ ra.id }}"
             value="{{ end_date_input }}"
             {% if is_current_input %}disabled{% endif %}>
    </div>
  </td>
  <td style="vertical-align:middle">
    <label class="toggle">
      <input type="checkbox" name="is_current" value="true"
             id="is-current-cb-{{ ra.id }}"
             aria-label="Current"
             {% if is_current_input %}checked{% endif %}>
      <span class="toggle__track"><span class="toggle__thumb"></span></span>
    </label>
  </td>
  <td style="text-align:right;vertical-align:middle;white-space:nowrap">
    <button type="button" class="btn btn--sm btn--primary"
            hx-post="/admin/people/{{ person_id }}/assignments/{{ ra.id }}/edit-row/"
            hx-target="#person-assignments-table tbody"
            hx-swap="innerHTML"
            hx-include="closest tr">Save</button>
    <button type="button" class="btn btn--sm btn--secondary"
            hx-get="/admin/people/{{ person_id }}/assignments/{{ ra.id }}/read-row/"
            hx-target="#person-assignment-row-{{ ra.id }}"
            hx-swap="outerHTML">Cancel</button>
  </td>
</tr>
<script>
(function() {
  var cb    = document.getElementById('is-current-cb-{{ ra.id }}');
  var endDt = document.getElementById('end-date-input-{{ ra.id }}');
  cb.addEventListener('change', function() {
    if (cb.checked) { endDt.value = ''; endDt.disabled = true; }
    else { endDt.disabled = false; }
  });
})();
</script>
```

- [ ] **Step 3.4: Run all assignment tests to confirm passing**

```bash
uv run pytest tests/api/admin/test_people_assignments_inline.py -v
```

Expected: all tests pass.

- [ ] **Step 3.5: Commit**

```bash
git add src/templates/admin/people/partials/_assignment_edit_row.html \
        tests/api/admin/test_people_assignments_inline.py
git commit -m "#76 feat: people-assignments read-row and edit-row routes"
```

---

## Task 4: Wire person detail page

Update the person detail query and template to use the new partials.

**Files:**
- Modify: `src/api/admin/people.py` (person detail query — add `r.id AS role_id`)
- Modify: `src/templates/admin/people/detail.html` (table id, 6th column, Add button, use `{% include %}`)

- [ ] **Step 4.1: Write failing test**

Add to `tests/api/admin/test_people_assignments_inline.py`:

```python
# ---------------------------------------------------------------------------
# Person detail page integration
# ---------------------------------------------------------------------------


async def test_person_detail_shows_role_assignments_table(client, person_id, assignment_id):
    """Person detail page must render the role assignments table with HTMX controls."""
    r = await client.get(f"/admin/people/{person_id}/", headers=HTMX_HEADERS)
    assert r.status_code == 200
    assert b"person-assignments-table" in r.content


async def test_person_detail_shows_add_assignment_button(client, person_id):
    r = await client.get(f"/admin/people/{person_id}/", headers=HTMX_HEADERS)
    assert r.status_code == 200
    assert b"Add assignment" in r.content


async def test_person_detail_role_title_links_to_role_detail(client, person_id, assignment_id, role_id):
    r = await client.get(f"/admin/people/{person_id}/", headers=HTMX_HEADERS)
    assert r.status_code == 200
    assert f"/admin/roles/{role_id}/".encode() in r.content


async def test_person_detail_hides_add_button_when_archived(client, db):
    pid = generate_id()
    await db.execute(
        "INSERT INTO people (id, archived_at) VALUES ($1, NOW())", pid
    )
    await db.execute(
        "INSERT INTO person_names (id, person_id, name, is_canonical) VALUES ($1, $2, $3, TRUE)",
        generate_id(), pid, "Archived Person",
    )
    r = await client.get(f"/admin/people/{pid}/", headers=HTMX_HEADERS)
    assert r.status_code == 200
    assert b"Add assignment" not in r.content
```

- [ ] **Step 4.2: Run to confirm failure**

```bash
uv run pytest tests/api/admin/test_people_assignments_inline.py -v \
    -k "person_detail"
```

Expected: `person_detail_shows_role_assignments_table` fails (table id not in HTML yet).

- [ ] **Step 4.3: Update person detail query in `people.py`**

Find the `role_assignments` fetch in `person_detail` (around line 301) and add `r.id AS role_id`:

```python
    role_assignments = await db.fetch(
        """SELECT ra.id, ra.is_current, ra.start_date, ra.end_date, ra.archived_at,
                  r.id AS role_id, r.title AS role_title,
                  o.id AS org_id, dn.display_name AS org_name
           FROM role_assignments ra
           JOIN roles r ON r.id = ra.role_id
           JOIN organizations o ON o.id = r.organization_id
           LEFT JOIN v_org_display_names dn ON dn.organization_id = o.id
           WHERE ra.person_id = $1
           ORDER BY ra.is_current DESC, ra.start_date DESC NULLS LAST""",
        person_id,
    )
```

- [ ] **Step 4.4: Update `people/detail.html` Role Assignments section**

Replace the existing Role Assignments `<section>` (lines 195–227) with:

```html
<section class="entity-section">
  <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:var(--space-3)">
    <h2>Role Assignments</h2>
    {% if not person.archived_at %}
    <button class="btn btn--sm btn--secondary"
            hx-get="/admin/people/{{ person.id }}/assignments/new-row/"
            hx-target="#person-assignments-table tbody"
            hx-swap="afterbegin"
            type="button">+ Add assignment</button>
    {% endif %}
  </div>
  <div class="table-wrapper">
    <table id="person-assignments-table" class="data-table">
      <thead>
        <tr>
          <th scope="col">Organization</th>
          <th scope="col">Role</th>
          <th scope="col">Start</th>
          <th scope="col">End</th>
          <th scope="col">Status</th>
          <th scope="col"></th>
        </tr>
      </thead>
      <tbody>
        {% for ra in role_assignments %}
        {% include "admin/people/partials/_assignment_row.html" %}
        {% else %}
        <tr><td colspan="6" style="text-align:center;color:var(--color-text-muted)">No role assignments</td></tr>
        {% endfor %}
      </tbody>
    </table>
  </div>
</section>
```

Note: The `{% include %}` needs `person_id` in scope. The template already has `person` available, so add `{% set person_id = person.id %}` before the loop:

```html
      <tbody>
        {% set person_id = person.id %}
        {% for ra in role_assignments %}
        {% include "admin/people/partials/_assignment_row.html" %}
        {% else %}
        <tr><td colspan="6" style="text-align:center;color:var(--color-text-muted)">No role assignments</td></tr>
        {% endfor %}
      </tbody>
```

- [ ] **Step 4.5: Run all tests**

```bash
uv run pytest tests/api/admin/test_people_assignments_inline.py -v
uv run pytest --no-cov -q
```

Expected: all people_assignments tests pass; full suite passes (277+ tests, 0 failures).

- [ ] **Step 4.6: Commit**

```bash
git add src/api/admin/people.py \
        src/templates/admin/people/detail.html \
        tests/api/admin/test_people_assignments_inline.py
git commit -m "#76 feat: wire person detail role assignments table with inline CRUD"
```
