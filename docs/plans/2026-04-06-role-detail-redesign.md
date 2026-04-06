# Role Detail Redesign — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Modernize the role detail page with inline editing for org/title/notes, add "+ Add assignment" with person typeahead, rename section to "Assignments".

**Architecture:** New `roles_detail.py` sub-router handles all inline editing routes under `/roles/{role_id}/`. People search endpoint added to `people.py`. Templates follow existing org detail partial patterns (read/edit swap via HTMX). Assignments use the tbody-replacement pattern after create.

**Tech Stack:** FastAPI, HTMX, Jinja2, asyncpg, native HTML5 `type="date"` inputs.

**Design doc:** `docs/plans/2026-04-06-role-detail-redesign-design.md`

---

## File Structure

### New files

| File | Responsibility |
|------|---------------|
| `src/api/admin/roles_detail.py` | Inline editing routes: org, title, notes, assignments |
| `src/templates/admin/roles/partials/_org_read.html` | Org field read partial |
| `src/templates/admin/roles/partials/_org_form.html` | Org field typeahead form |
| `src/templates/admin/roles/partials/_title_read.html` | Title field read partial |
| `src/templates/admin/roles/partials/_title_form.html` | Title field text input form |
| `src/templates/admin/roles/partials/_notes_read.html` | Notes field read partial |
| `src/templates/admin/roles/partials/_notes_form.html` | Notes field form |
| `src/templates/admin/roles/partials/_assignment_row.html` | Single assignment read row |
| `src/templates/admin/roles/partials/_assignment_rows.html` | Full tbody for assignments table |
| `src/templates/admin/roles/partials/_assignment_form_row.html` | New assignment form row with person typeahead |
| `src/templates/admin/people/partials/_search_results.html` | Person typeahead result items |
| `tests/api/admin/test_roles_detail_inline.py` | Integration tests for inline editing |
| `tests/api/admin/test_people_search.py` | Tests for person search endpoint |

### Modified files

| File | Change |
|------|--------|
| `src/api/admin/router.py` | Mount `roles_detail` sub-router |
| `src/api/admin/people.py` | Add `GET /people/search/` endpoint |
| `src/templates/admin/roles/detail.html` | Complete rewrite to inline editing layout |

---

## Task 1: People Search Endpoint

Add a typeahead search endpoint for people — needed by the assignment form.

**Files:**
- Modify: `src/api/admin/people.py` (add search route)
- Create: `src/templates/admin/people/partials/_search_results.html`
- Create: `tests/api/admin/test_people_search.py`

- [ ] **Step 1: Write the failing test**

Create `tests/api/admin/test_people_search.py`:

```python
"""Integration tests for people search typeahead."""

import os

import asyncpg
import pytest
from httpx import ASGITransport, AsyncClient

from src.api.admin.deps import get_db
from src.api.main import app
from src.core.db import apply_schema, generate_id

pytestmark = pytest.mark.integration

AUTH_HEADERS = {
    "X-ExeDev-UserID": "test-user",
    "X-ExeDev-Email": "test@example.com",
}
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
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c
    app.dependency_overrides.pop(get_db, None)


async def _make_person(db, first: str, last: str) -> str:
    pid = generate_id()
    await db.execute("INSERT INTO people (id) VALUES ($1)", pid)
    await db.execute(
        "INSERT INTO person_names (id, person_id, first_name, last_name, is_canonical)"
        " VALUES ($1, $2, $3, $4, TRUE)",
        generate_id(), pid, first, last,
    )
    return pid


async def test_search_returns_matching_person(client, db):
    await _make_person(db, "Jane", "Doe")
    r = await client.get(
        "/admin/people/search/", params={"q": "Jane"}, headers=HTMX_HEADERS
    )
    assert r.status_code == 200
    assert b"Jane" in r.content
    assert b"data-id" in r.content


async def test_search_empty_query_returns_empty(client, db):
    await _make_person(db, "Alice", "Smith")
    r = await client.get(
        "/admin/people/search/", params={"q": ""}, headers=HTMX_HEADERS
    )
    assert r.status_code == 200
    assert b"data-id" not in r.content


async def test_search_no_match_returns_empty(client, db):
    r = await client.get(
        "/admin/people/search/", params={"q": "zzzznotfound"}, headers=HTMX_HEADERS
    )
    assert r.status_code == 200
    assert b"data-id" not in r.content
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/api/admin/test_people_search.py -v`
Expected: FAIL (404 — route does not exist yet)

- [ ] **Step 3: Create the search results template**

Create `src/templates/admin/people/partials/_search_results.html`:

```html
{# admin/people/partials/_search_results.html #}
{% for r in results %}
<li id="opt-{{ r.id }}" role="option"
    data-id="{{ r.id }}"
    data-label="{{ r.display_name }}">{{ r.display_name }}</li>
{% endfor %}
```

- [ ] **Step 4: Add the search route to people.py**

Add to `src/api/admin/people.py`, near the top (after imports, before existing routes). The route must be before `/{person_id}/` to avoid path conflicts:

```python
@router.get("/search/")
async def people_search(
    request: Request,
    q: str = "",
    user: AdminUser | RedirectResponse = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Typeahead search — returns HTML fragment of matching people."""
    redirect, user = check_auth(user)
    if redirect:
        return redirect
    results = []
    if q.strip():
        results = await db.fetch(
            """SELECT p.id, pn.display_name
               FROM people p
               LEFT JOIN v_person_display_names pn ON pn.person_id = p.id
               WHERE p.archived_at IS NULL
                 AND pn.display_name ILIKE $1
               ORDER BY pn.display_name NULLS LAST
               LIMIT 20""",
            f"%{q.strip()}%",
        )
    return templates.TemplateResponse(
        request,
        "admin/people/partials/_search_results.html",
        {"results": results},
    )
```

Ensure the required imports are present in `people.py`: `check_auth`, `is_htmx`, `flash_trigger` from deps. Check if `templates` is already defined at module level.

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/api/admin/test_people_search.py -v`
Expected: 3 passed

- [ ] **Step 6: Commit**

```bash
git add tests/api/admin/test_people_search.py \
        src/api/admin/people.py \
        src/templates/admin/people/partials/_search_results.html
git commit -m "#67 feat: add people search typeahead endpoint"
```

---

## Task 2: Role Detail Page Template — Inline Layout

Rewrite the detail template to use inline editing partials. Create all read partials. No new routes yet — just template restructure.

**Files:**
- Create: `src/templates/admin/roles/partials/_org_read.html`
- Create: `src/templates/admin/roles/partials/_title_read.html`
- Create: `src/templates/admin/roles/partials/_notes_read.html`
- Create: `src/templates/admin/roles/partials/_assignment_row.html`
- Create: `src/templates/admin/roles/partials/_assignment_rows.html`
- Modify: `src/templates/admin/roles/detail.html`

- [ ] **Step 1: Create org read partial**

Create `src/templates/admin/roles/partials/_org_read.html`:

```html
{# admin/roles/partials/_org_read.html — org field read partial #}
<div id="org-field">
  <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:var(--space-3)">
    <h3 class="field-group-label">Organization</h3>
    {% if not role.archived_at %}
    <button type="button" class="btn btn--sm btn--secondary"
            hx-get="/admin/roles/{{ role.id }}/inline/org/edit/"
            hx-target="#org-field"
            hx-swap="outerHTML">Change</button>
    {% endif %}
  </div>
  <div style="border:1px solid var(--color-border);border-radius:var(--radius-md);padding:var(--space-3) var(--space-4);background:var(--color-surface-1);font-size:var(--font-size-sm)">
    {% if role.org_id %}
    <a href="/admin/orgs/{{ role.org_id }}/">{{ role.org_name or '(unnamed)' }}</a>
    {% else %}<span style="color:var(--color-text-muted)">—</span>{% endif %}
  </div>
</div>
```

- [ ] **Step 2: Create title read partial**

Create `src/templates/admin/roles/partials/_title_read.html`:

```html
{# admin/roles/partials/_title_read.html — title field read partial #}
<div id="title-field">
  <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:var(--space-3)">
    <h3 class="field-group-label">Title</h3>
    {% if not role.archived_at %}
    <button type="button" class="btn btn--sm btn--secondary"
            hx-get="/admin/roles/{{ role.id }}/inline/title/edit/"
            hx-target="#title-field"
            hx-swap="outerHTML">Edit</button>
    {% endif %}
  </div>
  <div style="border:1px solid var(--color-border);border-radius:var(--radius-md);padding:var(--space-3) var(--space-4);background:var(--color-surface-1);font-size:var(--font-size-sm);color:{% if role.title %}var(--color-text){% else %}var(--color-text-muted){% endif %}">
    {{ role.title or '—' }}
  </div>
</div>
```

- [ ] **Step 3: Create notes read partial**

Create `src/templates/admin/roles/partials/_notes_read.html`:

```html
{# admin/roles/partials/_notes_read.html — notes read partial #}
<div id="notes-field" style="margin-top:var(--space-5)">
  <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:var(--space-3)">
    <h3 class="field-group-label">Notes</h3>
    {% if not role.archived_at %}
    <button type="button" class="btn btn--sm btn--secondary"
            hx-get="/admin/roles/{{ role.id }}/inline/notes/edit/"
            hx-target="#notes-field"
            hx-swap="outerHTML">Edit</button>
    {% endif %}
  </div>
  <div style="border:1px solid var(--color-border);border-radius:var(--radius-md);padding:var(--space-3) var(--space-4);background:var(--color-surface-1);font-size:var(--font-size-sm);min-height:3rem;color:{% if role.notes %}var(--color-text){% else %}var(--color-text-muted){% endif %}">
    {{ role.notes or '—' }}
  </div>
</div>
```

- [ ] **Step 4: Create assignment row partial**

Create `src/templates/admin/roles/partials/_assignment_row.html`:

```html
{# admin/roles/partials/_assignment_row.html — single assignment read row #}
<tr>
  <td><a href="/admin/people/{{ ra.person_id }}/">{{ ra.person_name or '(unnamed)' }}</a></td>
  <td>{{ ra.start_date or '—' }}</td>
  <td>{{ ra.end_date or '—' }}</td>
  <td>
    {% if ra.archived_at %}<span class="badge badge--archived">Archived</span>
    {% elif ra.is_current %}<span class="badge badge--active">Current</span>
    {% else %}<span class="badge badge--inactive">Former</span>{% endif %}
  </td>
</tr>
```

- [ ] **Step 5: Create assignment rows (tbody) partial**

Create `src/templates/admin/roles/partials/_assignment_rows.html`:

```html
{# admin/roles/partials/_assignment_rows.html — full tbody for assignments table #}
{% for ra in assignments %}
{% include "admin/roles/partials/_assignment_row.html" %}
{% else %}
<tr><td colspan="4" style="text-align:center;color:var(--color-text-muted)">No assignments</td></tr>
{% endfor %}
```

- [ ] **Step 6: Rewrite detail.html**

Replace `src/templates/admin/roles/detail.html` with:

```html
{% extends "admin/base.html" %}
{% block title %}{{ role.title or role.id }} — Role{% endblock %}
{% block breadcrumb %}
  <a href="/admin/">Dashboard</a><span class="breadcrumb__sep">›</span>
  <a href="/admin/roles/">Roles</a><span class="breadcrumb__sep">›</span>
  <span>{{ role.title or role.id }}</span>
{% endblock %}
{% block content %}
<div class="page-header">
  <div>
    <span class="page-header__type">Role</span>
    <h1>{{ role.title or '(untitled)' }}</h1>
  </div>
</div>

<section class="entity-section">
  <h2>Details</h2>
  <div class="entity-card">

    <div style="margin-bottom:var(--space-4)">
      <span class="field-group-label">Status</span>
      <span style="margin-left:var(--space-2)">
        {% if role.archived_at %}<span class="badge badge--archived">Archived</span>
        {% else %}<span class="badge badge--active">Active</span>{% endif %}
      </span>
    </div>

    {% include "admin/roles/partials/_org_read.html" %}

    <div style="margin-top:var(--space-5)">
      {% include "admin/roles/partials/_title_read.html" %}
    </div>

    {% include "admin/roles/partials/_notes_read.html" %}

  </div>
</section>

<section class="entity-section">
  <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:var(--space-3)">
    <h2 style="margin:0">Assignments</h2>
    {% if not role.archived_at %}
    <button class="btn btn--sm btn--secondary"
            hx-get="/admin/roles/{{ role.id }}/assignments/new-row/"
            hx-target="#assignments-table tbody"
            hx-swap="afterbegin"
            type="button">+ Add assignment</button>
    {% endif %}
  </div>
  <div class="table-wrapper">
    <table id="assignments-table" class="data-table">
      <thead>
        <tr>
          <th scope="col">Person</th>
          <th scope="col">Start</th>
          <th scope="col">End</th>
          <th scope="col">Status</th>
        </tr>
      </thead>
      <tbody>
        {% include "admin/roles/partials/_assignment_rows.html" %}
      </tbody>
    </table>
  </div>
</section>

<p style="color:var(--color-text-muted);font-size:var(--font-sm);margin-top:var(--space-6)">
  Metadata &middot; ID: <code>{{ role.id }}</code>
  &middot; Created: {{ role.created_at.strftime('%Y-%m-%d') }}
  &middot; Updated: {{ role.updated_at.strftime('%Y-%m-%d') }}
</p>

<div class="danger-zone">
  <h2>Danger Zone</h2>
  {% if not role.archived_at %}
  <p>Archiving hides this role from active views but preserves all data.</p>
  <form method="POST" action="/admin/roles/{{ role.id }}/archive/">
    <button type="submit" class="btn btn--danger">Archive role</button>
  </form>
  {% else %}
  <p>This role is archived. You may permanently delete it — this cannot be undone.</p>
  <button class="btn btn--danger"
          hx-delete="/admin/roles/{{ role.id }}/"
          hx-confirm="Permanently delete this role? This cannot be undone."
          hx-target="body"
          hx-push-url="/admin/roles/">
    Delete permanently
  </button>
  {% endif %}
</div>
{% endblock %}
```

- [ ] **Step 7: Verify page loads**

Run dev server and verify the role detail page loads at `https://power-map.exe.xyz:8001/admin/roles/01KM234ZNEF4366FT0ZCTPXN9Z/`. The inline editing buttons won't work yet (routes don't exist), but the page should render without errors. Check the dev server log for template errors:

```bash
tail -20 /tmp/power-map-dev.log
```

- [ ] **Step 8: Commit**

```bash
git add src/templates/admin/roles/detail.html \
        src/templates/admin/roles/partials/
git commit -m "#67 feat: rewrite role detail template with inline editing layout"
```

---

## Task 3: Inline Editing Routes — Org, Title, Notes

Create the sub-router with all inline editing routes and their form partials.

**Files:**
- Create: `src/api/admin/roles_detail.py`
- Create: `src/templates/admin/roles/partials/_org_form.html`
- Create: `src/templates/admin/roles/partials/_title_form.html`
- Create: `src/templates/admin/roles/partials/_notes_form.html`
- Modify: `src/api/admin/router.py` (mount new sub-router)
- Create: `tests/api/admin/test_roles_detail_inline.py`

- [ ] **Step 1: Write failing tests**

Create `tests/api/admin/test_roles_detail_inline.py`:

```python
"""Integration tests for role detail inline editing routes."""

import json
import os

import asyncpg
import pytest
from httpx import ASGITransport, AsyncClient

from src.api.admin.deps import get_db
from src.api.main import app
from src.core.db import apply_schema, generate_id

pytestmark = pytest.mark.integration

AUTH_HEADERS = {
    "X-ExeDev-UserID": "test-user",
    "X-ExeDev-Email": "test@example.com",
}
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
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c
    app.dependency_overrides.pop(get_db, None)


async def _make_org(db, name: str) -> str:
    oid = generate_id()
    await db.execute("INSERT INTO organizations (id) VALUES ($1)", oid)
    await db.execute(
        "INSERT INTO organization_names (id, organization_id, name, is_canonical)"
        " VALUES ($1, $2, $3, TRUE)",
        generate_id(), oid, name,
    )
    return oid


@pytest.fixture
async def role_id(db):
    oid = await _make_org(db, "Test Org")
    rid = generate_id()
    await db.execute(
        "INSERT INTO roles (id, organization_id, title, notes)"
        " VALUES ($1, $2, $3, $4)",
        rid, oid, "Executive Director", "Some notes",
    )
    return rid


# ---------------------------------------------------------------------------
# Org inline
# ---------------------------------------------------------------------------


async def test_org_read_returns_partial(client, role_id):
    r = await client.get(
        f"/admin/roles/{role_id}/inline/org/", headers=HTMX_HEADERS
    )
    assert r.status_code == 200
    assert b"org-field" in r.content


async def test_org_edit_returns_form(client, role_id):
    r = await client.get(
        f"/admin/roles/{role_id}/inline/org/edit/", headers=HTMX_HEADERS
    )
    assert r.status_code == 200
    assert b"org-search" in r.content


async def test_org_post_updates_org(client, role_id, db):
    new_org = await _make_org(db, "New Org")
    r = await client.post(
        f"/admin/roles/{role_id}/inline/org/",
        data={"organization_id": new_org},
        headers=HTMX_HEADERS,
    )
    assert r.status_code == 200
    row = await db.fetchrow("SELECT organization_id FROM roles WHERE id=$1", role_id)
    assert row["organization_id"] == new_org


async def test_org_post_returns_flash(client, role_id, db):
    new_org = await _make_org(db, "Flash Org")
    r = await client.post(
        f"/admin/roles/{role_id}/inline/org/",
        data={"organization_id": new_org},
        headers=HTMX_HEADERS,
    )
    trigger = json.loads(r.headers["hx-trigger"])
    assert trigger["showFlash"]["level"] == "success"


async def test_org_post_empty_returns_error(client, role_id):
    r = await client.post(
        f"/admin/roles/{role_id}/inline/org/",
        data={"organization_id": ""},
        headers=HTMX_HEADERS,
    )
    assert r.status_code == 200
    trigger = json.loads(r.headers["hx-trigger"])
    assert trigger["showFlash"]["level"] == "error"


# ---------------------------------------------------------------------------
# Title inline
# ---------------------------------------------------------------------------


async def test_title_read_returns_partial(client, role_id):
    r = await client.get(
        f"/admin/roles/{role_id}/inline/title/", headers=HTMX_HEADERS
    )
    assert r.status_code == 200
    assert b"title-field" in r.content


async def test_title_edit_returns_form(client, role_id):
    r = await client.get(
        f"/admin/roles/{role_id}/inline/title/edit/", headers=HTMX_HEADERS
    )
    assert r.status_code == 200
    assert b'name="title"' in r.content


async def test_title_post_updates_title(client, role_id, db):
    r = await client.post(
        f"/admin/roles/{role_id}/inline/title/",
        data={"title": "Chief of Staff"},
        headers=HTMX_HEADERS,
    )
    assert r.status_code == 200
    row = await db.fetchrow("SELECT title FROM roles WHERE id=$1", role_id)
    assert row["title"] == "Chief of Staff"


async def test_title_post_returns_flash(client, role_id):
    r = await client.post(
        f"/admin/roles/{role_id}/inline/title/",
        data={"title": "New Title"},
        headers=HTMX_HEADERS,
    )
    trigger = json.loads(r.headers["hx-trigger"])
    assert trigger["showFlash"]["level"] == "success"


async def test_title_post_empty_returns_error(client, role_id):
    r = await client.post(
        f"/admin/roles/{role_id}/inline/title/",
        data={"title": "   "},
        headers=HTMX_HEADERS,
    )
    assert r.status_code == 200
    trigger = json.loads(r.headers["hx-trigger"])
    assert trigger["showFlash"]["level"] == "error"


# ---------------------------------------------------------------------------
# Notes inline
# ---------------------------------------------------------------------------


async def test_notes_read_returns_partial(client, role_id):
    r = await client.get(
        f"/admin/roles/{role_id}/inline/notes/", headers=HTMX_HEADERS
    )
    assert r.status_code == 200
    assert b"notes-field" in r.content


async def test_notes_edit_returns_form(client, role_id):
    r = await client.get(
        f"/admin/roles/{role_id}/inline/notes/edit/", headers=HTMX_HEADERS
    )
    assert r.status_code == 200
    assert b"notes-textarea" in r.content


async def test_notes_post_saves_value(client, role_id, db):
    r = await client.post(
        f"/admin/roles/{role_id}/inline/notes/",
        data={"notes": "Updated notes"},
        headers=HTMX_HEADERS,
    )
    assert r.status_code == 200
    row = await db.fetchrow("SELECT notes FROM roles WHERE id=$1", role_id)
    assert row["notes"] == "Updated notes"


async def test_notes_post_whitespace_to_null(client, role_id, db):
    r = await client.post(
        f"/admin/roles/{role_id}/inline/notes/",
        data={"notes": "   "},
        headers=HTMX_HEADERS,
    )
    assert r.status_code == 200
    row = await db.fetchrow("SELECT notes FROM roles WHERE id=$1", role_id)
    assert row["notes"] is None


async def test_notes_post_returns_read_partial(client, role_id):
    r = await client.post(
        f"/admin/roles/{role_id}/inline/notes/",
        data={"notes": "hello"},
        headers=HTMX_HEADERS,
    )
    assert r.status_code == 200
    assert b"notes-field" in r.content
    assert b"notes-textarea" not in r.content


async def test_notes_post_returns_flash(client, role_id):
    r = await client.post(
        f"/admin/roles/{role_id}/inline/notes/",
        data={"notes": "test"},
        headers=HTMX_HEADERS,
    )
    trigger = json.loads(r.headers["hx-trigger"])
    assert trigger["showFlash"]["level"] == "success"


# ---------------------------------------------------------------------------
# 404 handling
# ---------------------------------------------------------------------------


async def test_inline_routes_return_404_for_missing_role(client):
    fake_id = generate_id()
    for path in [
        f"/admin/roles/{fake_id}/inline/org/",
        f"/admin/roles/{fake_id}/inline/title/",
        f"/admin/roles/{fake_id}/inline/notes/",
    ]:
        r = await client.get(path, headers=HTMX_HEADERS)
        assert r.status_code == 404, f"Expected 404 for {path}"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/api/admin/test_roles_detail_inline.py -v`
Expected: FAIL (404 — routes do not exist)

- [ ] **Step 3: Create form partials**

Create `src/templates/admin/roles/partials/_org_form.html`:

```html
{# admin/roles/partials/_org_form.html — org field typeahead form #}
<div id="org-field">
  <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:var(--space-3)">
    <h3 class="field-group-label">Organization</h3>
    <div>
      <button type="button" class="btn btn--primary btn--sm"
              hx-post="/admin/roles/{{ role.id }}/inline/org/"
              hx-target="#org-field"
              hx-swap="outerHTML"
              hx-include="#org-id-hidden">Save</button>
      <button type="button" class="btn btn--secondary btn--sm"
              hx-get="/admin/roles/{{ role.id }}/inline/org/"
              hx-target="#org-field"
              hx-swap="outerHTML">Cancel</button>
    </div>
  </div>
  <div class="form-group" style="margin-bottom:0;position:relative">
    <input id="org-search" type="text" autocomplete="off"
           placeholder="Type to search…"
           value="{{ role.org_name or '' }}"
           hx-get="/admin/orgs/search/"
           hx-trigger="input changed delay:200ms"
           hx-target="#org-search-results"
           hx-swap="innerHTML"
           hx-params="q"
           name="q"
           role="combobox"
           aria-expanded="false"
           aria-haspopup="listbox"
           aria-controls="org-search-results"
           aria-autocomplete="list">
    <input type="hidden" name="organization_id" id="org-id-hidden"
           value="{{ role.org_id or '' }}">
    <ul id="org-search-results" class="typeahead-results" role="listbox"></ul>
  </div>
  <script>
  (function() {
    var inp    = document.getElementById('org-search');
    var ul     = document.getElementById('org-search-results');
    var hidden = document.getElementById('org-id-hidden');
    var activeIdx = -1;

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
</div>
```

Create `src/templates/admin/roles/partials/_title_form.html`:

```html
{# admin/roles/partials/_title_form.html — title inline edit form #}
<div id="title-field">
  <form hx-post="/admin/roles/{{ role.id }}/inline/title/"
        hx-target="#title-field"
        hx-swap="outerHTML">
    <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:var(--space-3)">
      <label for="title-input" class="field-group-label">Title</label>
      <div>
        <button type="submit" class="btn btn--primary btn--sm">Save</button>
        <button type="button" class="btn btn--secondary btn--sm"
                hx-get="/admin/roles/{{ role.id }}/inline/title/"
                hx-target="#title-field"
                hx-swap="outerHTML">Cancel</button>
      </div>
    </div>
    <div class="form-group" style="margin-bottom:0">
      <input id="title-input" type="text" name="title" value="{{ role.title or '' }}" required>
    </div>
  </form>
</div>
```

Create `src/templates/admin/roles/partials/_notes_form.html`:

```html
{# admin/roles/partials/_notes_form.html — notes inline edit form #}
<div id="notes-field" style="margin-top:var(--space-5)">
  <form hx-post="/admin/roles/{{ role.id }}/inline/notes/"
        hx-target="#notes-field"
        hx-swap="outerHTML">
    <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:var(--space-3)">
      <label for="notes-textarea" class="field-group-label">Notes</label>
      <div>
        <button type="submit" class="btn btn--primary btn--sm">Save</button>
        <button type="button" class="btn btn--secondary btn--sm"
                hx-get="/admin/roles/{{ role.id }}/inline/notes/"
                hx-target="#notes-field"
                hx-swap="outerHTML">Cancel</button>
      </div>
    </div>
    <div class="form-group" style="margin-bottom:0">
      <textarea id="notes-textarea" name="notes">{{ role.notes or '' }}</textarea>
    </div>
  </form>
</div>
```

- [ ] **Step 4: Create roles_detail.py sub-router**

Create `src/api/admin/roles_detail.py`:

```python
"""Inline editing routes for the role detail page."""

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from markupsafe import escape

from src.api.admin.deps import AdminUser, check_auth, flash_trigger, get_admin_user, get_db, is_htmx

templates = Jinja2Templates(directory="src/templates")
router = APIRouter(prefix="/roles/{role_id}", tags=["admin-roles-detail"])


async def _get_role(role_id: str, db):
    """Fetch role with org display name, or raise 404."""
    row = await db.fetchrow(
        """SELECT r.id, r.title, r.notes, r.archived_at, r.created_at, r.updated_at,
                  r.organization_id AS org_id,
                  dn.display_name AS org_name
           FROM roles r
           LEFT JOIN v_org_display_names dn ON dn.organization_id = r.organization_id
           WHERE r.id = $1""",
        role_id,
    )
    if not row:
        raise HTTPException(status_code=404, detail="Role not found")
    return row


# ---------------------------------------------------------------------------
# Organization inline
# ---------------------------------------------------------------------------


@router.get("/inline/org/")
async def role_inline_org_get(
    role_id: str,
    request: Request,
    user: AdminUser | RedirectResponse = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Return org read partial."""
    redirect, user = check_auth(user)
    if redirect:
        return redirect
    role = await _get_role(role_id, db)
    return templates.TemplateResponse(
        request, "admin/roles/partials/_org_read.html", {"role": role}
    )


@router.get("/inline/org/edit/")
async def role_inline_org_edit_get(
    role_id: str,
    request: Request,
    user: AdminUser | RedirectResponse = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Return org typeahead form partial."""
    redirect, user = check_auth(user)
    if redirect:
        return redirect
    role = await _get_role(role_id, db)
    return templates.TemplateResponse(
        request, "admin/roles/partials/_org_form.html", {"role": role}
    )


@router.post("/inline/org/")
async def role_inline_org_post(
    role_id: str,
    request: Request,
    organization_id: str = Form(""),
    user: AdminUser | RedirectResponse = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Save org change; return updated read partial."""
    redirect, user = check_auth(user)
    if redirect:
        return redirect
    role = await _get_role(role_id, db)
    resolved = organization_id.strip()
    if not resolved:
        if not is_htmx(request):
            return RedirectResponse(f"/admin/roles/{role_id}/", status_code=303)
        return templates.TemplateResponse(
            request,
            "admin/roles/partials/_org_read.html",
            {"role": role},
            headers=flash_trigger("error", "Organization is required."),
        )
    exists = await db.fetchval("SELECT id FROM organizations WHERE id=$1", resolved)
    if not exists:
        if not is_htmx(request):
            return RedirectResponse(f"/admin/roles/{role_id}/", status_code=303)
        return templates.TemplateResponse(
            request,
            "admin/roles/partials/_org_read.html",
            {"role": role},
            headers=flash_trigger("error", "Organization not found."),
        )
    await db.execute(
        "UPDATE roles SET organization_id=$1 WHERE id=$2", resolved, role_id
    )
    role = await _get_role(role_id, db)
    if not is_htmx(request):
        return RedirectResponse(f"/admin/roles/{role_id}/", status_code=303)
    return templates.TemplateResponse(
        request,
        "admin/roles/partials/_org_read.html",
        {"role": role},
        headers=flash_trigger(
            "success",
            f"Organization set to <strong>{escape(role['org_name'])}</strong>.",
        ),
    )


# ---------------------------------------------------------------------------
# Title inline
# ---------------------------------------------------------------------------


@router.get("/inline/title/")
async def role_inline_title_get(
    role_id: str,
    request: Request,
    user: AdminUser | RedirectResponse = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Return title read partial."""
    redirect, user = check_auth(user)
    if redirect:
        return redirect
    role = await _get_role(role_id, db)
    return templates.TemplateResponse(
        request, "admin/roles/partials/_title_read.html", {"role": role}
    )


@router.get("/inline/title/edit/")
async def role_inline_title_edit_get(
    role_id: str,
    request: Request,
    user: AdminUser | RedirectResponse = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Return title edit form partial."""
    redirect, user = check_auth(user)
    if redirect:
        return redirect
    role = await _get_role(role_id, db)
    return templates.TemplateResponse(
        request, "admin/roles/partials/_title_form.html", {"role": role}
    )


@router.post("/inline/title/")
async def role_inline_title_post(
    role_id: str,
    request: Request,
    title: str = Form(""),
    user: AdminUser | RedirectResponse = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Save title; return updated read partial."""
    redirect, user = check_auth(user)
    if redirect:
        return redirect
    role = await _get_role(role_id, db)
    cleaned = title.strip()
    if not cleaned:
        if not is_htmx(request):
            return RedirectResponse(f"/admin/roles/{role_id}/", status_code=303)
        return templates.TemplateResponse(
            request,
            "admin/roles/partials/_title_read.html",
            {"role": role},
            headers=flash_trigger("error", "Title cannot be empty."),
        )
    await db.execute("UPDATE roles SET title=$1 WHERE id=$2", cleaned, role_id)
    role = await _get_role(role_id, db)
    if not is_htmx(request):
        return RedirectResponse(f"/admin/roles/{role_id}/", status_code=303)
    return templates.TemplateResponse(
        request,
        "admin/roles/partials/_title_read.html",
        {"role": role},
        headers=flash_trigger("success", "Title saved."),
    )


# ---------------------------------------------------------------------------
# Notes inline
# ---------------------------------------------------------------------------


@router.get("/inline/notes/")
async def role_inline_notes_get(
    role_id: str,
    request: Request,
    user: AdminUser | RedirectResponse = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Return notes read partial."""
    redirect, user = check_auth(user)
    if redirect:
        return redirect
    role = await _get_role(role_id, db)
    return templates.TemplateResponse(
        request, "admin/roles/partials/_notes_read.html", {"role": role}
    )


@router.get("/inline/notes/edit/")
async def role_inline_notes_edit_get(
    role_id: str,
    request: Request,
    user: AdminUser | RedirectResponse = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Return notes edit form partial."""
    redirect, user = check_auth(user)
    if redirect:
        return redirect
    role = await _get_role(role_id, db)
    return templates.TemplateResponse(
        request, "admin/roles/partials/_notes_form.html", {"role": role}
    )


@router.post("/inline/notes/")
async def role_inline_notes_post(
    role_id: str,
    request: Request,
    notes: str = Form(""),
    user: AdminUser | RedirectResponse = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Save notes; return updated read partial."""
    redirect, user = check_auth(user)
    if redirect:
        return redirect
    await _get_role(role_id, db)  # 404 check
    await db.execute(
        "UPDATE roles SET notes=$1 WHERE id=$2", notes.strip() or None, role_id
    )
    role = await _get_role(role_id, db)
    if not is_htmx(request):
        return RedirectResponse(f"/admin/roles/{role_id}/", status_code=303)
    return templates.TemplateResponse(
        request,
        "admin/roles/partials/_notes_read.html",
        {"role": role},
        headers=flash_trigger("success", "Notes saved."),
    )
```

- [ ] **Step 5: Mount the sub-router in router.py**

Add to `src/api/admin/router.py`:

Import: `from src.api.admin import roles_detail as roles_detail_module`

Mount: `admin_router.include_router(roles_detail_module.router)`

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/api/admin/test_roles_detail_inline.py -v`
Expected: All passed

- [ ] **Step 7: Run full test suite**

Run: `uv run pytest --no-cov -q`
Expected: No regressions

- [ ] **Step 8: Commit**

```bash
git add src/api/admin/roles_detail.py \
        src/api/admin/router.py \
        src/templates/admin/roles/partials/_org_form.html \
        src/templates/admin/roles/partials/_title_form.html \
        src/templates/admin/roles/partials/_notes_form.html \
        tests/api/admin/test_roles_detail_inline.py
git commit -m "#67 feat: add inline editing routes for role org, title, notes"
```

---

## Task 4: Add Assignment — Form Row and Create Route

Add the "+ Add assignment" functionality with person typeahead and date inputs.

**Files:**
- Create: `src/templates/admin/roles/partials/_assignment_form_row.html`
- Modify: `src/api/admin/roles_detail.py` (add assignment routes)
- Create: `tests/api/admin/test_roles_assignments_inline.py`

- [ ] **Step 1: Write failing tests**

Create `tests/api/admin/test_roles_assignments_inline.py`:

```python
"""Integration tests for inline assignment create on role detail."""

import json
import os

import asyncpg
import pytest
from httpx import ASGITransport, AsyncClient

from src.api.admin.deps import get_db
from src.api.main import app
from src.core.db import apply_schema, generate_id

pytestmark = pytest.mark.integration

AUTH_HEADERS = {
    "X-ExeDev-UserID": "test-user",
    "X-ExeDev-Email": "test@example.com",
}
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
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c
    app.dependency_overrides.pop(get_db, None)


async def _make_org(db, name: str) -> str:
    oid = generate_id()
    await db.execute("INSERT INTO organizations (id) VALUES ($1)", oid)
    await db.execute(
        "INSERT INTO organization_names (id, organization_id, name, is_canonical)"
        " VALUES ($1, $2, $3, TRUE)",
        generate_id(), oid, name,
    )
    return oid


async def _make_person(db, first: str, last: str) -> str:
    pid = generate_id()
    await db.execute("INSERT INTO people (id) VALUES ($1)", pid)
    await db.execute(
        "INSERT INTO person_names (id, person_id, first_name, last_name, is_canonical)"
        " VALUES ($1, $2, $3, $4, TRUE)",
        generate_id(), pid, first, last,
    )
    return pid


@pytest.fixture
async def role_id(db):
    oid = await _make_org(db, "Test Org")
    rid = generate_id()
    await db.execute(
        "INSERT INTO roles (id, organization_id, title) VALUES ($1, $2, $3)",
        rid, oid, "Executive Director",
    )
    return rid


@pytest.fixture
async def person_id(db):
    return await _make_person(db, "Jane", "Doe")


# ---------------------------------------------------------------------------
# New row form
# ---------------------------------------------------------------------------


async def test_new_row_returns_form(client, role_id):
    r = await client.get(
        f"/admin/roles/{role_id}/assignments/new-row/", headers=HTMX_HEADERS
    )
    assert r.status_code == 200
    assert b"<form" in r.content
    assert b"person-search" in r.content


async def test_new_row_unknown_role_returns_404(client):
    r = await client.get(
        f"/admin/roles/{generate_id()}/assignments/new-row/", headers=HTMX_HEADERS
    )
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# Create assignment
# ---------------------------------------------------------------------------


async def test_create_persists_assignment(client, role_id, person_id, db):
    r = await client.post(
        f"/admin/roles/{role_id}/assignments/",
        headers=HTMX_HEADERS,
        data={
            "person_id": person_id,
            "start_date": "2024-01-15",
            "end_date": "",
            "is_current": "true",
        },
    )
    assert r.status_code == 200
    row = await db.fetchrow(
        "SELECT * FROM role_assignments WHERE role_id=$1 AND person_id=$2",
        role_id, person_id,
    )
    assert row is not None
    assert row["is_current"] is True
    assert str(row["start_date"]) == "2024-01-15"
    assert row["end_date"] is None


async def test_create_with_end_date(client, role_id, person_id, db):
    r = await client.post(
        f"/admin/roles/{role_id}/assignments/",
        headers=HTMX_HEADERS,
        data={
            "person_id": person_id,
            "start_date": "2020-01-01",
            "end_date": "2023-12-31",
        },
    )
    assert r.status_code == 200
    row = await db.fetchrow(
        "SELECT * FROM role_assignments WHERE role_id=$1 AND person_id=$2",
        role_id, person_id,
    )
    assert row is not None
    assert row["is_current"] is False
    assert str(row["end_date"]) == "2023-12-31"


async def test_create_returns_tbody(client, role_id, person_id):
    r = await client.post(
        f"/admin/roles/{role_id}/assignments/",
        headers=HTMX_HEADERS,
        data={"person_id": person_id, "start_date": "2024-01-01"},
    )
    assert r.status_code == 200
    assert b"Jane" in r.content  # person name appears in tbody


async def test_create_returns_success_flash(client, role_id, person_id):
    r = await client.post(
        f"/admin/roles/{role_id}/assignments/",
        headers=HTMX_HEADERS,
        data={"person_id": person_id, "start_date": "2024-01-01"},
    )
    trigger = json.loads(r.headers["hx-trigger"])
    assert trigger["showFlash"]["level"] == "success"


async def test_create_missing_person_returns_error(client, role_id):
    r = await client.post(
        f"/admin/roles/{role_id}/assignments/",
        headers=HTMX_HEADERS,
        data={"person_id": "", "start_date": "2024-01-01"},
    )
    assert r.status_code == 200
    trigger = json.loads(r.headers["hx-trigger"])
    assert trigger["showFlash"]["level"] == "error"
    assert b"<form" in r.content


async def test_create_current_with_end_date_returns_error(client, role_id, person_id):
    """CHECK constraint: is_current=TRUE + end_date set should fail."""
    r = await client.post(
        f"/admin/roles/{role_id}/assignments/",
        headers=HTMX_HEADERS,
        data={
            "person_id": person_id,
            "start_date": "2024-01-01",
            "end_date": "2024-06-01",
            "is_current": "true",
        },
    )
    assert r.status_code == 200
    trigger = json.loads(r.headers["hx-trigger"])
    assert trigger["showFlash"]["level"] == "error"


async def test_create_non_htmx_redirects(client, role_id, person_id):
    r = await client.post(
        f"/admin/roles/{role_id}/assignments/",
        headers=AUTH_HEADERS,
        data={"person_id": person_id, "start_date": "2024-01-01"},
        follow_redirects=False,
    )
    assert r.status_code == 303
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/api/admin/test_roles_assignments_inline.py -v`
Expected: FAIL (404 — routes don't exist)

- [ ] **Step 3: Create the assignment form row template**

Create `src/templates/admin/roles/partials/_assignment_form_row.html`:

```html
{# admin/roles/partials/_assignment_form_row.html — new assignment inline form #}
<tr id="assignment-row-new">
  <td colspan="4" style="padding:var(--space-2) var(--space-4)">
    <form hx-post="/admin/roles/{{ role_id }}/assignments/"
          hx-target="#assignments-table tbody"
          hx-swap="innerHTML"
          style="display:flex;gap:var(--space-2);align-items:flex-start;flex-wrap:wrap">
      <div class="form-group" style="margin-bottom:0;flex:2;min-width:10rem;position:relative">
        <input type="text" autocomplete="off" placeholder="Search for a person…"
               id="person-search-display"
               hx-get="/admin/people/search/"
               hx-trigger="input changed delay:200ms"
               hx-target="#person-search-results"
               hx-swap="innerHTML"
               hx-params="q"
               name="q"
               role="combobox"
               aria-expanded="false"
               aria-haspopup="listbox"
               aria-controls="person-search-results"
               aria-autocomplete="list">
        <input type="hidden" name="person_id" id="person-id-hidden">
        <ul id="person-search-results" class="typeahead-results" role="listbox"></ul>
      </div>
      <div class="form-group" style="margin-bottom:0;flex:1;min-width:8rem">
        <input type="date" name="start_date" placeholder="Start date"
               title="Start date" value="{{ start_date_input or '' }}">
      </div>
      <div class="form-group" style="margin-bottom:0;flex:1;min-width:8rem">
        <input type="date" name="end_date" placeholder="End date"
               title="End date" id="end-date-input" value="{{ end_date_input or '' }}">
      </div>
      <div class="form-group" style="margin-bottom:0;display:flex;align-items:center;gap:var(--space-1)">
        <input type="checkbox" name="is_current" value="true" id="is-current-cb"
               {% if is_current_input %}checked{% endif %}>
        <label for="is-current-cb" style="font-size:var(--font-size-sm);white-space:nowrap">Current</label>
      </div>
      <div style="display:flex;gap:var(--space-2);white-space:nowrap">
        <button type="submit" class="btn btn--sm btn--primary">Save</button>
        <button type="button" class="btn btn--sm btn--secondary"
                onclick="this.closest('tr').remove()">Cancel</button>
      </div>
    </form>
    <script>
    (function() {
      var inp    = document.getElementById('person-search-display');
      var ul     = document.getElementById('person-search-results');
      var hidden = document.getElementById('person-id-hidden');
      var cb     = document.getElementById('is-current-cb');
      var endDt  = document.getElementById('end-date-input');
      var activeIdx = -1;

      // Disable end_date when is_current is checked
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

- [ ] **Step 4: Add assignment routes to roles_detail.py**

Append to `src/api/admin/roles_detail.py` (add `import datetime` and `import asyncpg` at top):

```python
import datetime

import asyncpg


def _parse_date(value: str) -> datetime.date | None:
    """Parse ISO date string, return None if empty."""
    value = value.strip()
    if not value:
        return None
    return datetime.date.fromisoformat(value)


async def _fetch_assignments(role_id: str, db) -> list:
    """Fetch all assignments for a role, sorted for display."""
    return await db.fetch(
        """SELECT ra.id, ra.is_current, ra.start_date, ra.end_date, ra.archived_at,
                  p.id AS person_id,
                  pn.display_name AS person_name
           FROM role_assignments ra
           JOIN people p ON p.id = ra.person_id
           LEFT JOIN v_person_display_names pn ON pn.person_id = p.id
           WHERE ra.role_id = $1
           ORDER BY ra.is_current DESC, ra.start_date DESC NULLS LAST""",
        role_id,
    )


@router.get("/assignments/new-row/")
async def assignment_new_row(
    role_id: str,
    request: Request,
    user: AdminUser | RedirectResponse = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Return blank inline assignment form row."""
    redirect, user = check_auth(user)
    if redirect:
        return redirect
    await _get_role(role_id, db)  # 404 check
    return templates.TemplateResponse(
        request,
        "admin/roles/partials/_assignment_form_row.html",
        {
            "role_id": role_id,
            "start_date_input": "",
            "end_date_input": "",
            "is_current_input": False,
        },
    )


@router.post("/assignments/")
async def assignment_create(
    role_id: str,
    request: Request,
    person_id: str = Form(""),
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
    await _get_role(role_id, db)  # 404 check

    person_id_val = person_id.strip()
    is_current_val = bool(is_current)

    # Validate person_id
    if not person_id_val:
        if not is_htmx(request):
            return RedirectResponse(f"/admin/roles/{role_id}/", status_code=303)
        return templates.TemplateResponse(
            request,
            "admin/roles/partials/_assignment_form_row.html",
            {
                "role_id": role_id,
                "start_date_input": start_date,
                "end_date_input": end_date,
                "is_current_input": is_current_val,
            },
            headers={
                **flash_trigger("error", "Person is required."),
                "HX-Retarget": "#assignment-row-new",
                "HX-Reswap": "outerHTML",
            },
        )

    try:
        start_date_val = _parse_date(start_date)
        end_date_val = _parse_date(end_date)
    except ValueError:
        if not is_htmx(request):
            return RedirectResponse(f"/admin/roles/{role_id}/", status_code=303)
        return templates.TemplateResponse(
            request,
            "admin/roles/partials/_assignment_form_row.html",
            {
                "role_id": role_id,
                "start_date_input": start_date,
                "end_date_input": end_date,
                "is_current_input": is_current_val,
            },
            headers={
                **flash_trigger("error", "Invalid date format. Use YYYY-MM-DD."),
                "HX-Retarget": "#assignment-row-new",
                "HX-Reswap": "outerHTML",
            },
        )

    ra_id = generate_id()
    try:
        await db.execute(
            """INSERT INTO role_assignments (id, person_id, role_id, is_current, start_date, end_date)
               VALUES ($1, $2, $3, $4, $5, $6)""",
            ra_id, person_id_val, role_id, is_current_val, start_date_val, end_date_val,
        )
    except asyncpg.CheckViolationError:
        if not is_htmx(request):
            return RedirectResponse(f"/admin/roles/{role_id}/", status_code=303)
        return templates.TemplateResponse(
            request,
            "admin/roles/partials/_assignment_form_row.html",
            {
                "role_id": role_id,
                "start_date_input": start_date,
                "end_date_input": end_date,
                "is_current_input": is_current_val,
            },
            headers={
                **flash_trigger("error", "Current assignments cannot have an end date."),
                "HX-Retarget": "#assignment-row-new",
                "HX-Reswap": "outerHTML",
            },
        )
    except asyncpg.UniqueViolationError:
        if not is_htmx(request):
            return RedirectResponse(f"/admin/roles/{role_id}/", status_code=303)
        return templates.TemplateResponse(
            request,
            "admin/roles/partials/_assignment_form_row.html",
            {
                "role_id": role_id,
                "start_date_input": start_date,
                "end_date_input": end_date,
                "is_current_input": is_current_val,
            },
            headers={
                **flash_trigger(
                    "error",
                    "An assignment for this person with this start date already exists.",
                ),
                "HX-Retarget": "#assignment-row-new",
                "HX-Reswap": "outerHTML",
            },
        )

    if not is_htmx(request):
        return RedirectResponse(f"/admin/roles/{role_id}/", status_code=303)

    assignments = await _fetch_assignments(role_id, db)
    return templates.TemplateResponse(
        request,
        "admin/roles/partials/_assignment_rows.html",
        {"assignments": assignments},
        headers=flash_trigger("success", "Assignment added."),
    )
```

Add `generate_id` to the imports at the top of `roles_detail.py`:

```python
from src.core.db import generate_id
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/api/admin/test_roles_assignments_inline.py -v`
Expected: All passed

- [ ] **Step 6: Run full test suite**

Run: `uv run pytest --no-cov -q`
Expected: No regressions

- [ ] **Step 7: Manual verification**

Visit `https://power-map.exe.xyz:8001/admin/roles/01KM234ZNEF4366FT0ZCTPXN9Z/` and verify:
- Page renders with inline layout
- Organization Change button opens typeahead, saves correctly
- Title Edit opens form, saves correctly
- Notes Edit opens form, saves correctly
- "+ Add assignment" opens form with person typeahead and date inputs
- Creating an assignment refreshes the tbody
- "Current" checkbox disables end date input
- Section heading says "Assignments" (not "Assignment History")
- Metadata footer shows above Danger Zone

- [ ] **Step 8: Commit**

```bash
git add src/api/admin/roles_detail.py \
        src/templates/admin/roles/partials/_assignment_form_row.html \
        tests/api/admin/test_roles_assignments_inline.py
git commit -m "#67 feat: add inline assignment create with person typeahead"
```

---

## Task 5: Clean Up — Remove Old Edit Form Route

The old `GET /roles/{role_id}/edit/` and `POST /roles/{role_id}/edit/` routes and the `form.html` template are now superseded by inline editing. Remove them. Also fix the roles list `_rows.html` which links to the now-removed edit route.

**Files:**
- Modify: `src/api/admin/roles.py` (remove edit routes)
- Delete: `src/templates/admin/roles/form.html` (orphaned template)
- Modify: `src/templates/admin/roles/_rows.html` (change edit link → detail link)
- Modify: `tests/api/admin/test_roles.py` (remove/update edit-related tests if any)

- [ ] **Step 1: Check for edit route tests**

Read `tests/api/admin/test_roles.py` to identify any tests that reference the edit form or edit POST route.

- [ ] **Step 2: Remove edit routes from roles.py**

Remove the `role_edit_form` (GET `/{role_id}/edit/`) and `role_update` (POST `/{role_id}/edit/`) functions from `src/api/admin/roles.py`.

- [ ] **Step 3: Delete orphaned form template**

Delete `src/templates/admin/roles/form.html` — it's no longer referenced by any route.

- [ ] **Step 4: Fix roles list _rows.html**

In `src/templates/admin/roles/_rows.html` line 14, change the edit link to point to the detail page:

Old: `<a href="/admin/roles/{{ role.id }}/edit/" class="btn btn--ghost btn--sm">Edit</a>`
New: `<a href="/admin/roles/{{ role.id }}/" class="btn btn--ghost btn--sm">View</a>`

- [ ] **Step 5: Remove or update edit-related tests**

Remove any tests that test the edit form GET/POST routes. If any test navigates to the edit page, update it to test inline routes instead.

- [ ] **Step 6: Run full test suite**

Run: `uv run pytest --no-cov -q`
Expected: All pass, no regressions

- [ ] **Step 7: Commit**

```bash
git add src/api/admin/roles.py tests/api/admin/test_roles.py \
        src/templates/admin/roles/_rows.html
git rm src/templates/admin/roles/form.html
git commit -m "#67 refactor: remove old role edit form routes (superseded by inline editing)"
```
