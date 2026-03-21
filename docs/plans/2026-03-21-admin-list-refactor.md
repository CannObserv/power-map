# Admin List View Refactor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add top+sticky-footer pagination, a per-page size selector, and an org deduplication feature (discovery banner + dedicated merge screen) to the admin dashboard.

**Architecture:** Three independent subsystems — (1) pagination layout CSS + template change applied to all four list views, (2) `page_size` query param wired through backend routes and filter bars, (3) a new org duplicates route + merge endpoint backed by `pg_trgm` similarity and a `duplicate_dismissals` table. All work is in the `feature/18-admin-list-refactor` worktree at `.worktrees/feature/18-admin-list-refactor`.

**Tech Stack:** FastAPI, asyncpg, Jinja2, HTMX, PostgreSQL (`pg_trgm`), pytest (unit + integration), uv

---

## File Map

**Modified:**
- `src/static/admin/admin.css` — add `.pagination--sticky` and `.alert`/`.alert--warning` CSS classes
- `src/templates/admin/orgs/_region.html` — top + sticky pagination; dedup banner
- `src/templates/admin/people/_region.html` — top + sticky pagination
- `src/templates/admin/roles/_region.html` — top + sticky pagination
- `src/templates/admin/role_assignments/_region.html` — top + sticky pagination
- `src/templates/admin/orgs/list.html` — add `page_size` select to filter bar
- `src/templates/admin/people/list.html` — add `page_size` select to filter bar
- `src/templates/admin/roles/list.html` — add `page_size` select to filter bar
- `src/templates/admin/role_assignments/list.html` — add `page_size` select to filter bar
- `src/api/admin/orgs.py` — add `page_size` param to list route; add duplicates list + merge endpoints (declared before `/{org_id}/`)
- `src/api/admin/people.py` — add `page_size` param to list route
- `src/api/admin/roles.py` — add `page_size` param to list route
- `src/api/admin/role_assignments.py` — add `page_size` param to list route
- `src/core/schema.sql` — add `pg_trgm` extension + `duplicate_dismissals` table

**Created:**
- `src/templates/admin/orgs/duplicates.html` — full-page duplicate pairs list
- `src/templates/admin/orgs/_duplicates_region.html` — HTMX partial for the pairs list
- `tests/api/admin/test_orgs_duplicates.py` — integration tests for dedup routes
- `tests/api/admin/test_list_ui.py` — integration tests for pagination/page-size HTML rendering

---

## Task 1: Write failing integration tests for pagination and page_size UI

These tests verify the HTML structure we're about to add. They must fail now and pass after Tasks 2–4.

**Files:**
- Create: `tests/api/admin/test_list_ui.py`

The TestClient without a DB pool still renders templates, which is enough to check HTML structure. The orgs list route will return 500 if there's no DB, but we can use the `client` fixture from conftest (which suppresses server errors via `raise_server_exceptions=False`) — for rendering tests we need a client with a live DB *or* we can accept the approach of using integration markers and a DB. Since the existing `test_base_template.py` uses `pytest.mark.integration` and a live DB, follow that pattern.

However, for render-only structure checks (does the HTML contain `pagination--sticky` class?), the orgs list just needs to return 200. Use `pytest.mark.integration` so the tests skip when `DATABASE_URL` is not set.

- [ ] **Step 1: Write failing tests**

Create `tests/api/admin/test_list_ui.py`:

```python
"""Integration tests for list view UI: pagination placement and per-page size."""
import pytest
from fastapi.testclient import TestClient

from src.api.main import app

pytestmark = pytest.mark.integration

AUTH_HEADERS = {
    "X-ExeDev-UserID": "usr_test",
    "X-ExeDev-Email": "admin@test.com",
}


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


# --- Pagination placement ---

def test_orgs_list_has_sticky_pagination(client):
    """pagination--sticky class must appear in orgs list HTML."""
    response = client.get("/admin/orgs/", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert "pagination--sticky" in response.text


def test_people_list_has_sticky_pagination(client):
    response = client.get("/admin/people/", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert "pagination--sticky" in response.text


def test_roles_list_has_sticky_pagination(client):
    response = client.get("/admin/roles/", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert "pagination--sticky" in response.text


def test_ra_list_has_sticky_pagination(client):
    response = client.get("/admin/role-assignments/", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert "pagination--sticky" in response.text


# --- Per-page size selector ---

def test_orgs_list_has_page_size_select(client):
    """orgs list filter bar must include a page_size select."""
    response = client.get("/admin/orgs/", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert 'name="page_size"' in response.text


def test_people_list_has_page_size_select(client):
    response = client.get("/admin/people/", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert 'name="page_size"' in response.text


def test_roles_list_has_page_size_select(client):
    response = client.get("/admin/roles/", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert 'name="page_size"' in response.text


def test_ra_list_has_page_size_select(client):
    response = client.get("/admin/role-assignments/", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert 'name="page_size"' in response.text


# --- page_size URL param respected ---

def test_orgs_list_accepts_page_size_param(client):
    """page_size=25 in URL must be reflected in the selected option."""
    response = client.get("/admin/orgs/?page_size=25", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert "page_size" in response.text  # select rendered
```

- [ ] **Step 2: Run to confirm they fail**

```bash
uv run pytest tests/api/admin/test_list_ui.py -v -m integration
```

Expected: skip (if no `DATABASE_URL`) or fail with missing elements. Do not proceed until confirmed failing or skipped.

- [ ] **Step 3: Commit the failing tests**

```bash
git add tests/api/admin/test_list_ui.py
git commit -m "#18 test: add failing integration tests for pagination and page_size UI"
```

---

## Task 2: Add `.pagination--sticky` CSS class

**Files:**
- Modify: `src/static/admin/admin.css`

- [ ] **Step 1: Add CSS rules**

Append after line 169 (the `.pagination__info` rule):

```css
.pagination--sticky { position: sticky; bottom: 0; background: var(--color-surface-1); border-top: 1px solid var(--color-border); padding: 0.5rem 1rem; z-index: 10; }
.alert { padding: var(--space-3) var(--space-4); border-radius: var(--radius-md); font-size: var(--font-size-sm); }
.alert--warning { background: #fef9c3; color: #854d0e; border: 1px solid #fde68a; }
```

Also add inside the existing `@media (prefers-color-scheme: dark)` block:

```css
.alert--warning { background: #422006; color: #fde68a; border-color: #713f12; }
```

(`--color-surface-1` is `#ffffff` in light mode / `#1e293b` in dark mode — the correct token for card/surface backgrounds in this codebase. The design doc used `--color-surface` which does not exist.)

- [ ] **Step 2: Commit**

```bash
git add src/static/admin/admin.css
git commit -m "#18 feat: add pagination--sticky and alert CSS classes"
```

---

## Task 3: Pagination — top + sticky on all four list views

**Files:**
- Modify: `src/templates/admin/orgs/_region.html`
- Modify: `src/templates/admin/people/_region.html`
- Modify: `src/templates/admin/roles/_region.html`
- Modify: `src/templates/admin/role_assignments/_region.html`

Each `_region.html` currently renders pagination below the table only. Replace each file to render pagination once above the table and once in a `.pagination--sticky` div below it. Also introduce a `_qs` variable to keep `extra_qs` DRY — and include `page_size` in it (Task 5 wires it through the backend; during this intermediate state `page_size` will be `50` from the existing constant, which is harmless).

- [ ] **Step 1: Update `orgs/_region.html`**

Replace the entire file:

```html
{% from "admin/macros/pagination.html" import controls as pagination %}
{% set _qs = "q=" ~ q|urlencode ~ "&status=" ~ status|urlencode ~ "&page_size=" ~ page_size %}
{% if duplicate_count %}
<div class="alert alert--warning" style="margin-bottom:var(--space-4)">
  {{ duplicate_count }} possible duplicate organization{{ 's' if duplicate_count != 1 else '' }} —
  <a href="/admin/orgs/duplicates/">Review</a>
</div>
{% endif %}
{{ pagination(page, total_pages, showing_from, showing_to, total, page_range,
             "/admin/orgs/", "#orgs-list-region", _qs) }}
<div class="table-wrapper">
  <table class="data-table" id="orgs-table">
    <caption>Organizations — {{ total }} record{{ 's' if total != 1 else '' }}</caption>
    <thead>
      <tr>
        <th scope="col">Name</th><th scope="col">Status</th>
        <th scope="col">Created</th><th scope="col"><span class="sr-only">Actions</span></th>
      </tr>
    </thead>
    <tbody id="orgs-table-body">{% include "admin/orgs/_rows.html" %}</tbody>
  </table>
</div>
<div class="pagination--sticky">
  {{ pagination(page, total_pages, showing_from, showing_to, total, page_range,
               "/admin/orgs/", "#orgs-list-region", _qs) }}
</div>
```

Note: `duplicate_count` is added to context in Task 6. Until then it will be undefined → Jinja2 treats undefined as falsy, so the banner block silently suppresses. No breakage.

- [ ] **Step 2: Update `people/_region.html`**

```html
{% from "admin/macros/pagination.html" import controls as pagination %}
{% set _qs = "q=" ~ q|urlencode ~ "&status=" ~ status|urlencode ~ "&page_size=" ~ page_size %}
{{ pagination(page, total_pages, showing_from, showing_to, total, page_range,
             "/admin/people/", "#people-list-region", _qs) }}
<div class="table-wrapper">
  <table class="data-table" id="people-table">
    <caption>People — {{ total }} record{{ 's' if total != 1 else '' }}</caption>
    <thead>
      <tr>
        <th scope="col">Name</th><th scope="col">Status</th>
        <th scope="col">Created</th><th scope="col"><span class="sr-only">Actions</span></th>
      </tr>
    </thead>
    <tbody id="people-table-body">{% include "admin/people/_rows.html" %}</tbody>
  </table>
</div>
<div class="pagination--sticky">
  {{ pagination(page, total_pages, showing_from, showing_to, total, page_range,
               "/admin/people/", "#people-list-region", _qs) }}
</div>
```

- [ ] **Step 3: Update `roles/_region.html`**

```html
{% from "admin/macros/pagination.html" import controls as pagination %}
{% set _qs = "q=" ~ q|urlencode ~ "&org_q=" ~ org_q|urlencode ~ "&status=" ~ status|urlencode ~ "&page_size=" ~ page_size %}
{{ pagination(page, total_pages, showing_from, showing_to, total, page_range,
             "/admin/roles/", "#roles-list-region", _qs) }}
<div class="table-wrapper">
  <table class="data-table" id="roles-table">
    <caption>Roles — {{ total }} record{{ 's' if total != 1 else '' }}</caption>
    <thead>
      <tr>
        <th scope="col">Organization</th><th scope="col">Title</th>
        <th scope="col">Status</th><th scope="col">Created</th>
        <th scope="col"><span class="sr-only">Actions</span></th>
      </tr>
    </thead>
    <tbody id="roles-table-body">{% include "admin/roles/_rows.html" %}</tbody>
  </table>
</div>
<div class="pagination--sticky">
  {{ pagination(page, total_pages, showing_from, showing_to, total, page_range,
               "/admin/roles/", "#roles-list-region", _qs) }}
</div>
```

- [ ] **Step 4: Update `role_assignments/_region.html`**

```html
{% from "admin/macros/pagination.html" import controls as pagination %}
{% set _qs = "q=" ~ q|urlencode ~ "&status=" ~ status|urlencode ~ "&page_size=" ~ page_size %}
{{ pagination(page, total_pages, showing_from, showing_to, total, page_range,
             "/admin/role-assignments/", "#ra-list-region", _qs) }}
<div class="table-wrapper">
  <table class="data-table" id="ra-table">
    <caption>Role Assignments — {{ total }} record{{ 's' if total != 1 else '' }}</caption>
    <thead>
      <tr>
        <th scope="col">Assignment</th>
        <th scope="col">Status</th>
        <th scope="col">Start</th>
        <th scope="col">End</th>
        <th scope="col">Created</th>
        <th scope="col"><span class="sr-only">Actions</span></th>
      </tr>
    </thead>
    <tbody id="ra-table-body">{% include "admin/role_assignments/_rows.html" %}</tbody>
  </table>
</div>
<div class="pagination--sticky">
  {{ pagination(page, total_pages, showing_from, showing_to, total, page_range,
               "/admin/role-assignments/", "#ra-list-region", _qs) }}
</div>
```

- [ ] **Step 5: Run unit tests (fast check)**

```bash
uv run pytest -v --ignore=tests/api/admin/test_list_ui.py --ignore=tests/api/admin/test_orgs_duplicates.py
```

Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add src/templates/admin/orgs/_region.html \
        src/templates/admin/people/_region.html \
        src/templates/admin/roles/_region.html \
        src/templates/admin/role_assignments/_region.html
git commit -m "#18 feat: add top and sticky-footer pagination to all list views"
```

---

## Task 4: Per-page size — backend

**Files:**
- Modify: `src/api/admin/orgs.py`
- Modify: `src/api/admin/people.py`
- Modify: `src/api/admin/roles.py`
- Modify: `src/api/admin/role_assignments.py`
- Test: `tests/api/admin/test_pagination.py` (extend with custom page_size cases)

- [ ] **Step 1: Extend `test_pagination.py` with custom page_size tests**

Add to `tests/api/admin/test_pagination.py`:

```python
def test_context_custom_page_size_25():
    ctx = pagination_context(1, 200, 25)
    assert ctx["total_pages"] == 8
    assert ctx["showing_to"] == 25

def test_context_page_size_100():
    ctx = pagination_context(1, 200, 100)
    assert ctx["total_pages"] == 2
    assert ctx["showing_to"] == 100
```

- [ ] **Step 2: Run — confirm they pass (function already supports it)**

```bash
uv run pytest tests/api/admin/test_pagination.py -v
```

Expected: all pass. `pagination_context` already takes `page_size` as a parameter.

- [ ] **Step 3: Update `orgs.py` list route**

In `orgs_list`, add `page_size` to the function signature after `page`:

```python
page_size: int = Query(50, ge=10, le=500),
```

In the function body, replace both uses of `PAGE_SIZE` with `page_size`:

```python
pctx = pagination_context(page, count, page_size)
offset = (pctx["page"] - 1) * page_size
list_params = params + [page_size, offset]
```

In the `ctx` dict, the line `"page_size": PAGE_SIZE,` already exists — change it to `"page_size": page_size,`. Do not add a second `page_size` key.

- [ ] **Step 4: Update `people.py` list route** — same change: add `page_size: int = Query(50, ge=10, le=500)` to signature; replace `PAGE_SIZE` uses with `page_size`; update `ctx` to set `"page_size": page_size`.

- [ ] **Step 5: Update `roles.py` list route** — same change.

- [ ] **Step 6: Update `role_assignments.py` list route** — same change.

- [ ] **Step 7: Run unit tests**

```bash
uv run pytest tests/api/admin/test_pagination.py -v
```

Expected: all pass.

- [ ] **Step 8: Commit**

```bash
git add src/api/admin/orgs.py src/api/admin/people.py \
        src/api/admin/roles.py src/api/admin/role_assignments.py \
        tests/api/admin/test_pagination.py
git commit -m "#18 feat: add page_size query param to all list routes"
```

---

## Task 5: Per-page size — filter bar UI

**Files:**
- Modify: `src/templates/admin/orgs/list.html`
- Modify: `src/templates/admin/people/list.html`
- Modify: `src/templates/admin/roles/list.html`
- Modify: `src/templates/admin/role_assignments/list.html`

Add a `<select name="page_size">` with options 25/50/100/250 to the `.filter-card__controls` section of each list page. On `change`, HTMX re-fetches with `page=1` (via `hx-vals`) and includes all other filter fields.

Also update the existing search `<input>` and status `<select>` in each file to include `[name='page_size']` in their `hx-include` attribute so the chosen page size is preserved when searching or changing status.

- [ ] **Step 1: Update `orgs/list.html`**

In the `.filter-card__controls` div, add after the existing status field:

```html
<div class="filter-card__field">
  <label for="orgs-page-size">Per page</label>
  <select id="orgs-page-size" name="page_size"
          hx-get="/admin/orgs/" hx-trigger="change"
          hx-target="#orgs-list-region"
          hx-include="[name='q'],[name='status']"
          hx-vals='{"page": 1}'
          hx-push-url="true">
    <option value="25"  {% if page_size==25  %}selected{% endif %}>25</option>
    <option value="50"  {% if page_size==50  %}selected{% endif %}>50</option>
    <option value="100" {% if page_size==100 %}selected{% endif %}>100</option>
    <option value="250" {% if page_size==250 %}selected{% endif %}>250</option>
  </select>
</div>
```

Update the search input's `hx-include` from `[name='status']` to `[name='status'],[name='page_size']`.

Update the status select's `hx-include` from `[name='q']` to `[name='q'],[name='page_size']`.

- [ ] **Step 2: Update `people/list.html`** — same pattern; target `#people-list-region`; prefix `people-`.

- [ ] **Step 3: Update `roles/list.html`** — same pattern; target `#roles-list-region`; prefix `roles-`. The roles page has an additional `org_q` field; include `[name='org_q']` in the new select's `hx-include`. Update all existing HTMX elements to also include `[name='page_size']`.

- [ ] **Step 4: Update `role_assignments/list.html`** — same pattern; target `#ra-list-region`; prefix `ra-`.

- [ ] **Step 5: Commit**

```bash
git add src/templates/admin/orgs/list.html \
        src/templates/admin/people/list.html \
        src/templates/admin/roles/list.html \
        src/templates/admin/role_assignments/list.html
git commit -m "#18 feat: add per-page size selector to list filter bars"
```

---

## Task 6: Schema — `pg_trgm` extension + `duplicate_dismissals` table

**Files:**
- Modify: `src/core/schema.sql`

`apply_schema` is idempotent. Both additions use `IF NOT EXISTS`.

- [ ] **Step 1: Add `pg_trgm` extension**

Add after line 4 (the file header comment block):

```sql
-- Enable trigram similarity for duplicate detection
CREATE EXTENSION IF NOT EXISTS pg_trgm;
```

- [ ] **Step 2: Add `duplicate_dismissals` table**

Add near the bottom of the schema, before the `-- Ingestion` section:

```sql
CREATE TABLE IF NOT EXISTS duplicate_dismissals (
    id            TEXT        PRIMARY KEY,
    entity_type   TEXT        NOT NULL,
    entity_a_id   TEXT        NOT NULL,
    entity_b_id   TEXT        NOT NULL,
    dismissed_by  TEXT        NOT NULL,
    dismissed_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_dismissal UNIQUE (entity_type, entity_a_id, entity_b_id)
);
```

- [ ] **Step 3: Commit**

```bash
git add src/core/schema.sql
git commit -m "#18 feat: add pg_trgm extension and duplicate_dismissals table to schema"
```

---

## Task 7: Org duplicates — backend routes

**Files:**
- Modify: `src/api/admin/orgs.py`
- Create: `tests/api/admin/test_orgs_duplicates.py`

**Route ordering is critical:** FastAPI matches routes in declaration order. The new `/duplicates/` route and the `/{winner_id}/merge/{loser_id}/` route must be declared **before** the existing `/{org_id}/` route. When editing `orgs.py`, insert these new routes above the `@router.get("/{org_id}/")` definition (currently at line 155).

Three new things in `orgs.py`:

1. `_CANDIDATE_WHERE` — SQL fragment shared by the count and list queries.
2. `_count_org_duplicates(db) -> int` — returns the candidate pair count.
3. `GET /admin/orgs/duplicates/` — renders the duplicates list page.
4. `POST /admin/orgs/{winner_id}/merge/{loser_id}/` — executes the merge transaction.
5. `POST /admin/orgs/{id_a}/dismiss-duplicate/{id_b}/` — records a dismissal.

`orgs_list` also gets a `duplicate_count` context variable.

### Candidate query fragment

```python
_CANDIDATE_WHERE = """
    FROM organizations a
    JOIN organizations b ON b.id > a.id
    JOIN v_org_display_names dn_a ON dn_a.organization_id = a.id
    JOIN v_org_display_names dn_b ON dn_b.organization_id = b.id
    WHERE a.archived_at IS NULL AND b.archived_at IS NULL
      AND similarity(dn_a.display_name, dn_b.display_name) > 0.85
      AND NOT EXISTS (
          SELECT 1 FROM duplicate_dismissals
          WHERE entity_type = 'organization'
            AND entity_a_id = a.id AND entity_b_id = b.id
      )
"""
```

### Merge transaction

The merge must reassign **all** tables that reference `organization_id` or that use `entity_type='organization'` + `entity_id`:

```python
async with db.transaction():
    # organizations.parent_id (FK — must reassign before deleting loser)
    await db.execute(
        "UPDATE organizations SET parent_id=$1 WHERE parent_id=$2",
        winner_id, loser_id,
    )
    # organization_names — keep winner's canonical, fold loser's non-canonical as alt names
    await db.execute(
        "UPDATE organization_names SET organization_id=$1"
        " WHERE organization_id=$2 AND is_canonical=FALSE",
        winner_id, loser_id,
    )
    await db.execute(
        "DELETE FROM organization_names WHERE organization_id=$1 AND is_canonical=TRUE",
        loser_id,
    )
    # organization_acronyms
    await db.execute(
        "UPDATE organization_acronyms SET organization_id=$1"
        " WHERE organization_id=$2 AND is_canonical=FALSE",
        winner_id, loser_id,
    )
    await db.execute(
        "DELETE FROM organization_acronyms WHERE organization_id=$1 AND is_canonical=TRUE",
        loser_id,
    )
    # roles (FK column organization_id)
    await db.execute(
        "UPDATE roles SET organization_id=$1 WHERE organization_id=$2",
        winner_id, loser_id,
    )
    # Polymorphic entity tables (entity_type TEXT + entity_id TEXT, no FK constraint)
    for table in ("entity_addresses", "contact_methods", "urls",
                  "social_links", "import_provenance", "field_confidence"):
        await db.execute(
            f"UPDATE {table} SET entity_id=$1"
            f" WHERE entity_type='organization' AND entity_id=$2",
            winner_id, loser_id,
        )
    # identifiers — entity_type encoded in entity_identifier_type, not a column here
    await db.execute(
        "UPDATE identifiers SET entity_id=$1 WHERE entity_id=$2",
        winner_id, loser_id,
    )
    # Hard-delete the loser
    await db.execute("DELETE FROM organizations WHERE id=$1", loser_id)
```

- [ ] **Step 1: Write failing integration tests**

Create `tests/api/admin/test_orgs_duplicates.py`:

```python
"""Integration tests for org duplicate detection and merge routes."""
import asyncio
import os

import asyncpg
import pytest
from fastapi.testclient import TestClient

from src.api.main import app
from src.core.db import apply_schema, generate_id

pytestmark = pytest.mark.integration

AUTH_HEADERS = {
    "X-ExeDev-UserID": "usr_test",
    "X-ExeDev-Email": "admin@test.com",
}


def _get_dsn() -> str:
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        pytest.skip("DATABASE_URL not set")
    return dsn


async def _aconnect(dsn: str) -> asyncpg.Connection:
    conn = await asyncpg.connect(dsn)
    await apply_schema(conn)
    return conn


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture
def org_pair():
    """Insert two near-duplicate orgs (id_a < id_b), yield (id_a, id_b), teardown."""
    dsn = _get_dsn()
    id_a, id_b = generate_id(), generate_id()
    if id_a > id_b:
        id_a, id_b = id_b, id_a

    async def setup():
        conn = await _aconnect(dsn)
        try:
            for oid, name in [
                (id_a, "Alberta Gaming, Liquor and Cannabis Commission"),
                (id_b, "Alberta Gaming, Liquor, and Cannabis Commission"),
            ]:
                await conn.execute("INSERT INTO organizations (id) VALUES ($1)", oid)
                await conn.execute(
                    "INSERT INTO organization_names"
                    " (id, organization_id, name, is_canonical)"
                    " VALUES ($1, $2, $3, TRUE)",
                    generate_id(), oid, name,
                )
        finally:
            await conn.close()

    async def teardown():
        conn = await asyncpg.connect(dsn)
        try:
            await conn.execute(
                "DELETE FROM duplicate_dismissals"
                " WHERE entity_a_id=$1 OR entity_b_id=$1"
                " OR entity_a_id=$2 OR entity_b_id=$2",
                id_a, id_b,
            )
            for oid in [id_a, id_b]:
                await conn.execute(
                    "DELETE FROM organization_names WHERE organization_id=$1", oid
                )
                await conn.execute(
                    "DELETE FROM organizations WHERE id=$1", oid
                )
        finally:
            await conn.close()

    asyncio.run(setup())
    yield id_a, id_b
    asyncio.run(teardown())


def test_duplicates_list_returns_200(client, org_pair):
    response = client.get("/admin/orgs/duplicates/", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert "duplicate" in response.text.lower()


def test_duplicates_list_shows_pair(client, org_pair):
    response = client.get("/admin/orgs/duplicates/", headers=AUTH_HEADERS)
    assert "Alberta Gaming" in response.text


def test_orgs_list_shows_duplicate_banner(client, org_pair):
    response = client.get("/admin/orgs/", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert "possible duplicate" in response.text.lower()


def test_merge_hard_deletes_loser(client, org_pair):
    id_a, id_b = org_pair
    response = client.post(
        f"/admin/orgs/{id_a}/merge/{id_b}/",
        headers=AUTH_HEADERS,
        follow_redirects=False,
    )
    assert response.status_code in (302, 303)

    dsn = _get_dsn()

    async def check_deleted():
        conn = await asyncpg.connect(dsn)
        try:
            return await conn.fetchrow(
                "SELECT id FROM organizations WHERE id=$1", id_b
            )
        finally:
            await conn.close()

    assert asyncio.run(check_deleted()) is None


def test_dismiss_pair_removes_from_list(client, org_pair):
    id_a, id_b = org_pair
    response = client.post(
        f"/admin/orgs/{id_a}/dismiss-duplicate/{id_b}/",
        headers=AUTH_HEADERS,
        follow_redirects=False,
    )
    assert response.status_code in (302, 303)
    response2 = client.get("/admin/orgs/duplicates/", headers=AUTH_HEADERS)
    assert response2.status_code == 200
    # The dismissed pair should no longer appear as a candidate
    assert "Alberta Gaming, Liquor and Cannabis Commission" not in response2.text \
        or "Alberta Gaming, Liquor, and Cannabis Commission" not in response2.text
```

- [ ] **Step 2: Run to confirm they fail**

```bash
uv run pytest tests/api/admin/test_orgs_duplicates.py -v -m integration
```

Expected: errors — routes don't exist yet.

- [ ] **Step 3: Add `_CANDIDATE_WHERE` and `_count_org_duplicates` to `orgs.py`**

Add near the top of `orgs.py` (after the `PAGE_SIZE` constant):

```python
_CANDIDATE_WHERE = """
    FROM organizations a
    JOIN organizations b ON b.id > a.id
    JOIN v_org_display_names dn_a ON dn_a.organization_id = a.id
    JOIN v_org_display_names dn_b ON dn_b.organization_id = b.id
    WHERE a.archived_at IS NULL AND b.archived_at IS NULL
      AND similarity(dn_a.display_name, dn_b.display_name) > 0.85
      AND NOT EXISTS (
          SELECT 1 FROM duplicate_dismissals
          WHERE entity_type = 'organization'
            AND entity_a_id = a.id AND entity_b_id = b.id
      )
"""


async def _count_org_duplicates(db) -> int:
    """Return count of non-dismissed near-duplicate org pairs."""
    return await db.fetchval(f"SELECT count(*) {_CANDIDATE_WHERE}")
```

- [ ] **Step 4: Update `orgs_list` to pass `duplicate_count` to context**

In `orgs_list`, after `pctx = pagination_context(...)`, add:

```python
duplicate_count = await _count_org_duplicates(db)
```

Add to the `ctx` dict:

```python
"duplicate_count": duplicate_count,
```

- [ ] **Step 5: Add `GET /admin/orgs/duplicates/`**

Insert this route **before** `@router.get("/{org_id}/")` in `orgs.py`:

```python
@router.get("/duplicates/")
async def orgs_duplicates(
    request: Request,
    user: AdminUser | RedirectResponse = Depends(get_admin_user),
    db=Depends(get_db),
):
    """List near-duplicate organization pairs for review."""
    redirect, user = check_auth(user)
    if redirect:
        return redirect

    pairs = await db.fetch(
        f"""SELECT
            a.id AS a_id, dn_a.display_name AS a_name, a.created_at AS a_created,
            b.id AS b_id, dn_b.display_name AS b_name, b.created_at AS b_created,
            similarity(dn_a.display_name, dn_b.display_name) AS score,
            (SELECT count(*) FROM roles
             WHERE organization_id = a.id AND archived_at IS NULL) AS a_roles,
            (SELECT count(*) FROM roles
             WHERE organization_id = b.id AND archived_at IS NULL) AS b_roles
        {_CANDIDATE_WHERE}
        ORDER BY score DESC"""
    )
    ctx = {
        "user": user,
        "active_section": "orgs",
        "pairs": pairs,
    }
    template = (
        "admin/orgs/_duplicates_region.html"
        if request.headers.get("HX-Request") and not request.headers.get("HX-Boosted")
        else "admin/orgs/duplicates.html"
    )
    return templates.TemplateResponse(request, template, ctx)
```

- [ ] **Step 6: Add `POST /admin/orgs/{winner_id}/merge/{loser_id}/`**

Insert **before** `@router.get("/{org_id}/")`:

```python
@router.post("/{winner_id}/merge/{loser_id}/")
async def org_merge(
    winner_id: str,
    loser_id: str,
    request: Request,
    user: AdminUser | RedirectResponse = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Merge loser into winner: reassign all references, hard-delete loser."""
    redirect, user = check_auth(user)
    if redirect:
        return redirect
    winner = await db.fetchrow("SELECT id FROM organizations WHERE id=$1", winner_id)
    loser = await db.fetchrow("SELECT id FROM organizations WHERE id=$1", loser_id)
    if not winner or not loser:
        raise HTTPException(status_code=404, detail="Organization not found")
    async with db.transaction():
        # organizations.parent_id (FK — reassign before deleting loser)
        await db.execute(
            "UPDATE organizations SET parent_id=$1 WHERE parent_id=$2",
            winner_id, loser_id,
        )
        # organization_names
        await db.execute(
            "UPDATE organization_names SET organization_id=$1"
            " WHERE organization_id=$2 AND is_canonical=FALSE",
            winner_id, loser_id,
        )
        await db.execute(
            "DELETE FROM organization_names"
            " WHERE organization_id=$1 AND is_canonical=TRUE",
            loser_id,
        )
        # organization_acronyms
        await db.execute(
            "UPDATE organization_acronyms SET organization_id=$1"
            " WHERE organization_id=$2 AND is_canonical=FALSE",
            winner_id, loser_id,
        )
        await db.execute(
            "DELETE FROM organization_acronyms"
            " WHERE organization_id=$1 AND is_canonical=TRUE",
            loser_id,
        )
        # roles
        await db.execute(
            "UPDATE roles SET organization_id=$1 WHERE organization_id=$2",
            winner_id, loser_id,
        )
        # Polymorphic entity tables (entity_type TEXT + entity_id TEXT, no FK)
        for table in ("entity_addresses", "contact_methods", "urls",
                      "social_links", "import_provenance", "field_confidence"):
            await db.execute(
                f"UPDATE {table} SET entity_id=$1"
                f" WHERE entity_type='organization' AND entity_id=$2",
                winner_id, loser_id,
            )
        # identifiers (entity_type encoded in entity_identifier_type_id, not a column)
        await db.execute(
            "UPDATE identifiers SET entity_id=$1 WHERE entity_id=$2",
            winner_id, loser_id,
        )
        await db.execute("DELETE FROM organizations WHERE id=$1", loser_id)
    return RedirectResponse("/admin/orgs/duplicates/", status_code=303)
```

- [ ] **Step 7: Add `POST /admin/orgs/{id_a}/dismiss-duplicate/{id_b}/`**

Insert **before** `@router.get("/{org_id}/")`:

```python
@router.post("/{id_a}/dismiss-duplicate/{id_b}/")
async def org_dismiss_duplicate(
    id_a: str,
    id_b: str,
    request: Request,
    user: AdminUser | RedirectResponse = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Record that this pair is not a duplicate (suppress from future results)."""
    redirect, user = check_auth(user)
    if redirect:
        return redirect
    # Store with consistent ordering (a < b)
    a, b = (id_a, id_b) if id_a < id_b else (id_b, id_a)
    await db.execute(
        "INSERT INTO duplicate_dismissals"
        " (id, entity_type, entity_a_id, entity_b_id, dismissed_by)"
        " VALUES ($1, 'organization', $2, $3, $4)"
        " ON CONFLICT (entity_type, entity_a_id, entity_b_id) DO NOTHING",
        generate_id(), a, b, user.email,
    )
    return RedirectResponse("/admin/orgs/duplicates/", status_code=303)
```

- [ ] **Step 8: Run integration tests**

```bash
uv run pytest tests/api/admin/test_orgs_duplicates.py -v -m integration
```

Expected: all pass.

- [ ] **Step 9: Commit**

```bash
git add src/api/admin/orgs.py tests/api/admin/test_orgs_duplicates.py
git commit -m "#18 feat: add org duplicate detection, merge, and dismiss endpoints"
```

---

## Task 8: Org duplicates — templates

**Files:**
- Create: `src/templates/admin/orgs/duplicates.html`
- Create: `src/templates/admin/orgs/_duplicates_region.html`

(The banner in `orgs/_region.html` was already added in Task 3 Step 1 — it simply won't render until `duplicate_count` is in context, which is now wired in Task 7.)

- [ ] **Step 1: Create `_duplicates_region.html`**

```html
{% if pairs %}
<table class="data-table">
  <caption>{{ pairs|length }} candidate pair{{ 's' if pairs|length != 1 else '' }}</caption>
  <thead>
    <tr>
      <th scope="col">Record A</th>
      <th scope="col">Record B</th>
      <th scope="col">Score</th>
      <th scope="col"><span class="sr-only">Actions</span></th>
    </tr>
  </thead>
  <tbody>
    {% for p in pairs %}
    <tr>
      <td>
        <a href="/admin/orgs/{{ p.a_id }}/">{{ p.a_name }}</a><br>
        <small style="color:var(--color-text-muted)">{{ p.a_created.strftime('%Y-%m-%d') }} · {{ p.a_roles }} role{{ 's' if p.a_roles != 1 else '' }}</small>
      </td>
      <td>
        <a href="/admin/orgs/{{ p.b_id }}/">{{ p.b_name }}</a><br>
        <small style="color:var(--color-text-muted)">{{ p.b_created.strftime('%Y-%m-%d') }} · {{ p.b_roles }} role{{ 's' if p.b_roles != 1 else '' }}</small>
      </td>
      <td>{{ "%.0f%%"|format(p.score * 100) }}</td>
      <td style="white-space:nowrap">
        <form method="post" action="/admin/orgs/{{ p.a_id }}/merge/{{ p.b_id }}/" style="display:inline">
          <button class="btn btn--primary btn--sm" type="submit">Keep A</button>
        </form>
        <form method="post" action="/admin/orgs/{{ p.b_id }}/merge/{{ p.a_id }}/" style="display:inline">
          <button class="btn btn--primary btn--sm" type="submit">Keep B</button>
        </form>
        <form method="post" action="/admin/orgs/{{ p.a_id }}/dismiss-duplicate/{{ p.b_id }}/" style="display:inline">
          <button class="btn btn--ghost btn--sm" type="submit">Not a duplicate</button>
        </form>
      </td>
    </tr>
    {% endfor %}
  </tbody>
</table>
{% else %}
<p style="color:var(--color-text-muted);text-align:center;padding:2rem">No duplicate candidates found.</p>
{% endif %}
```

- [ ] **Step 2: Create `duplicates.html`**

```html
{% extends "admin/base.html" %}
{% block title %}Duplicate Organizations{% endblock %}
{% block breadcrumb %}
  <a href="/admin/">Dashboard</a><span class="breadcrumb__sep">›</span>
  <a href="/admin/orgs/">Organizations</a><span class="breadcrumb__sep">›</span>
  <span>Duplicates</span>
{% endblock %}
{% block content %}
<div class="page-header">
  <h1>Duplicate Organizations</h1>
</div>
<div class="table-wrapper">
  {% include "admin/orgs/_duplicates_region.html" %}
</div>
{% endblock %}
```

- [ ] **Step 3: Run integration tests to confirm dedup template tests pass**

```bash
uv run pytest tests/api/admin/test_orgs_duplicates.py -v -m integration
```

Expected: all pass.

- [ ] **Step 4: Commit**

```bash
git add src/templates/admin/orgs/duplicates.html \
        src/templates/admin/orgs/_duplicates_region.html
git commit -m "#18 feat: add duplicate orgs list page template"
```

---

## Task 9: Final verification

- [ ] **Step 1: Run full test suite**

```bash
uv run pytest -v
```

Expected: all non-integration tests pass; integration tests skip or pass (not fail).

- [ ] **Step 2: Run linter**

```bash
uv run ruff check .
```

Expected: no errors.

- [ ] **Step 3: Commit any lint fixes** (if needed)

```bash
git add -u
git commit -m "#18 chore: fix lint warnings"
```
