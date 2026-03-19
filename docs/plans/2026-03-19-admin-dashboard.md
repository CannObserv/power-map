# Admin Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Server-rendered admin dashboard at `/admin/` for managing all power-map entities using Jinja2 + HTMX + custom CSS, authenticated via exe.dev proxy headers.

**Architecture:** Modular FastAPI routers (one per entity type) in `src/api/admin/`, Jinja2 templates in `src/templates/admin/`, custom CSS design-token system in `src/static/admin/admin.css`. Auth via middleware reading `X-ExeDev-UserID` / `X-ExeDev-Email` headers. Archive model adds `archived_at TIMESTAMPTZ` to core entity tables; hard delete is gated on archival.

**Tech Stack:** FastAPI, Jinja2, HTMX (CDN), asyncpg, custom CSS (no framework, no build step), pytest + starlette TestClient.

---

## File Structure

**New files:**
```
src/api/admin/__init__.py
src/api/admin/deps.py                   — AdminUser dataclass, get_admin_user dep, get_db dep
src/api/admin/router.py                 — mounts sub-routers, auth middleware
src/api/admin/orgs.py                   — organizations CRUD
src/api/admin/people.py                 — people CRUD
src/api/admin/roles.py                  — roles CRUD
src/api/admin/role_assignments.py       — role assignments CRUD
src/api/admin/lookups.py                — platforms, url_types, entity_identifier_types CRUD
src/api/admin/imports.py                — import history (read-only)
src/static/admin/admin.css
src/templates/admin/base.html
src/templates/admin/dashboard.html
src/templates/admin/orgs/list.html
src/templates/admin/orgs/detail.html
src/templates/admin/orgs/form.html
src/templates/admin/people/list.html
src/templates/admin/people/detail.html
src/templates/admin/people/form.html
src/templates/admin/roles/list.html
src/templates/admin/roles/detail.html
src/templates/admin/roles/form.html
src/templates/admin/role_assignments/list.html
src/templates/admin/role_assignments/detail.html
src/templates/admin/role_assignments/form.html
src/templates/admin/lookups/list.html
src/templates/admin/lookups/form.html
src/templates/admin/imports/batches.html
src/templates/admin/imports/batch_detail.html
src/templates/admin/partials/flash.html
src/templates/admin/partials/delete_modal.html
tests/api/admin/__init__.py
tests/api/admin/conftest.py
tests/api/admin/test_deps.py
tests/api/admin/test_orgs.py
tests/api/admin/test_people.py
tests/api/admin/test_roles.py
tests/api/admin/test_role_assignments.py
tests/api/admin/test_lookups.py
tests/api/admin/test_imports.py
```

**Modified files:**
```
src/core/schema.sql     — add archived_at TIMESTAMPTZ to 4 entity tables
src/api/main.py         — add lifespan (DB pool), Jinja2Templates, StaticFiles, mount admin router
pyproject.toml          — add jinja2, python-multipart
```

---

## Task 1: Schema migration — add `archived_at`

**Files:**
- Modify: `src/core/schema.sql`
- Test: `tests/core/test_schema.py` (already exists — add to it)

- [ ] **Step 1: Write the failing test**

Add to `tests/core/test_schema.py`:

```python
async def test_organizations_has_archived_at(db):
    """organizations.archived_at column must exist and be nullable."""
    org_id = generate_id()
    await db.execute("INSERT INTO organizations (id) VALUES ($1)", org_id)
    await db.execute(
        "UPDATE organizations SET archived_at = NOW() WHERE id = $1", org_id
    )
    row = await db.fetchrow(
        "SELECT archived_at FROM organizations WHERE id = $1", org_id
    )
    assert row["archived_at"] is not None


async def test_people_has_archived_at(db):
    person_id = await _person(db)
    await db.execute(
        "UPDATE people SET archived_at = NOW() WHERE id = $1", person_id
    )
    row = await db.fetchrow(
        "SELECT archived_at FROM people WHERE id = $1", person_id
    )
    assert row["archived_at"] is not None


async def test_roles_has_archived_at(db):
    org_id = await _org(db)
    role_id = await _role(db, org_id)
    await db.execute(
        "UPDATE roles SET archived_at = NOW() WHERE id = $1", role_id
    )
    row = await db.fetchrow(
        "SELECT archived_at FROM roles WHERE id = $1", role_id
    )
    assert row["archived_at"] is not None


async def test_role_assignments_has_archived_at(db):
    org_id = await _org(db)
    person_id = await _person(db)
    role_id = await _role(db, org_id)
    ra_id = generate_id()
    await db.execute(
        "INSERT INTO role_assignments (id, person_id, role_id) VALUES ($1, $2, $3)",
        ra_id, person_id, role_id,
    )
    await db.execute(
        "UPDATE role_assignments SET archived_at = NOW() WHERE id = $1", ra_id
    )
    row = await db.fetchrow(
        "SELECT archived_at FROM role_assignments WHERE id = $1", ra_id
    )
    assert row["archived_at"] is not None
```

- [ ] **Step 2: Run to confirm failure**

```
DATABASE_URL=<dsn> uv run pytest tests/core/test_schema.py::test_organizations_has_archived_at -m integration -v
```

Expected: FAIL — column does not exist.

- [ ] **Step 3: Add `archived_at` to `schema.sql`**

In each of the four `CREATE TABLE IF NOT EXISTS` blocks, add `archived_at TIMESTAMPTZ` as a nullable column (no default — NULL means active):

```sql
-- organizations (after updated_at):
archived_at  TIMESTAMPTZ,

-- people (after updated_at):
archived_at  TIMESTAMPTZ,

-- roles (after updated_at):
archived_at  TIMESTAMPTZ,

-- role_assignments (after updated_at):
archived_at  TIMESTAMPTZ,
```

Then add idempotent migration blocks for existing databases (add after all `CREATE TABLE` blocks, before triggers):

```sql
-- =============================================================================
-- Schema evolution: archived_at columns
-- =============================================================================

DO $$ BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name='organizations' AND column_name='archived_at'
    ) THEN
        ALTER TABLE organizations ADD COLUMN archived_at TIMESTAMPTZ;
    END IF;
END $$;

DO $$ BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name='people' AND column_name='archived_at'
    ) THEN
        ALTER TABLE people ADD COLUMN archived_at TIMESTAMPTZ;
    END IF;
END $$;

DO $$ BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name='roles' AND column_name='archived_at'
    ) THEN
        ALTER TABLE roles ADD COLUMN archived_at TIMESTAMPTZ;
    END IF;
END $$;

DO $$ BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name='role_assignments' AND column_name='archived_at'
    ) THEN
        ALTER TABLE role_assignments ADD COLUMN archived_at TIMESTAMPTZ;
    END IF;
END $$;
```

- [ ] **Step 4: Run tests**

```
DATABASE_URL=<dsn> uv run pytest tests/core/test_schema.py -m integration -v
```

Expected: all 4 new tests PASS, all existing tests still PASS.

- [ ] **Step 5: Commit**

```bash
git add src/core/schema.sql tests/core/test_schema.py
git commit -m "#6 feat: add archived_at columns to core entity tables"
```

---

## Task 2: Add dependencies

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: Add jinja2 and python-multipart**

```bash
uv add jinja2 python-multipart
```

- [ ] **Step 2: Verify**

```bash
uv run python -c "import jinja2, multipart; print('ok')"
```

Expected: `ok`

- [ ] **Step 3: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "#6 chore: add jinja2 and python-multipart dependencies"
```

---

## Task 3: App wiring — lifespan, Jinja2, StaticFiles, admin router

**Files:**
- Modify: `src/api/main.py`
- Create: `src/api/admin/__init__.py`
- Create: `src/api/admin/router.py` (skeleton)

- [ ] **Step 1: Write the failing test**

Create `tests/api/admin/__init__.py` (empty).

Create `tests/api/admin/conftest.py`:

```python
"""Shared fixtures for admin route tests."""

import pytest
from fastapi.testclient import TestClient

AUTH_HEADERS = {
    "X-ExeDev-UserID": "usr_test123",
    "X-ExeDev-Email": "admin@example.com",
}


@pytest.fixture
def client():
    """TestClient without DB pool (no lifespan). Auth + routing tests only."""
    from src.api.main import app

    with TestClient(app, raise_server_exceptions=False) as c:
        yield c
```

Create `tests/api/admin/test_deps.py`:

```python
"""Tests for admin auth dependency."""

from fastapi.testclient import TestClient

from src.api.main import app

_client = TestClient(app, raise_server_exceptions=False)


def test_admin_root_redirects_when_unauthenticated():
    """GET /admin/ without exe.dev headers must redirect to login."""
    response = _client.get("/admin/", allow_redirects=False)
    assert response.status_code in (302, 307)
    assert "/__exe.dev/login" in response.headers["location"]


def test_admin_root_redirects_when_only_user_id_present():
    response = _client.get(
        "/admin/", headers={"X-ExeDev-UserID": "usr123"}, allow_redirects=False
    )
    assert response.status_code in (302, 307)
    assert "/__exe.dev/login" in response.headers["location"]


def test_admin_root_redirects_when_only_email_present():
    response = _client.get(
        "/admin/", headers={"X-ExeDev-Email": "a@b.com"}, allow_redirects=False
    )
    assert response.status_code in (302, 307)
    assert "/__exe.dev/login" in response.headers["location"]
```

- [ ] **Step 2: Run to confirm failure**

```
uv run pytest tests/api/admin/test_deps.py -v
```

Expected: FAIL — routes don't exist yet.

- [ ] **Step 3: Create `src/api/admin/__init__.py`**

```python
"""Admin dashboard package."""
```

- [ ] **Step 4: Create `src/api/admin/router.py`**

```python
"""Admin router — mounts all entity sub-routers."""

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse

admin_router = APIRouter(prefix="/admin")


@admin_router.get("/")
async def dashboard(request: Request):
    user_id = request.headers.get("X-ExeDev-UserID")
    email = request.headers.get("X-ExeDev-Email")
    if not user_id or not email:
        return RedirectResponse(
            f"/__exe.dev/login?redirect=/admin/", status_code=307
        )
    return {"user": email}  # placeholder — replaced in Task 6
```

- [ ] **Step 5: Update `src/api/main.py`**

```python
"""FastAPI application entry point."""

import os
from contextlib import asynccontextmanager

import asyncpg
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from src.api.admin.router import admin_router
from src.core.logging import configure_logging

configure_logging()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Create and close the asyncpg connection pool."""
    dsn = os.environ.get("DATABASE_URL")
    if dsn:
        app.state.db_pool = await asyncpg.create_pool(dsn)
    else:
        app.state.db_pool = None
    yield
    if getattr(app.state, "db_pool", None):
        await app.state.db_pool.close()


app = FastAPI(title="power-map", version="0.1.0", lifespan=lifespan)

app.include_router(admin_router)

# Static files and templates — directories created in Task 5
# app.mount("/static/admin", StaticFiles(directory="src/static/admin"), name="admin-static")
templates = Jinja2Templates(directory="src/templates")
```

Note: `StaticFiles` mount is commented out until the directory exists (Task 5).

- [ ] **Step 6: Run tests**

```
uv run pytest tests/api/admin/test_deps.py -v
```

Expected: all 3 tests PASS.

- [ ] **Step 7: Run full suite**

```
uv run pytest -v
```

Expected: all existing tests still PASS.

- [ ] **Step 8: Commit**

```bash
git add src/api/main.py src/api/admin/__init__.py src/api/admin/router.py \
        tests/api/admin/__init__.py tests/api/admin/conftest.py tests/api/admin/test_deps.py
git commit -m "#6 feat: wire admin router, lifespan DB pool, auth redirect"
```

---

## Task 4: Auth dependency and DB dependency

**Files:**
- Create: `src/api/admin/deps.py`
- Modify: `tests/api/admin/test_deps.py`

- [ ] **Step 1: Write tests for AdminUser and get_db**

Add to `tests/api/admin/test_deps.py`:

```python
from src.api.admin.deps import AdminUser, get_admin_user


def test_admin_user_dataclass():
    user = AdminUser(id="usr123", email="a@b.com")
    assert user.id == "usr123"
    assert user.email == "a@b.com"


def test_get_admin_user_returns_user_when_headers_present():
    """get_admin_user must extract headers into AdminUser."""
    from unittest.mock import MagicMock

    request = MagicMock()
    request.headers = {
        "X-ExeDev-UserID": "usr_abc",
        "X-ExeDev-Email": "test@example.com",
    }
    request.url.path = "/admin/"

    import asyncio

    result = asyncio.get_event_loop().run_until_complete(get_admin_user(request))
    assert isinstance(result, AdminUser)
    assert result.id == "usr_abc"
    assert result.email == "test@example.com"
```

- [ ] **Step 2: Run to confirm failure**

```
uv run pytest tests/api/admin/test_deps.py::test_admin_user_dataclass -v
```

Expected: FAIL — `deps` module doesn't exist.

- [ ] **Step 3: Create `src/api/admin/deps.py`**

```python
"""Admin dashboard dependencies."""

from dataclasses import dataclass

import asyncpg
from fastapi import Request
from fastapi.responses import RedirectResponse


@dataclass
class AdminUser:
    """Authenticated exe.dev user."""

    id: str
    email: str


async def get_admin_user(request: Request) -> AdminUser | RedirectResponse:
    """Require exe.dev auth headers; redirect to login if absent."""
    user_id = request.headers.get("X-ExeDev-UserID")
    email = request.headers.get("X-ExeDev-Email")
    if not user_id or not email:
        path = request.url.path
        return RedirectResponse(
            f"/__exe.dev/login?redirect={path}", status_code=307
        )
    return AdminUser(id=user_id, email=email)


async def get_db(request: Request) -> asyncpg.Connection:
    """Yield a connection from the app-level asyncpg pool."""
    pool: asyncpg.Pool | None = getattr(request.app.state, "db_pool", None)
    if pool is None:
        raise RuntimeError("Database pool not initialized — is DATABASE_URL set?")
    async with pool.acquire() as conn:
        yield conn
```

- [ ] **Step 4: Run tests**

```
uv run pytest tests/api/admin/test_deps.py -v
```

Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/api/admin/deps.py tests/api/admin/test_deps.py
git commit -m "#6 feat: add AdminUser dataclass and auth/db dependencies"
```

---

## Task 5: CSS design system + base template

**Files:**
- Create: `src/static/admin/admin.css`
- Create: `src/templates/admin/base.html`
- Create: `src/templates/admin/partials/flash.html`
- Create: `src/templates/admin/partials/delete_modal.html`
- Modify: `src/api/main.py` (uncomment StaticFiles mount)

No automated tests for CSS. Template structure is tested via route responses in later tasks.

- [ ] **Step 1: Create directory structure**

```bash
mkdir -p src/static/admin src/templates/admin/partials \
         src/templates/admin/orgs src/templates/admin/people \
         src/templates/admin/roles src/templates/admin/role_assignments \
         src/templates/admin/lookups src/templates/admin/imports
```

- [ ] **Step 2: Create `src/static/admin/admin.css`**

```css
/* ==========================================================================
   Power-Map Admin — Design Token System
   ========================================================================== */

:root {
  /* Color — light mode */
  --color-brand:         #2563eb;
  --color-brand-hover:   #1d4ed8;
  --color-surface-0:     #f8fafc;  /* page background */
  --color-surface-1:     #ffffff;  /* card / panel */
  --color-surface-2:     #1e293b;  /* sidebar */
  --color-text:          #0f172a;
  --color-text-muted:    #64748b;
  --color-text-inverse:  #f1f5f9;
  --color-border:        #e2e8f0;
  --color-border-focus:  #2563eb;
  --color-success:       #16a34a;
  --color-warning:       #d97706;
  --color-danger:        #dc2626;
  --color-inactive:      #94a3b8;

  /* Spacing (4px base × 2 scale) */
  --space-1: 0.25rem;
  --space-2: 0.5rem;
  --space-3: 0.75rem;
  --space-4: 1rem;
  --space-5: 1.5rem;
  --space-6: 2rem;
  --space-7: 3rem;
  --space-8: 4rem;

  /* Shape */
  --radius-sm: 0.25rem;
  --radius-md: 0.375rem;
  --radius-lg: 0.5rem;

  /* Typography */
  --font-family-base: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto,
    Helvetica, Arial, sans-serif, "Apple Color Emoji";
  --font-size-sm:  0.8125rem;
  --font-size-md:  0.9375rem;
  --font-size-lg:  1.125rem;
  --font-size-xl:  1.375rem;
}

@media (prefers-color-scheme: dark) {
  :root {
    --color-brand:        #3b82f6;
    --color-brand-hover:  #60a5fa;
    --color-surface-0:    #0f172a;
    --color-surface-1:    #1e293b;
    --color-surface-2:    #0f172a;
    --color-text:         #f1f5f9;
    --color-text-muted:   #94a3b8;
    --color-text-inverse: #0f172a;
    --color-border:       #334155;
    --color-border-focus: #3b82f6;
    --color-success:      #4ade80;
    --color-warning:      #fbbf24;
    --color-danger:       #f87171;
    --color-inactive:     #475569;
  }
}

@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after {
    animation-duration: 0.01ms !important;
    transition-duration: 0.01ms !important;
  }
}

/* ==========================================================================
   Reset
   ========================================================================== */

*, *::before, *::after { box-sizing: border-box; }

body {
  margin: 0;
  font-family: var(--font-family-base);
  font-size: var(--font-size-md);
  color: var(--color-text);
  background: var(--color-surface-0);
  line-height: 1.5;
}

a { color: var(--color-brand); }
a:hover { color: var(--color-brand-hover); }

/* ==========================================================================
   Skip link
   ========================================================================== */

.skip-link {
  position: absolute;
  left: -9999px;
  top: var(--space-2);
  padding: var(--space-2) var(--space-4);
  background: var(--color-brand);
  color: #fff;
  border-radius: var(--radius-md);
  font-weight: 600;
  z-index: 9999;
  text-decoration: none;
}
.skip-link:focus { left: var(--space-2); }

/* ==========================================================================
   Layout
   ========================================================================== */

.admin-layout {
  display: grid;
  grid-template-columns: 240px 1fr;
  grid-template-rows: auto 1fr;
  min-height: 100vh;
}

.admin-topbar {
  grid-column: 1 / -1;
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: var(--space-3) var(--space-5);
  background: var(--color-surface-1);
  border-bottom: 1px solid var(--color-border);
  gap: var(--space-4);
}

.admin-topbar__brand {
  font-weight: 700;
  font-size: var(--font-size-lg);
  color: var(--color-text);
  text-decoration: none;
}

.admin-topbar__user {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  font-size: var(--font-size-sm);
  color: var(--color-text-muted);
}

.admin-sidebar {
  background: var(--color-surface-2);
  color: var(--color-text-inverse);
  padding: var(--space-5) 0;
  overflow-y: auto;
}

.admin-sidebar__group-label {
  padding: var(--space-2) var(--space-5);
  font-size: var(--font-size-sm);
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.08em;
  color: var(--color-inactive);
  margin-top: var(--space-4);
}

.admin-sidebar__link {
  display: block;
  padding: var(--space-2) var(--space-5);
  color: #cbd5e1;
  text-decoration: none;
  font-size: var(--font-size-sm);
  border-left: 3px solid transparent;
  transition: background 0.15s, border-color 0.15s;
}

.admin-sidebar__link:hover,
.admin-sidebar__link[aria-current="page"] {
  background: rgba(255,255,255,0.08);
  color: #fff;
  border-left-color: var(--color-brand);
}

.admin-main {
  padding: var(--space-6);
  overflow-y: auto;
}

/* Responsive: stack on narrow viewports */
@media (max-width: 768px) {
  .admin-layout {
    grid-template-columns: 1fr;
  }
  .admin-sidebar {
    display: none; /* TODO: implement hamburger toggle */
  }
}

/* RTL support */
[dir="rtl"] .admin-layout {
  grid-template-columns: 1fr 240px;
}
[dir="rtl"] .admin-sidebar__link {
  border-left: none;
  border-right: 3px solid transparent;
}
[dir="rtl"] .admin-sidebar__link:hover,
[dir="rtl"] .admin-sidebar__link[aria-current="page"] {
  border-right-color: var(--color-brand);
}

/* ==========================================================================
   Breadcrumb
   ========================================================================== */

.breadcrumb {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  font-size: var(--font-size-sm);
  color: var(--color-text-muted);
  margin-bottom: var(--space-5);
  flex-wrap: wrap;
}

.breadcrumb a { color: var(--color-text-muted); }
.breadcrumb__sep { color: var(--color-border); }

/* ==========================================================================
   Page header
   ========================================================================== */

.page-header {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: var(--space-4);
  margin-bottom: var(--space-5);
  flex-wrap: wrap;
}

.page-header h1 {
  margin: 0;
  font-size: var(--font-size-xl);
  font-weight: 700;
}

/* ==========================================================================
   Buttons
   ========================================================================== */

.btn {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  padding: var(--space-2) var(--space-4);
  font-size: var(--font-size-sm);
  font-weight: 500;
  border-radius: var(--radius-md);
  border: 1px solid transparent;
  cursor: pointer;
  text-decoration: none;
  transition: background 0.15s, border-color 0.15s;
  white-space: nowrap;
}

.btn:focus-visible {
  outline: 2px solid var(--color-border-focus);
  outline-offset: 2px;
}

.btn--primary {
  background: var(--color-brand);
  color: #fff;
  border-color: var(--color-brand);
}
.btn--primary:hover { background: var(--color-brand-hover); border-color: var(--color-brand-hover); color: #fff; }

.btn--ghost {
  background: transparent;
  color: var(--color-text);
  border-color: var(--color-border);
}
.btn--ghost:hover { background: var(--color-surface-0); color: var(--color-text); }

.btn--danger {
  background: var(--color-danger);
  color: #fff;
  border-color: var(--color-danger);
}
.btn--danger:hover { opacity: 0.88; color: #fff; }

.btn--sm {
  padding: var(--space-1) var(--space-3);
}

/* ==========================================================================
   Badges
   ========================================================================== */

.badge {
  display: inline-block;
  padding: 0.125rem var(--space-2);
  border-radius: var(--radius-sm);
  font-size: 0.75rem;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.04em;
}

.badge--active   { background: #dcfce7; color: #15803d; }
.badge--inactive { background: #f1f5f9; color: var(--color-inactive); }
.badge--archived { background: #fee2e2; color: #991b1b; }

@media (prefers-color-scheme: dark) {
  .badge--active   { background: #14532d; color: #86efac; }
  .badge--inactive { background: #1e293b; color: var(--color-inactive); }
  .badge--archived { background: #450a0a; color: #fca5a5; }
}

/* ==========================================================================
   Data table
   ========================================================================== */

.table-wrapper {
  overflow-x: auto;
  border-radius: var(--radius-lg);
  border: 1px solid var(--color-border);
}

.data-table {
  width: 100%;
  border-collapse: collapse;
  font-size: var(--font-size-sm);
  background: var(--color-surface-1);
}

.data-table caption {
  text-align: start;
  padding: var(--space-3) var(--space-4);
  font-weight: 600;
  font-size: var(--font-size-md);
  border-bottom: 1px solid var(--color-border);
}

.data-table th {
  padding: var(--space-3) var(--space-4);
  text-align: start;
  font-size: var(--font-size-sm);
  font-weight: 600;
  color: var(--color-text-muted);
  border-bottom: 1px solid var(--color-border);
  white-space: nowrap;
}

.data-table td {
  padding: var(--space-3) var(--space-4);
  border-bottom: 1px solid var(--color-border);
  vertical-align: middle;
}

.data-table tbody tr:last-child td { border-bottom: none; }
.data-table tbody tr:hover { background: var(--color-surface-0); }

/* Archived rows */
.data-table tr.is-archived td { color: var(--color-text-muted); text-decoration: line-through; }
.data-table tr.is-inactive td:first-child { color: var(--color-inactive); }

/* ==========================================================================
   Entity card (detail view)
   ========================================================================== */

.entity-card {
  background: var(--color-surface-1);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  padding: var(--space-5);
  margin-bottom: var(--space-5);
}

.entity-card__title {
  margin: 0 0 var(--space-4);
  font-size: var(--font-size-lg);
  font-weight: 700;
}

.entity-card__row {
  display: flex;
  gap: var(--space-4);
  padding: var(--space-2) 0;
  border-bottom: 1px solid var(--color-border);
  align-items: baseline;
}

.entity-card__row:last-child { border-bottom: none; }

.entity-card__label {
  min-width: 160px;
  font-size: var(--font-size-sm);
  font-weight: 600;
  color: var(--color-text-muted);
  flex-shrink: 0;
}

/* ==========================================================================
   Section header (within detail view)
   ========================================================================== */

.section-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin: var(--space-6) 0 var(--space-3);
}

.section-header h2 {
  margin: 0;
  font-size: var(--font-size-md);
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  color: var(--color-text-muted);
}

/* ==========================================================================
   Forms
   ========================================================================== */

.form-group {
  margin-bottom: var(--space-4);
}

.form-group label {
  display: block;
  font-size: var(--font-size-sm);
  font-weight: 600;
  margin-bottom: var(--space-2);
  color: var(--color-text);
}

.form-group input,
.form-group select,
.form-group textarea {
  width: 100%;
  padding: var(--space-2) var(--space-3);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  font-family: inherit;
  font-size: var(--font-size-md);
  color: var(--color-text);
  background: var(--color-surface-1);
  transition: border-color 0.15s;
}

.form-group input:focus,
.form-group select:focus,
.form-group textarea:focus {
  outline: none;
  border-color: var(--color-border-focus);
  box-shadow: 0 0 0 3px rgba(37, 99, 235, 0.15);
}

.form-group__error {
  margin-top: var(--space-1);
  font-size: var(--font-size-sm);
  color: var(--color-danger);
}

.form-group__hint {
  margin-top: var(--space-1);
  font-size: var(--font-size-sm);
  color: var(--color-text-muted);
}

.form-actions {
  display: flex;
  gap: var(--space-3);
  margin-top: var(--space-5);
  align-items: center;
}

/* ==========================================================================
   Search bar
   ========================================================================== */

.search-bar {
  display: flex;
  gap: var(--space-3);
  margin-bottom: var(--space-4);
  align-items: center;
  flex-wrap: wrap;
}

.search-bar input {
  padding: var(--space-2) var(--space-3);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  font-family: inherit;
  font-size: var(--font-size-sm);
  color: var(--color-text);
  background: var(--color-surface-1);
  min-width: 240px;
}

.search-bar input:focus {
  outline: none;
  border-color: var(--color-border-focus);
}

/* ==========================================================================
   Flash messages (injected via hx-swap-oob)
   ========================================================================== */

.flash-region {
  position: fixed;
  top: var(--space-5);
  right: var(--space-5);
  z-index: 1000;
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
  max-width: 360px;
}

[dir="rtl"] .flash-region { right: auto; left: var(--space-5); }

.alert {
  padding: var(--space-3) var(--space-4);
  border-radius: var(--radius-md);
  font-size: var(--font-size-sm);
  font-weight: 500;
  border: 1px solid transparent;
}

.alert--success {
  background: #f0fdf4;
  color: #15803d;
  border-color: #bbf7d0;
}

.alert--error {
  background: #fef2f2;
  color: #991b1b;
  border-color: #fecaca;
}

@media (prefers-color-scheme: dark) {
  .alert--success { background: #14532d; color: #86efac; border-color: #166534; }
  .alert--error   { background: #450a0a; color: #fca5a5; border-color: #991b1b; }
}

/* ==========================================================================
   Modal (hard-delete confirmation)
   ========================================================================== */

.modal-backdrop {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.5);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 500;
}

.modal {
  background: var(--color-surface-1);
  border-radius: var(--radius-lg);
  padding: var(--space-6);
  max-width: 440px;
  width: 100%;
  border: 1px solid var(--color-border);
}

.modal h2 {
  margin: 0 0 var(--space-3);
  font-size: var(--font-size-lg);
}

.modal p {
  color: var(--color-text-muted);
  margin: 0 0 var(--space-4);
}

.modal__actions {
  display: flex;
  gap: var(--space-3);
  justify-content: flex-end;
}

/* ==========================================================================
   Pagination
   ========================================================================== */

.pagination {
  display: flex;
  gap: var(--space-2);
  align-items: center;
  margin-top: var(--space-4);
  justify-content: flex-end;
  font-size: var(--font-size-sm);
}

.pagination__info {
  color: var(--color-text-muted);
  margin-right: var(--space-3);
}

/* ==========================================================================
   Action row (archive / delete zone in detail view)
   ========================================================================== */

.danger-zone {
  border: 1px solid var(--color-danger);
  border-radius: var(--radius-lg);
  padding: var(--space-4) var(--space-5);
  margin-top: var(--space-6);
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-4);
  flex-wrap: wrap;
}

.danger-zone__label {
  font-weight: 600;
  font-size: var(--font-size-sm);
  color: var(--color-danger);
}

.danger-zone__desc {
  font-size: var(--font-size-sm);
  color: var(--color-text-muted);
  margin-top: var(--space-1);
}
```

- [ ] **Step 3: Create `src/templates/admin/base.html`**

```html
<!doctype html>
<html lang="en" dir="ltr">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{% block title %}Admin{% endblock %} — power-map</title>
  <link rel="stylesheet" href="/static/admin/admin.css">
  <script src="https://unpkg.com/htmx.org@1.9.12" defer></script>
</head>
<body>
  <a class="skip-link" href="#main-content">Skip to main content</a>

  <div class="admin-layout" hx-boost="true">
    <header class="admin-topbar">
      <a class="admin-topbar__brand" href="/admin/">power-map admin</a>
      <nav aria-label="Breadcrumb" class="breadcrumb">
        {% block breadcrumb %}{% endblock %}
      </nav>
      <div class="admin-topbar__user">
        <span>{{ user.email }}</span>
        <form method="POST" action="/__exe.dev/logout" style="margin:0">
          <button type="submit" class="btn btn--ghost btn--sm">Log out</button>
        </form>
      </div>
    </header>

    <nav class="admin-sidebar" aria-label="Entity navigation">
      <span class="admin-sidebar__group-label">Entities</span>
      <a class="admin-sidebar__link" href="/admin/people/"
         {% if active_section == 'people' %}aria-current="page"{% endif %}>People</a>
      <a class="admin-sidebar__link" href="/admin/orgs/"
         {% if active_section == 'orgs' %}aria-current="page"{% endif %}>Organizations</a>
      <a class="admin-sidebar__link" href="/admin/roles/"
         {% if active_section == 'roles' %}aria-current="page"{% endif %}>Roles</a>
      <a class="admin-sidebar__link" href="/admin/role-assignments/"
         {% if active_section == 'role_assignments' %}aria-current="page"{% endif %}>Assignments</a>

      <span class="admin-sidebar__group-label">Reference</span>
      <a class="admin-sidebar__link" href="/admin/lookups/platforms/"
         {% if active_section == 'lookups' %}aria-current="page"{% endif %}>Lookups</a>

      <span class="admin-sidebar__group-label">Ingestion</span>
      <a class="admin-sidebar__link" href="/admin/imports/"
         {% if active_section == 'imports' %}aria-current="page"{% endif %}>Import History</a>
    </nav>

    <main id="main-content" class="admin-main">
      {% block content %}{% endblock %}
    </main>
  </div>

  {# Flash message region — updated via hx-swap-oob #}
  <div id="flash-region" class="flash-region" aria-live="polite" aria-atomic="false">
    {% block flash %}{% endblock %}
  </div>
</body>
</html>
```

- [ ] **Step 4: Create `src/templates/admin/partials/flash.html`**

```html
{# Injected via hx-swap-oob="afterbegin:#flash-region" on every mutating response #}
<div class="alert alert--{{ level }}" role="alert">{{ message }}</div>
```

- [ ] **Step 5: Create `src/templates/admin/partials/delete_modal.html`**

```html
{# Hard-delete confirmation modal. Include via hx-get + hx-target="#modal-slot" #}
<div class="modal-backdrop" id="modal-slot">
  <div class="modal" role="dialog" aria-modal="true" aria-labelledby="modal-title">
    <h2 id="modal-title">Permanently delete?</h2>
    <p>
      This will permanently remove <strong>{{ entity_label }}</strong> and cannot be undone.
      This record must be archived before it can be deleted.
    </p>
    <div class="modal__actions">
      <button class="btn btn--ghost"
              onclick="document.getElementById('modal-slot').remove()">
        Cancel
      </button>
      <button class="btn btn--danger"
              hx-delete="{{ delete_url }}"
              hx-target="closest tr"
              hx-swap="outerHTML"
              hx-on::after-request="document.getElementById('modal-slot').remove()">
        Delete permanently
      </button>
    </div>
  </div>
</div>
```

- [ ] **Step 6: Uncomment StaticFiles in `src/api/main.py`**

```python
app.mount("/static/admin", StaticFiles(directory="src/static/admin"), name="admin-static")
```

- [ ] **Step 7: Run tests**

```
uv run pytest -v
```

Expected: all existing tests PASS (no HTML tests yet).

- [ ] **Step 8: Commit**

```bash
git add src/static/admin/admin.css src/templates/admin/ src/api/main.py
git commit -m "#6 feat: add CSS design system and base template"
```

---

## Task 6: Dashboard landing page

**Files:**
- Create: `src/templates/admin/dashboard.html`
- Modify: `src/api/admin/router.py`
- Modify: `tests/api/admin/test_deps.py`

- [ ] **Step 1: Write failing test**

Add to `tests/api/admin/test_deps.py`:

```python
from tests.api.admin.conftest import AUTH_HEADERS


def test_admin_dashboard_returns_200_when_authenticated():
    response = _client.get("/admin/", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert "power-map admin" in response.text.lower()
```

- [ ] **Step 2: Run to confirm failure**

```
uv run pytest tests/api/admin/test_deps.py::test_admin_dashboard_returns_200_when_authenticated -v
```

Expected: FAIL — route returns JSON dict, not HTML.

- [ ] **Step 3: Create `src/templates/admin/dashboard.html`**

```html
{% extends "admin/base.html" %}

{% block title %}Dashboard{% endblock %}

{% block breadcrumb %}
  <span>Dashboard</span>
{% endblock %}

{% block content %}
<div class="page-header">
  <h1>Dashboard</h1>
</div>

<div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:var(--space-4)">
  {% for item in nav_items %}
  <a href="{{ item.url }}" style="
    display:block;padding:var(--space-4);
    background:var(--color-surface-1);
    border:1px solid var(--color-border);
    border-radius:var(--radius-lg);
    text-decoration:none;color:var(--color-text);
    font-weight:600;
  ">
    {{ item.label }}
    <span style="display:block;font-size:var(--font-size-sm);color:var(--color-text-muted);font-weight:400;margin-top:var(--space-1)">
      {{ item.count }} records
    </span>
  </a>
  {% endfor %}
</div>
{% endblock %}
```

- [ ] **Step 4: Update `src/api/admin/router.py`**

```python
"""Admin router — mounts all entity sub-routers."""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from src.api.admin.deps import AdminUser, get_admin_user, get_db

templates = Jinja2Templates(directory="src/templates")

admin_router = APIRouter(prefix="/admin")


@admin_router.get("/")
async def dashboard(
    request: Request,
    user: AdminUser | RedirectResponse = Depends(get_admin_user),
):
    if isinstance(user, RedirectResponse):
        return user
    return templates.TemplateResponse(
        request,
        "admin/dashboard.html",
        {
            "user": user,
            "active_section": "dashboard",
            "nav_items": [
                {"label": "People", "url": "/admin/people/", "count": "—"},
                {"label": "Organizations", "url": "/admin/orgs/", "count": "—"},
                {"label": "Roles", "url": "/admin/roles/", "count": "—"},
                {"label": "Assignments", "url": "/admin/role-assignments/", "count": "—"},
                {"label": "Import History", "url": "/admin/imports/", "count": "—"},
            ],
        },
    )
```

Note: counts will be wired to real DB queries in a follow-up; placeholder `"—"` avoids DB dependency on the dashboard for now.

- [ ] **Step 5: Run tests**

```
uv run pytest tests/api/admin/test_deps.py -v
```

Expected: all tests PASS.

- [ ] **Step 6: Commit**

```bash
git add src/templates/admin/dashboard.html src/api/admin/router.py tests/api/admin/test_deps.py
git commit -m "#6 feat: add admin dashboard landing page"
```

---

## Task 7: Organizations — list and detail

**Files:**
- Create: `src/api/admin/orgs.py`
- Create: `src/templates/admin/orgs/list.html`
- Create: `src/templates/admin/orgs/detail.html`
- Create: `tests/api/admin/test_orgs.py`
- Modify: `src/api/admin/router.py` (include orgs router)

- [ ] **Step 1: Write failing integration tests**

Create `tests/api/admin/test_orgs.py`:

```python
"""Integration tests for admin organizations views.

Requires DATABASE_URL. Run with:
    DATABASE_URL=<dsn> uv run pytest tests/api/admin/test_orgs.py -m integration -v
"""

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
def client():
    with TestClient(app) as c:  # triggers lifespan (DB pool)
        yield c


@pytest.fixture
async def org_id(db):
    oid = generate_id()
    await db.execute("INSERT INTO organizations (id) VALUES ($1)", oid)
    await db.execute(
        "INSERT INTO organization_names (id, organization_id, name, is_canonical)"
        " VALUES ($1, $2, 'Test Org', TRUE)",
        generate_id(), oid,
    )
    return oid


def test_orgs_list_returns_200(client):
    response = client.get("/admin/orgs/", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert "organizations" in response.text.lower()


def test_orgs_list_redirects_unauthenticated(client):
    response = client.get("/admin/orgs/", allow_redirects=False)
    assert response.status_code in (302, 307)
    assert "/__exe.dev/login" in response.headers["location"]


def test_org_detail_returns_200(client, org_id):
    response = client.get(f"/admin/orgs/{org_id}/", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert "Test Org" in response.text


def test_org_detail_404_for_unknown_id(client):
    response = client.get(f"/admin/orgs/{generate_id()}/", headers=AUTH_HEADERS)
    assert response.status_code == 404
```

- [ ] **Step 2: Run to confirm failure**

```
DATABASE_URL=<dsn> uv run pytest tests/api/admin/test_orgs.py -m integration -v
```

Expected: FAIL — routes don't exist.

- [ ] **Step 3: Create `src/api/admin/orgs.py`**

```python
"""Admin views for organizations."""

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from src.api.admin.deps import AdminUser, get_admin_user, get_db
from src.core.db import generate_id

templates = Jinja2Templates(directory="src/templates")
router = APIRouter(prefix="/orgs", tags=["admin-orgs"])

PAGE_SIZE = 50


def _require_user(user):
    """Return redirect if user is RedirectResponse, else the AdminUser."""
    if isinstance(user, RedirectResponse):
        return user, None
    return None, user


@router.get("/")
async def orgs_list(
    request: Request,
    q: str = "",
    status: str = "active",
    page: int = 1,
    user: AdminUser | RedirectResponse = Depends(get_admin_user),
    db=Depends(get_db),
):
    redirect, user = _require_user(user)
    if redirect:
        return redirect

    offset = (page - 1) * PAGE_SIZE
    filters = []
    params = []

    if status == "active":
        filters.append("o.archived_at IS NULL")
    elif status == "archived":
        filters.append("o.archived_at IS NOT NULL")
    elif status == "inactive":
        filters.append("o.archived_at IS NULL AND o.active = FALSE")

    if q:
        params.append(f"%{q}%")
        filters.append(f"n.name ILIKE ${len(params)}")

    where = ("WHERE " + " AND ".join(filters)) if filters else ""
    params_for_count = params[:]
    params_for_list = params + [PAGE_SIZE, offset]

    count = await db.fetchval(
        f"""SELECT count(DISTINCT o.id)
            FROM organizations o
            LEFT JOIN organization_names n
              ON n.organization_id = o.id AND n.is_canonical = TRUE
            {where}""",
        *params_for_count,
    )

    rows = await db.fetch(
        f"""SELECT o.id, o.active, o.archived_at,
                   n.name AS canonical_name,
                   o.created_at
            FROM organizations o
            LEFT JOIN organization_names n
              ON n.organization_id = o.id AND n.is_canonical = TRUE
            {where}
            ORDER BY n.name NULLS LAST
            LIMIT ${len(params_for_list) - 1} OFFSET ${len(params_for_list)}""",
        *params_for_list,
    )

    return templates.TemplateResponse(
        request,
        "admin/orgs/list.html",
        {
            "user": user,
            "active_section": "orgs",
            "orgs": rows,
            "q": q,
            "status": status,
            "page": page,
            "page_size": PAGE_SIZE,
            "total": count,
        },
    )


@router.get("/{org_id}/")
async def org_detail(
    org_id: str,
    request: Request,
    user: AdminUser | RedirectResponse = Depends(get_admin_user),
    db=Depends(get_db),
):
    redirect, user = _require_user(user)
    if redirect:
        return redirect

    org = await db.fetchrow(
        "SELECT * FROM organizations WHERE id = $1", org_id
    )
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")

    names = await db.fetch(
        "SELECT * FROM organization_names WHERE organization_id = $1 ORDER BY is_canonical DESC",
        org_id,
    )
    addresses = await db.fetch(
        """SELECT ea.*, a.standardized, a.address_line_1, a.city, a.region, a.postal_code
           FROM entity_addresses ea JOIN addresses a ON a.id = ea.address_id
           WHERE ea.entity_type = 'organization' AND ea.entity_id = $1""",
        org_id,
    )
    contacts = await db.fetch(
        "SELECT * FROM contact_methods WHERE entity_type = 'organization' AND entity_id = $1",
        org_id,
    )
    urls = await db.fetch(
        """SELECT u.*, ut.display_name AS url_type_name
           FROM urls u JOIN url_types ut ON ut.id = u.url_type_id
           WHERE u.entity_type = 'organization' AND u.entity_id = $1""",
        org_id,
    )
    social = await db.fetch(
        """SELECT sl.*, p.display_name AS platform_name
           FROM social_links sl JOIN platforms p ON p.id = sl.platform_id
           WHERE sl.entity_type = 'organization' AND sl.entity_id = $1""",
        org_id,
    )
    identifiers = await db.fetch(
        """SELECT i.*, eit.display_name AS type_name, eit.full_name AS type_full_name
           FROM identifiers i
           JOIN entity_identifier_types eit ON eit.id = i.entity_identifier_type_id
           WHERE i.entity_id = $1""",
        org_id,
    )
    children = await db.fetch(
        """SELECT o.id, n.name AS canonical_name, o.active, o.archived_at
           FROM organizations o
           LEFT JOIN organization_names n
             ON n.organization_id = o.id AND n.is_canonical = TRUE
           WHERE o.parent_id = $1
           ORDER BY n.name""",
        org_id,
    )
    roles = await db.fetch(
        "SELECT * FROM roles WHERE organization_id = $1 ORDER BY title",
        org_id,
    )

    return templates.TemplateResponse(
        request,
        "admin/orgs/detail.html",
        {
            "user": user,
            "active_section": "orgs",
            "org": org,
            "names": names,
            "addresses": addresses,
            "contacts": contacts,
            "urls": urls,
            "social": social,
            "identifiers": identifiers,
            "children": children,
            "roles": roles,
        },
    )
```

- [ ] **Step 4: Create `src/templates/admin/orgs/list.html`**

```html
{% extends "admin/base.html" %}
{% block title %}Organizations{% endblock %}

{% block breadcrumb %}
  <a href="/admin/">Dashboard</a>
  <span class="breadcrumb__sep">›</span>
  <span>Organizations</span>
{% endblock %}

{% block content %}
<div class="page-header">
  <h1>Organizations</h1>
  <a href="/admin/orgs/new/" class="btn btn--primary">+ Add organization</a>
</div>

<div class="search-bar">
  <input
    type="search"
    name="q"
    value="{{ q }}"
    placeholder="Search by name…"
    aria-label="Search organizations"
    hx-get="/admin/orgs/"
    hx-trigger="input delay:300ms, search"
    hx-target="#orgs-table-body"
    hx-include="[name='status']"
    hx-push-url="true"
  >
  <select name="status"
          aria-label="Filter by status"
          hx-get="/admin/orgs/"
          hx-trigger="change"
          hx-target="#orgs-table-body"
          hx-include="[name='q']"
          hx-push-url="true">
    <option value="active"   {% if status=='active'   %}selected{% endif %}>Active</option>
    <option value="inactive" {% if status=='inactive' %}selected{% endif %}>Inactive</option>
    <option value="archived" {% if status=='archived' %}selected{% endif %}>Archived</option>
  </select>
</div>

<div class="table-wrapper">
  <table class="data-table" id="orgs-table">
    <caption>Organizations — {{ total }} record{{ 's' if total != 1 else '' }}</caption>
    <thead>
      <tr>
        <th scope="col">Name</th>
        <th scope="col">Status</th>
        <th scope="col">Created</th>
        <th scope="col"><span class="sr-only">Actions</span></th>
      </tr>
    </thead>
    <tbody id="orgs-table-body">
      {% include "admin/orgs/_rows.html" %}
    </tbody>
  </table>
</div>
{% endblock %}
```

Create `src/templates/admin/orgs/_rows.html` (the HTMX partial):

```html
{% for org in orgs %}
<tr class="{% if org.archived_at %}is-archived{% elif not org.active %}is-inactive{% endif %}">
  <td>
    <a href="/admin/orgs/{{ org.id }}/">
      {{ org.canonical_name or '(unnamed)' }}
    </a>
  </td>
  <td>
    {% if org.archived_at %}
      <span class="badge badge--archived">Archived</span>
    {% elif not org.active %}
      <span class="badge badge--inactive">Inactive</span>
    {% else %}
      <span class="badge badge--active">Active</span>
    {% endif %}
  </td>
  <td>{{ org.created_at.strftime('%Y-%m-%d') }}</td>
  <td style="white-space:nowrap">
    <a href="/admin/orgs/{{ org.id }}/edit/" class="btn btn--ghost btn--sm">Edit</a>
  </td>
</tr>
{% else %}
<tr><td colspan="4" style="text-align:center;color:var(--color-text-muted)">No results</td></tr>
{% endfor %}
```

- [ ] **Step 5: Create `src/templates/admin/orgs/detail.html`**

```html
{% extends "admin/base.html" %}
{% block title %}{{ org.canonical_name or org.id }}{% endblock %}

{% block breadcrumb %}
  <a href="/admin/">Dashboard</a>
  <span class="breadcrumb__sep">›</span>
  <a href="/admin/orgs/">Organizations</a>
  <span class="breadcrumb__sep">›</span>
  <span>{{ org.canonical_name or org.id }}</span>
{% endblock %}

{% block content %}
<div class="page-header">
  <h1>
    {{ org.canonical_name or '(unnamed)' }}
    {% if org.archived_at %}
      <span class="badge badge--archived">Archived</span>
    {% elif not org.active %}
      <span class="badge badge--inactive">Inactive</span>
    {% else %}
      <span class="badge badge--active">Active</span>
    {% endif %}
  </h1>
  <a href="/admin/orgs/{{ org.id }}/edit/" class="btn btn--primary">Edit</a>
</div>

<div class="entity-card">
  <div class="entity-card__row">
    <span class="entity-card__label">ID</span>
    <code>{{ org.id }}</code>
  </div>
  <div class="entity-card__row">
    <span class="entity-card__label">Active</span>
    {{ 'Yes' if org.active else 'No' }}
  </div>
  <div class="entity-card__row">
    <span class="entity-card__label">Notes</span>
    {{ org.notes or '—' }}
  </div>
  <div class="entity-card__row">
    <span class="entity-card__label">Created</span>
    {{ org.created_at.strftime('%Y-%m-%dT%H:%M:%SZ') }}
  </div>
  <div class="entity-card__row">
    <span class="entity-card__label">Updated</span>
    {{ org.updated_at.strftime('%Y-%m-%dT%H:%M:%SZ') }}
  </div>
  {% if org.archived_at %}
  <div class="entity-card__row">
    <span class="entity-card__label">Archived</span>
    {{ org.archived_at.strftime('%Y-%m-%dT%H:%M:%SZ') }}
  </div>
  {% endif %}
</div>

<div class="section-header"><h2>Names</h2></div>
<div class="table-wrapper">
  <table class="data-table">
    <thead><tr><th>Name</th><th>Type</th><th>Canonical</th></tr></thead>
    <tbody>
      {% for n in names %}
      <tr>
        <td>{{ n.name }}</td>
        <td>{{ n.name_type }}</td>
        <td>{{ '✓' if n.is_canonical else '' }}</td>
      </tr>
      {% else %}
      <tr><td colspan="3">—</td></tr>
      {% endfor %}
    </tbody>
  </table>
</div>

<div class="section-header"><h2>Addresses</h2></div>
<div class="table-wrapper">
  <table class="data-table">
    <thead><tr><th>Type</th><th>Label</th><th>Address</th></tr></thead>
    <tbody>
      {% for a in addresses %}
      <tr>
        <td>{{ a.address_type }}</td>
        <td>{{ a.display_name or '—' }}</td>
        <td>{{ a.standardized or a.address_line_1 or '—' }}</td>
      </tr>
      {% else %}
      <tr><td colspan="3">—</td></tr>
      {% endfor %}
    </tbody>
  </table>
</div>

<div class="section-header"><h2>Contact Methods</h2></div>
<div class="table-wrapper">
  <table class="data-table">
    <thead><tr><th>Type</th><th>Value</th><th>Label</th></tr></thead>
    <tbody>
      {% for c in contacts %}
      <tr>
        <td>{{ c.contact_type }}</td>
        <td>{{ c.value }}</td>
        <td>{{ c.display_label or '—' }}</td>
      </tr>
      {% else %}
      <tr><td colspan="3">—</td></tr>
      {% endfor %}
    </tbody>
  </table>
</div>

<div class="section-header"><h2>URLs</h2></div>
<div class="table-wrapper">
  <table class="data-table">
    <thead><tr><th>Type</th><th>URL</th><th>Canonical</th></tr></thead>
    <tbody>
      {% for u in urls %}
      <tr>
        <td>{{ u.url_type_name }}</td>
        <td><a href="{{ u.url }}" target="_blank" rel="noopener">{{ u.url }}</a></td>
        <td>{{ '✓' if u.is_canonical else '' }}</td>
      </tr>
      {% else %}
      <tr><td colspan="3">—</td></tr>
      {% endfor %}
    </tbody>
  </table>
</div>

<div class="section-header"><h2>Social Links</h2></div>
<div class="table-wrapper">
  <table class="data-table">
    <thead><tr><th>Platform</th><th>URL</th></tr></thead>
    <tbody>
      {% for s in social %}
      <tr>
        <td>{{ s.platform_name }}</td>
        <td><a href="{{ s.url }}" target="_blank" rel="noopener">{{ s.url }}</a></td>
      </tr>
      {% else %}
      <tr><td colspan="2">—</td></tr>
      {% endfor %}
    </tbody>
  </table>
</div>

<div class="section-header"><h2>Identifiers</h2></div>
<div class="table-wrapper">
  <table class="data-table">
    <thead><tr><th>Type</th><th>Value</th></tr></thead>
    <tbody>
      {% for i in identifiers %}
      <tr>
        <td title="{{ i.type_full_name }}">{{ i.type_name }}</td>
        <td>{{ i.value }}</td>
      </tr>
      {% else %}
      <tr><td colspan="2">—</td></tr>
      {% endfor %}
    </tbody>
  </table>
</div>

<div class="section-header"><h2>Child Organizations</h2></div>
<div class="table-wrapper">
  <table class="data-table">
    <thead><tr><th>Name</th><th>Status</th></tr></thead>
    <tbody>
      {% for c in children %}
      <tr>
        <td><a href="/admin/orgs/{{ c.id }}/">{{ c.canonical_name or '(unnamed)' }}</a></td>
        <td>
          {% if c.archived_at %}<span class="badge badge--archived">Archived</span>
          {% elif not c.active %}<span class="badge badge--inactive">Inactive</span>
          {% else %}<span class="badge badge--active">Active</span>{% endif %}
        </td>
      </tr>
      {% else %}
      <tr><td colspan="2">—</td></tr>
      {% endfor %}
    </tbody>
  </table>
</div>

<div class="section-header"><h2>Roles</h2></div>
<div class="table-wrapper">
  <table class="data-table">
    <thead><tr><th>Title</th><th>Notes</th></tr></thead>
    <tbody>
      {% for r in roles %}
      <tr>
        <td><a href="/admin/roles/{{ r.id }}/">{{ r.title }}</a></td>
        <td>{{ r.notes or '—' }}</td>
      </tr>
      {% else %}
      <tr><td colspan="2">—</td></tr>
      {% endfor %}
    </tbody>
  </table>
</div>

{# Archive / Delete zone #}
<div class="danger-zone">
  {% if not org.archived_at %}
  <div>
    <div class="danger-zone__label">Archive this organization</div>
    <div class="danger-zone__desc">
      Hides from active views. Required before permanent deletion.
    </div>
  </div>
  <button class="btn btn--danger"
          hx-post="/admin/orgs/{{ org.id }}/archive/"
          hx-confirm="Archive {{ org.canonical_name or org.id }}?">
    Archive
  </button>
  {% else %}
  <div>
    <div class="danger-zone__label">Delete permanently</div>
    <div class="danger-zone__desc">This cannot be undone.</div>
  </div>
  <button class="btn btn--danger"
          hx-delete="/admin/orgs/{{ org.id }}/"
          hx-confirm="Permanently delete {{ org.canonical_name or org.id }}? This cannot be undone.">
    Delete permanently
  </button>
  {% endif %}
</div>
{% endblock %}
```

- [ ] **Step 6: Mount orgs router**

In `src/api/admin/router.py`, add:

```python
from src.api.admin import orgs as orgs_module
admin_router.include_router(orgs_module.router)
```

- [ ] **Step 7: Run integration tests**

```
DATABASE_URL=<dsn> uv run pytest tests/api/admin/test_orgs.py -m integration -v
```

Expected: all 4 tests PASS.

- [ ] **Step 8: Commit**

```bash
git add src/api/admin/orgs.py src/templates/admin/orgs/ \
        src/api/admin/router.py tests/api/admin/test_orgs.py
git commit -m "#6 feat: add organizations list and detail views"
```

---

## Task 8: Organizations — create, edit, archive, hard delete

**Files:**
- Modify: `src/api/admin/orgs.py`
- Create: `src/templates/admin/orgs/form.html`
- Modify: `tests/api/admin/test_orgs.py`

- [ ] **Step 1: Add integration tests**

Append to `tests/api/admin/test_orgs.py`:

```python
def test_create_org_form_returns_200(client):
    response = client.get("/admin/orgs/new/", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert "form" in response.text.lower()


def test_create_org_post_redirects_on_success(client):
    response = client.post(
        "/admin/orgs/new/",
        headers=AUTH_HEADERS,
        data={"name": "Test Create Org", "active": "true"},
        follow_redirects=False,
    )
    assert response.status_code in (302, 303)
    assert "/admin/orgs/" in response.headers["location"]


def test_edit_org_form_returns_200(client, org_id):
    response = client.get(f"/admin/orgs/{org_id}/edit/", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert "Test Org" in response.text


def test_archive_org(client, org_id):
    response = client.post(
        f"/admin/orgs/{org_id}/archive/",
        headers=AUTH_HEADERS,
        follow_redirects=False,
    )
    assert response.status_code in (302, 303)


def test_hard_delete_requires_archive_first(client, org_id):
    """DELETE on a non-archived org must return 409."""
    response = client.delete(
        f"/admin/orgs/{org_id}/",
        headers=AUTH_HEADERS,
    )
    assert response.status_code == 409


def test_hard_delete_archived_org(client, db, org_id):
    """DELETE on an archived org must succeed and remove the row."""
    import asyncio
    asyncio.get_event_loop().run_until_complete(
        db.execute("UPDATE organizations SET archived_at = NOW() WHERE id = $1", org_id)
    )
    response = client.delete(f"/admin/orgs/{org_id}/", headers=AUTH_HEADERS)
    assert response.status_code == 200
```

- [ ] **Step 2: Run to confirm failure**

```
DATABASE_URL=<dsn> uv run pytest tests/api/admin/test_orgs.py -m integration -v
```

Expected: new tests FAIL.

- [ ] **Step 3: Add routes to `src/api/admin/orgs.py`**

```python
@router.get("/new/")
async def org_new_form(
    request: Request,
    user: AdminUser | RedirectResponse = Depends(get_admin_user),
    db=Depends(get_db),
):
    redirect, user = _require_user(user)
    if redirect:
        return redirect

    # Load parent options
    parents = await db.fetch(
        """SELECT o.id, n.name AS canonical_name
           FROM organizations o
           LEFT JOIN organization_names n
             ON n.organization_id = o.id AND n.is_canonical = TRUE
           WHERE o.archived_at IS NULL
           ORDER BY n.name NULLS LAST"""
    )
    return templates.TemplateResponse(
        request,
        "admin/orgs/form.html",
        {"user": user, "active_section": "orgs", "org": None, "parents": parents},
    )


@router.post("/new/")
async def org_create(
    request: Request,
    name: str = Form(...),
    active: bool = Form(True),
    parent_id: str = Form(""),
    notes: str = Form(""),
    user: AdminUser | RedirectResponse = Depends(get_admin_user),
    db=Depends(get_db),
):
    redirect, user = _require_user(user)
    if redirect:
        return redirect

    org_id = generate_id()
    await db.execute(
        "INSERT INTO organizations (id, active, parent_id, notes) VALUES ($1, $2, $3, $4)",
        org_id, active, parent_id or None, notes or None,
    )
    await db.execute(
        "INSERT INTO organization_names (id, organization_id, name, is_canonical)"
        " VALUES ($1, $2, $3, TRUE)",
        generate_id(), org_id, name,
    )
    return RedirectResponse(f"/admin/orgs/{org_id}/", status_code=303)


@router.get("/{org_id}/edit/")
async def org_edit_form(
    org_id: str,
    request: Request,
    user: AdminUser | RedirectResponse = Depends(get_admin_user),
    db=Depends(get_db),
):
    redirect, user = _require_user(user)
    if redirect:
        return redirect

    org = await db.fetchrow("SELECT * FROM organizations WHERE id = $1", org_id)
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")

    canonical_name = await db.fetchrow(
        "SELECT name FROM organization_names WHERE organization_id = $1 AND is_canonical = TRUE",
        org_id,
    )
    parents = await db.fetch(
        """SELECT o.id, n.name AS canonical_name
           FROM organizations o
           LEFT JOIN organization_names n
             ON n.organization_id = o.id AND n.is_canonical = TRUE
           WHERE o.archived_at IS NULL AND o.id != $1
           ORDER BY n.name NULLS LAST""",
        org_id,
    )
    return templates.TemplateResponse(
        request,
        "admin/orgs/form.html",
        {
            "user": user,
            "active_section": "orgs",
            "org": org,
            "canonical_name": canonical_name["name"] if canonical_name else "",
            "parents": parents,
        },
    )


@router.post("/{org_id}/edit/")
async def org_update(
    org_id: str,
    request: Request,
    name: str = Form(...),
    active: bool = Form(True),
    parent_id: str = Form(""),
    notes: str = Form(""),
    user: AdminUser | RedirectResponse = Depends(get_admin_user),
    db=Depends(get_db),
):
    redirect, user = _require_user(user)
    if redirect:
        return redirect

    org = await db.fetchrow("SELECT id FROM organizations WHERE id = $1", org_id)
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")

    await db.execute(
        "UPDATE organizations SET active = $1, parent_id = $2, notes = $3 WHERE id = $4",
        active, parent_id or None, notes or None, org_id,
    )
    # Update canonical name
    existing = await db.fetchrow(
        "SELECT id FROM organization_names WHERE organization_id = $1 AND is_canonical = TRUE",
        org_id,
    )
    if existing:
        await db.execute(
            "UPDATE organization_names SET name = $1 WHERE id = $2",
            name, existing["id"],
        )
    else:
        await db.execute(
            "INSERT INTO organization_names (id, organization_id, name, is_canonical)"
            " VALUES ($1, $2, $3, TRUE)",
            generate_id(), org_id, name,
        )
    return RedirectResponse(f"/admin/orgs/{org_id}/", status_code=303)


@router.post("/{org_id}/archive/")
async def org_archive(
    org_id: str,
    request: Request,
    user: AdminUser | RedirectResponse = Depends(get_admin_user),
    db=Depends(get_db),
):
    redirect, user = _require_user(user)
    if redirect:
        return redirect

    org = await db.fetchrow("SELECT id FROM organizations WHERE id = $1", org_id)
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")

    await db.execute(
        "UPDATE organizations SET archived_at = NOW() WHERE id = $1", org_id
    )
    from fastapi.responses import RedirectResponse as RR
    return RR(f"/admin/orgs/{org_id}/", status_code=303)


@router.delete("/{org_id}/")
async def org_delete(
    org_id: str,
    request: Request,
    user: AdminUser | RedirectResponse = Depends(get_admin_user),
    db=Depends(get_db),
):
    redirect, user = _require_user(user)
    if redirect:
        return redirect

    org = await db.fetchrow(
        "SELECT id, archived_at FROM organizations WHERE id = $1", org_id
    )
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")
    if not org["archived_at"]:
        raise HTTPException(
            status_code=409,
            detail="Organization must be archived before it can be deleted",
        )

    await db.execute("DELETE FROM organizations WHERE id = $1", org_id)
    # Return empty response; HTMX swaps the row out
    return HTMLResponse(content="", status_code=200)
```

- [ ] **Step 4: Create `src/templates/admin/orgs/form.html`**

```html
{% extends "admin/base.html" %}
{% block title %}{{ 'Edit' if org else 'New' }} Organization{% endblock %}

{% block breadcrumb %}
  <a href="/admin/">Dashboard</a>
  <span class="breadcrumb__sep">›</span>
  <a href="/admin/orgs/">Organizations</a>
  <span class="breadcrumb__sep">›</span>
  <span>{{ 'Edit' if org else 'New' }}</span>
{% endblock %}

{% block content %}
<div class="page-header">
  <h1>{{ 'Edit Organization' if org else 'New Organization' }}</h1>
</div>

<div class="entity-card">
  <form method="POST"
        action="{{ '/admin/orgs/' + org.id + '/edit/' if org else '/admin/orgs/new/' }}">
    <div class="form-group">
      <label for="name">Canonical name <span aria-hidden="true">*</span></label>
      <input id="name" name="name" type="text" required
             value="{{ canonical_name if org else '' }}"
             autocomplete="organization">
    </div>

    <div class="form-group">
      <label for="active">
        <input id="active" name="active" type="checkbox" value="true"
               {% if org is none or org.active %}checked{% endif %}>
        Active (uncheck for historical / defunct organizations)
      </label>
      <div class="form-group__hint">
        Inactive organizations are visually distinguished but not archived.
      </div>
    </div>

    <div class="form-group">
      <label for="parent_id">Parent organization</label>
      <select id="parent_id" name="parent_id">
        <option value="">— none —</option>
        {% for p in parents %}
        <option value="{{ p.id }}"
                {% if org and org.parent_id == p.id %}selected{% endif %}>
          {{ p.canonical_name or p.id }}
        </option>
        {% endfor %}
      </select>
    </div>

    <div class="form-group">
      <label for="notes">Notes</label>
      <textarea id="notes" name="notes" rows="3">{{ org.notes if org else '' }}</textarea>
    </div>

    <div class="form-actions">
      <button type="submit" class="btn btn--primary">
        {{ 'Save changes' if org else 'Create organization' }}
      </button>
      <a href="{{ '/admin/orgs/' + org.id + '/' if org else '/admin/orgs/' }}"
         class="btn btn--ghost">
        Cancel
      </a>
    </div>
  </form>
</div>
{% endblock %}
```

- [ ] **Step 5: Run integration tests**

```
DATABASE_URL=<dsn> uv run pytest tests/api/admin/test_orgs.py -m integration -v
```

Expected: all tests PASS.

- [ ] **Step 6: Run linter**

```
uv run ruff check src/api/admin/orgs.py
```

Fix any issues.

- [ ] **Step 7: Commit**

```bash
git add src/api/admin/orgs.py src/templates/admin/orgs/form.html \
        tests/api/admin/test_orgs.py
git commit -m "#6 feat: add organizations create, edit, archive, and delete"
```

---

## Task 9: People

**Files:**
- Create: `src/api/admin/people.py`
- Create: `src/templates/admin/people/list.html`, `_rows.html`, `detail.html`, `form.html`
- Create: `tests/api/admin/test_people.py`
- Modify: `src/api/admin/router.py`

Follow the Organizations pattern exactly. Key differences:

- Table: `people` (columns: `id`, `personal_pronouns`, `notes`, `archived_at`, `created_at`, `updated_at`)
- Names table: `person_names` (name_types: `legal`, `former`, `preferred`, `alias`, `initials`)
- No `active` flag — people only have active/archived states
- Detail view includes **role assignments timeline** instead of child orgs / roles
- Route prefix: `/people/`

- [ ] **Step 1: Write failing integration tests**

Create `tests/api/admin/test_people.py` — same structure as `test_orgs.py`:

```python
"""Integration tests for admin people views."""

import os

import asyncpg
import pytest
from fastapi.testclient import TestClient

from src.api.main import app
from src.core.db import apply_schema, generate_id

pytestmark = pytest.mark.integration

AUTH_HEADERS = {"X-ExeDev-UserID": "usr_test", "X-ExeDev-Email": "admin@test.com"}


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
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture
async def person_id(db):
    pid = generate_id()
    await db.execute("INSERT INTO people (id) VALUES ($1)", pid)
    await db.execute(
        "INSERT INTO person_names (id, person_id, name, is_canonical)"
        " VALUES ($1, $2, 'Jane Doe', TRUE)",
        generate_id(), pid,
    )
    return pid


def test_people_list_returns_200(client):
    response = client.get("/admin/people/", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert "people" in response.text.lower()


def test_people_list_redirects_unauthenticated(client):
    response = client.get("/admin/people/", allow_redirects=False)
    assert response.status_code in (302, 307)


def test_person_detail_returns_200(client, person_id):
    response = client.get(f"/admin/people/{person_id}/", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert "Jane Doe" in response.text


def test_person_detail_404_for_unknown(client):
    response = client.get(f"/admin/people/{generate_id()}/", headers=AUTH_HEADERS)
    assert response.status_code == 404


def test_create_person_post_redirects(client):
    response = client.post(
        "/admin/people/new/",
        headers=AUTH_HEADERS,
        data={"name": "Test Person"},
        follow_redirects=False,
    )
    assert response.status_code in (302, 303)


def test_archive_person(client, person_id):
    response = client.post(
        f"/admin/people/{person_id}/archive/",
        headers=AUTH_HEADERS,
        follow_redirects=False,
    )
    assert response.status_code in (302, 303)


def test_hard_delete_requires_archive(client, person_id):
    response = client.delete(f"/admin/people/{person_id}/", headers=AUTH_HEADERS)
    assert response.status_code == 409


def test_hard_delete_archived_person(client, db, person_id):
    import asyncio
    asyncio.get_event_loop().run_until_complete(
        db.execute("UPDATE people SET archived_at = NOW() WHERE id = $1", person_id)
    )
    response = client.delete(f"/admin/people/{person_id}/", headers=AUTH_HEADERS)
    assert response.status_code == 200
```

- [ ] **Step 2: Run to confirm failure**

```
DATABASE_URL=<dsn> uv run pytest tests/api/admin/test_people.py -m integration -v
```

- [ ] **Step 3: Create `src/api/admin/people.py`**

Mirror `orgs.py`. Key differences in queries:
- List query: `FROM people p LEFT JOIN person_names n ON n.person_id = p.id AND n.is_canonical = TRUE`
- Detail: fetch names, contacts, addresses, social links, identifiers, and role assignments (join `role_assignments ra`, `roles r`, `organization_names on` for org name)
- No `active` field in form
- Add `personal_pronouns` field in create/edit form

Role assignments query for detail:
```sql
SELECT ra.id, ra.is_current, ra.start_date, ra.end_date, ra.archived_at,
       r.title, o.id AS org_id,
       n.name AS org_name
FROM role_assignments ra
JOIN roles r ON r.id = ra.role_id
JOIN organizations o ON o.id = r.organization_id
LEFT JOIN organization_names n ON n.organization_id = o.id AND n.is_canonical = TRUE
WHERE ra.person_id = $1
ORDER BY ra.is_current DESC, ra.start_date DESC NULLS LAST
```

- [ ] **Step 4: Create templates** (mirror orgs templates with people-specific fields)

`src/templates/admin/people/list.html`, `_rows.html`, `detail.html`, `form.html`

In `detail.html`, replace child orgs / roles sections with role assignments timeline:

```html
<div class="section-header"><h2>Role Assignments</h2></div>
<div class="table-wrapper">
  <table class="data-table">
    <thead>
      <tr>
        <th>Organization</th><th>Role</th>
        <th>Start</th><th>End</th><th>Status</th>
      </tr>
    </thead>
    <tbody>
      {% for ra in role_assignments %}
      <tr class="{{ 'is-archived' if ra.archived_at else '' }}">
        <td><a href="/admin/orgs/{{ ra.org_id }}/">{{ ra.org_name or '—' }}</a></td>
        <td><a href="/admin/role-assignments/{{ ra.id }}/">{{ ra.title }}</a></td>
        <td>{{ ra.start_date or '—' }}</td>
        <td>{{ ra.end_date or '—' }}</td>
        <td>
          {% if ra.is_current %}<span class="badge badge--active">Current</span>
          {% else %}<span class="badge badge--inactive">Former</span>{% endif %}
        </td>
      </tr>
      {% else %}
      <tr><td colspan="5">—</td></tr>
      {% endfor %}
    </tbody>
  </table>
</div>
```

- [ ] **Step 5: Mount router**

In `src/api/admin/router.py`:
```python
from src.api.admin import people as people_module
admin_router.include_router(people_module.router)
```

- [ ] **Step 6: Run integration tests**

```
DATABASE_URL=<dsn> uv run pytest tests/api/admin/test_people.py -m integration -v
```

Expected: all PASS.

- [ ] **Step 7: Commit**

```bash
git add src/api/admin/people.py src/templates/admin/people/ \
        src/api/admin/router.py tests/api/admin/test_people.py
git commit -m "#6 feat: add people admin views (list, detail, CRUD, archive)"
```

---

## Task 10: Roles

**Files:**
- Create: `src/api/admin/roles.py`
- Create: `src/templates/admin/roles/{list,_rows,detail,form}.html`
- Create: `tests/api/admin/test_roles.py`
- Modify: `src/api/admin/router.py`

Follow the Organizations pattern. Key differences:

- Table: `roles` (columns: `id`, `organization_id`, `title`, `notes`, `archived_at`)
- No names table — `title` is the display name
- List grouped by org (add `org_name` via join)
- No `active` flag
- Route prefix: `/roles/`
- Detail shows assignment history (people who have held this role):

```sql
SELECT ra.id, ra.is_current, ra.start_date, ra.end_date, ra.archived_at,
       p.id AS person_id,
       pn.name AS person_name
FROM role_assignments ra
JOIN people p ON p.id = ra.person_id
LEFT JOIN person_names pn ON pn.person_id = p.id AND pn.is_canonical = TRUE
WHERE ra.role_id = $1
ORDER BY ra.is_current DESC, ra.start_date DESC NULLS LAST
```

- Form fields: `organization_id` (select from active orgs), `title`, `notes`

Tests: same shape as `test_orgs.py` / `test_people.py` — list 200, detail 200, create redirects, archive, hard-delete gated.

- [ ] **Step 1: Write failing tests** (`tests/api/admin/test_roles.py`)
- [ ] **Step 2: Run to confirm failure**
- [ ] **Step 3: Create `src/api/admin/roles.py`**
- [ ] **Step 4: Create templates**
- [ ] **Step 5: Mount router**
- [ ] **Step 6: Run tests**

```
DATABASE_URL=<dsn> uv run pytest tests/api/admin/test_roles.py -m integration -v
```

- [ ] **Step 7: Commit**

```bash
git add src/api/admin/roles.py src/templates/admin/roles/ \
        src/api/admin/router.py tests/api/admin/test_roles.py
git commit -m "#6 feat: add roles admin views"
```

---

## Task 11: Role Assignments

**Files:**
- Create: `src/api/admin/role_assignments.py`
- Create: `src/templates/admin/role_assignments/{list,_rows,detail,form}.html`
- Create: `tests/api/admin/test_role_assignments.py`
- Modify: `src/api/admin/router.py`

Follow the pattern. Key differences:

- Table: `role_assignments` (columns: `id`, `person_id`, `role_id`, `is_current`, `start_date`, `end_date`, `notes`, `archived_at`)
- No name — display as `{person_name} @ {role_title} ({org_name})`
- Route prefix: `/role-assignments/`
- List filters: `person_id`, `role_id`, `current` (boolean)
- Form fields: `person_id` (select), `role_id` (select), `is_current` (checkbox), `start_date` (date input), `end_date` (date input, disabled when `is_current` checked), `notes`
- Constraint: `is_current=TRUE` and `end_date` set together is rejected by DB (`chk_current_no_end_date`) — surface as a form validation error

Detail view shows contact methods, URLs, social links, identifiers attached to the assignment.

- [ ] **Step 1: Write failing tests** (`tests/api/admin/test_role_assignments.py`)

Include a test that validates the `is_current` + `end_date` constraint returns an error:

```python
def test_create_with_is_current_and_end_date_returns_error(client, ...):
    # POST with is_current=True and end_date set
    # Expect 422 or form re-render with error message
```

- [ ] **Step 2: Run to confirm failure**
- [ ] **Step 3: Create `src/api/admin/role_assignments.py`**

Wrap the create/update DB call in try/except for `asyncpg.CheckViolationError`, re-render the form with an error message.

- [ ] **Step 4: Create templates**
- [ ] **Step 5: Mount router**
- [ ] **Step 6: Run tests**

```
DATABASE_URL=<dsn> uv run pytest tests/api/admin/test_role_assignments.py -m integration -v
```

- [ ] **Step 7: Commit**

```bash
git add src/api/admin/role_assignments.py src/templates/admin/role_assignments/ \
        src/api/admin/router.py tests/api/admin/test_role_assignments.py
git commit -m "#6 feat: add role assignments admin views"
```

---

## Task 12: Lookup tables

**Files:**
- Create: `src/api/admin/lookups.py`
- Create: `src/templates/admin/lookups/{list,form}.html`
- Create: `tests/api/admin/test_lookups.py`
- Modify: `src/api/admin/router.py`

Manage three lookup tables: `platforms`, `url_types`, `entity_identifier_types`. All use the same two templates — the route passes a `kind` context variable.

No archival step for lookups — hard delete with confirmation only.

Route prefix: `/lookups/`

Sub-routes:
- `GET /lookups/platforms/` → list
- `GET /lookups/platforms/new/` → form
- `POST /lookups/platforms/new/` → create
- `GET /lookups/platforms/{id}/edit/` → form
- `POST /lookups/platforms/{id}/edit/` → update
- `DELETE /lookups/platforms/{id}/` → hard delete (with `hx-confirm`)
- Same for `/lookups/url-types/` and `/lookups/identifier-types/`

- [ ] **Step 1: Write failing tests** (`tests/api/admin/test_lookups.py`)

```python
def test_platforms_list_returns_200(client):
    response = client.get("/admin/lookups/platforms/", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert "Twitter" in response.text  # seeded platform


def test_url_types_list_returns_200(client):
    response = client.get("/admin/lookups/url-types/", headers=AUTH_HEADERS)
    assert response.status_code == 200


def test_identifier_types_list_returns_200(client):
    response = client.get("/admin/lookups/identifier-types/", headers=AUTH_HEADERS)
    assert response.status_code == 200
```

- [ ] **Step 2: Run to confirm failure**
- [ ] **Step 3: Create `src/api/admin/lookups.py`**
- [ ] **Step 4: Create templates**

`list.html` takes `kind` (`platforms`, `url_types`, `identifier_types`), `items`, and column definitions to render the table generically.

- [ ] **Step 5: Mount router**
- [ ] **Step 6: Run tests**

```
DATABASE_URL=<dsn> uv run pytest tests/api/admin/test_lookups.py -m integration -v
```

- [ ] **Step 7: Commit**

```bash
git add src/api/admin/lookups.py src/templates/admin/lookups/ \
        src/api/admin/router.py tests/api/admin/test_lookups.py
git commit -m "#6 feat: add lookup table admin views"
```

---

## Task 13: Import History (read-only)

**Files:**
- Create: `src/api/admin/imports.py`
- Create: `src/templates/admin/imports/batches.html`
- Create: `src/templates/admin/imports/batch_detail.html`
- Create: `tests/api/admin/test_imports.py`
- Modify: `src/api/admin/router.py`

Read-only. No create/edit/delete. Route prefix: `/imports/`.

Sub-routes:
- `GET /imports/` → paginated list of `import_batches`, most recent first
- `GET /imports/{batch_id}/` → batch detail with `import_provenance` rows, expandable `raw_data` / `error_detail` via HTMX

Provenance rows paginated (50 per page, `hx-get` for pagination).

- [ ] **Step 1: Write failing tests** (`tests/api/admin/test_imports.py`)

```python
def test_imports_list_returns_200(client):
    response = client.get("/admin/imports/", headers=AUTH_HEADERS)
    assert response.status_code == 200


def test_import_detail_404_for_unknown(client):
    response = client.get(f"/admin/imports/{generate_id()}/", headers=AUTH_HEADERS)
    assert response.status_code == 404
```

- [ ] **Step 2: Run to confirm failure**
- [ ] **Step 3: Create `src/api/admin/imports.py`**
- [ ] **Step 4: Create templates**
- [ ] **Step 5: Mount router**
- [ ] **Step 6: Run tests**

```
DATABASE_URL=<dsn> uv run pytest tests/api/admin/test_imports.py -m integration -v
```

- [ ] **Step 7: Final full test run**

```
uv run pytest -v
DATABASE_URL=<dsn> uv run pytest -m integration -v
uv run ruff check .
```

Expected: all PASS, no lint errors.

- [ ] **Step 8: Commit**

```bash
git add src/api/admin/imports.py src/templates/admin/imports/ \
        src/api/admin/router.py tests/api/admin/test_imports.py
git commit -m "#6 feat: add import history admin views"
```

---

## Local development notes

**Inject auth headers for local testing** (from `docs/COMMANDS.md`):

```bash
mitmdump \
  --mode reverse:http://localhost:8001 \
  --listen-port 3000 \
  --set modify_headers='/~q/X-Exedev-Email/admin@example.com' \
  --set modify_headers='/~q/X-Exedev-Userid/usr_local_dev'
```

Then browse to `http://localhost:3000/admin/`.
