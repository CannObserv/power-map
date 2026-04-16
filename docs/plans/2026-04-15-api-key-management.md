# API Key Management Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `app_users` + `api_keys` DB tables, a `provision_app_user` admin dep, a `require_api_key` public API dep, CRUD admin UI under Settings → API Keys, and a `/api/v1/` router scaffold.

**Architecture:** Schema adds two tables (`app_users` keyed by exe.dev user ID, `api_keys` with SHA-256 hashed keys). A new `provision_app_user` dep in the admin layer upserts the user row on first API-keys page access. A new `src/api/public/` package holds the `X-API-Key` auth dep and the `/api/v1/` router stub. Admin UI follows the existing row-level HTMX edit pattern from `settings_link_types.py`; key generation returns a one-time modal.

**Tech Stack:** asyncpg, FastAPI, Jinja2 + HTMX, pytest (integration + unit), SHA-256 via `hashlib`, `os.urandom` for key entropy.

**Worktree:** `.worktrees/feature/103-api-key-management`

**Reference files:**
- Design doc: `docs/plans/2026-04-15-api-key-management-design.md`
- Pattern reference: `src/api/admin/settings_link_types.py` + templates
- Modal pattern: `src/templates/admin/partials/delete_modal.html`
- Auth dep: `src/api/admin/deps.py`

---

## File Map

**Create:**
- `src/api/public/__init__.py` — package marker
- `src/api/public/deps.py` — `require_api_key` auth dependency
- `src/api/public/router.py` — `/api/v1/` scaffold router
- `src/api/admin/settings_api_keys.py` — API key CRUD routes + `generate_api_key()`
- `src/templates/admin/settings/api_keys.html` — API keys list page
- `src/templates/admin/settings/partials/_api_key_row.html` — read row
- `src/templates/admin/settings/partials/_api_key_edit_row.html` — edit/new row form
- `src/templates/admin/settings/partials/_api_key_new_key_modal.html` — one-time key display modal
- `tests/api/public/__init__.py` — package marker
- `tests/api/public/test_api_key_auth.py` — public API auth tests

**Modify:**
- `src/core/schema.sql` — add `app_users`, `api_keys` tables + `updated_at` trigger for `app_users`
- `src/api/admin/deps.py` — add `provision_app_user` dependency
- `src/api/admin/router.py` — mount `settings_api_keys` router
- `src/api/admin/settings.py` — add `api_keys_count` to landing query
- `src/templates/admin/settings/index.html` — add API Keys card
- `src/templates/admin/base.html` — add API Keys sidebar link
- `src/api/main.py` — mount public router
- `tests/api/admin/test_settings.py` — update landing test to expect API Keys card

---

## Provisioning note

The design doc says "lazy upsert on every admin login via `get_admin_user`." That is the intent; however, adding `db=Depends(get_db)` to `get_admin_user` would break all non-DB unit tests (they call it directly without a pool). Instead, a separate `provision_app_user` dep wraps `get_admin_user` and does the upsert. It is used only on the API keys routes and the settings landing page — the two places that actually need `app_users` to exist. All other admin routes remain unchanged. The first time you visit Settings or API Keys, your row is silently created.

---

## Task 1: Schema — app_users + api_keys tables

**Files:**
- Modify: `src/core/schema.sql`
- Test: `tests/api/admin/test_settings_api_keys.py` (create this file)

- [ ] **Step 1: Create the test file with failing schema tests**

```python
# tests/api/admin/test_settings_api_keys.py
"""Integration tests for API key management admin routes."""

import hashlib
import os
from unittest.mock import MagicMock

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
        yield conn
    finally:
        await conn.close()


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


# --- Schema ---

async def test_app_users_table_exists(db):
    row = await db.fetchrow(
        "SELECT 1 FROM information_schema.tables WHERE table_name='app_users'"
    )
    assert row is not None


async def test_api_keys_table_exists(db):
    row = await db.fetchrow(
        "SELECT 1 FROM information_schema.tables WHERE table_name='api_keys'"
    )
    assert row is not None


async def test_api_keys_key_hash_unique(db):
    uid = generate_id()
    await db.execute(
        "INSERT INTO app_users (id, email) VALUES ($1, $2)", uid, "a@test.com"
    )
    kid1 = generate_id()
    kid2 = generate_id()
    await db.execute(
        "INSERT INTO api_keys (id, user_id, label, key_prefix, key_hash)"
        " VALUES ($1,$2,$3,$4,$5)",
        kid1, uid, "key1", "pm_abc123", "deadbeef" * 8,
    )
    with pytest.raises(asyncpg.UniqueViolationError):
        await db.execute(
            "INSERT INTO api_keys (id, user_id, label, key_prefix, key_hash)"
            " VALUES ($1,$2,$3,$4,$5)",
            kid2, uid, "key2", "pm_abc124", "deadbeef" * 8,
        )
    await db.execute("DELETE FROM api_keys WHERE user_id=$1", uid)
    await db.execute("DELETE FROM app_users WHERE id=$1", uid)
```

- [ ] **Step 2: Run tests — verify they fail**

```bash
cd /home/exedev/power-map/.worktrees/feature/103-api-key-management
export $(cat /etc/power-map/.env | xargs) && export $(cat .env | xargs)
uv run pytest tests/api/admin/test_settings_api_keys.py::test_app_users_table_exists tests/api/admin/test_settings_api_keys.py::test_api_keys_table_exists -v --no-cov
```

Expected: FAIL — table does not exist.

- [ ] **Step 3: Add tables to schema.sql**

Find the `-- =============================================================================` section just before `-- Ingestion Audit Tables` (near the end) and insert:

```sql
-- =============================================================================
-- Application Users & API Keys
-- =============================================================================

-- One row per exe.dev user; keyed by X-ExeDev-UserID. Upserted on each admin login.
CREATE TABLE IF NOT EXISTS app_users (
    id         TEXT        PRIMARY KEY,  -- X-ExeDev-UserID value
    email      TEXT        NOT NULL,     -- X-ExeDev-Email, updated on each login
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE OR REPLACE TRIGGER trg_updated_at_app_users
    BEFORE UPDATE ON app_users
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- Hashed static API keys for programmatic access. Direct hard delete (no archive).
CREATE TABLE IF NOT EXISTS api_keys (
    id           TEXT        PRIMARY KEY,         -- ULID
    user_id      TEXT        NOT NULL REFERENCES app_users(id),
    label        TEXT        NOT NULL,
    key_prefix   TEXT        NOT NULL,            -- first 8 chars of raw key, for display
    key_hash     TEXT        NOT NULL UNIQUE,     -- SHA-256 hex of raw key
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_used_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_api_keys_user_id ON api_keys(user_id);
```

- [ ] **Step 4: Run tests — verify they pass**

```bash
uv run pytest tests/api/admin/test_settings_api_keys.py::test_app_users_table_exists tests/api/admin/test_settings_api_keys.py::test_api_keys_table_exists tests/api/admin/test_settings_api_keys.py::test_api_keys_key_hash_unique -v --no-cov
```

Expected: PASS.

- [ ] **Step 5: Run full unit suite to confirm no regressions**

```bash
uv run pytest --no-cov -q
```

Expected: same as baseline (297 pass, 1 pre-existing failure).

- [ ] **Step 6: Commit**

```bash
git add src/core/schema.sql tests/api/admin/test_settings_api_keys.py
git commit -m "#103 feat: add app_users and api_keys schema tables"
```

---

## Task 2: provision_app_user dependency

**Files:**
- Modify: `src/api/admin/deps.py`
- Test: `tests/api/admin/test_settings_api_keys.py`

- [ ] **Step 1: Add failing integration test**

Append to `tests/api/admin/test_settings_api_keys.py`:

```python
# --- provision_app_user ---

async def test_provision_app_user_creates_row(db):
    """provision_app_user upserts an app_users row for the current user."""
    from src.api.admin.deps import AdminUser, provision_app_user

    user = AdminUser(id="usr_provision_test", email="provision@test.com")

    # Call dep directly with real db
    result = await provision_app_user(user=user, db=db)
    assert result.id == "usr_provision_test"

    row = await db.fetchrow("SELECT id, email FROM app_users WHERE id=$1", "usr_provision_test")
    assert row is not None
    assert row["email"] == "provision@test.com"
    await db.execute("DELETE FROM app_users WHERE id='usr_provision_test'")


async def test_provision_app_user_updates_email_on_conflict(db):
    """provision_app_user updates email when user already exists."""
    from src.api.admin.deps import AdminUser, provision_app_user

    uid = "usr_upsert_test"
    await db.execute("INSERT INTO app_users (id, email) VALUES ($1,$2)", uid, "old@test.com")
    try:
        user = AdminUser(id=uid, email="new@test.com")
        await provision_app_user(user=user, db=db)
        row = await db.fetchrow("SELECT email FROM app_users WHERE id=$1", uid)
        assert row["email"] == "new@test.com"
    finally:
        await db.execute("DELETE FROM app_users WHERE id=$1", uid)
```

- [ ] **Step 2: Run to confirm failure**

```bash
uv run pytest tests/api/admin/test_settings_api_keys.py::test_provision_app_user_creates_row -v --no-cov
```

Expected: FAIL — `provision_app_user` not defined.

- [ ] **Step 3: Add provision_app_user to deps.py**

Append after `get_admin_user` in `src/api/admin/deps.py`:

```python
async def provision_app_user(
    user: AdminUser = Depends(get_admin_user),
    db=Depends(get_db),
) -> AdminUser:
    """Upsert the app_users row for the current exe.dev user, then return the user.

    Use as a drop-in replacement for get_admin_user on routes that require the
    user to exist in the app_users table (e.g., API key management routes).
    """
    await db.execute(
        """
        INSERT INTO app_users (id, email) VALUES ($1, $2)
        ON CONFLICT (id) DO UPDATE SET email = EXCLUDED.email
        """,
        user.id,
        user.email,
    )
    return user
```

Also add `provision_app_user` to the imports in any file that uses it later (no change needed now).

- [ ] **Step 4: Run tests — pass**

```bash
uv run pytest tests/api/admin/test_settings_api_keys.py::test_provision_app_user_creates_row tests/api/admin/test_settings_api_keys.py::test_provision_app_user_updates_email_on_conflict -v --no-cov
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/api/admin/deps.py tests/api/admin/test_settings_api_keys.py
git commit -m "#103 feat: add provision_app_user dependency"
```

---

## Task 3: generate_api_key() unit function

**Files:**
- Create: `src/api/admin/settings_api_keys.py` (stub + function only)
- Test: `tests/api/admin/test_settings_api_keys.py`

- [ ] **Step 1: Add failing unit test**

Append to `tests/api/admin/test_settings_api_keys.py`:

```python
# --- generate_api_key (unit) ---

def test_generate_api_key_format():
    from src.api.admin.settings_api_keys import generate_api_key
    raw_key, key_hash, key_prefix = generate_api_key()
    assert raw_key.startswith("pm_")
    assert len(raw_key) == 35          # "pm_" + 32 hex chars
    assert len(key_hash) == 64         # SHA-256 hex
    assert key_prefix == raw_key[:8]


def test_generate_api_key_is_random():
    from src.api.admin.settings_api_keys import generate_api_key
    raw1, _, _ = generate_api_key()
    raw2, _, _ = generate_api_key()
    assert raw1 != raw2


def test_generate_api_key_hash_matches():
    import hashlib
    from src.api.admin.settings_api_keys import generate_api_key
    raw_key, key_hash, _ = generate_api_key()
    expected = hashlib.sha256(raw_key.encode()).hexdigest()
    assert key_hash == expected
```

- [ ] **Step 2: Run — fail**

```bash
uv run pytest tests/api/admin/test_settings_api_keys.py::test_generate_api_key_format -v --no-cov
```

Expected: FAIL — module not found.

- [ ] **Step 3: Create settings_api_keys.py stub**

```python
# src/api/admin/settings_api_keys.py
"""Admin settings: API key management CRUD views."""

import hashlib
import os

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from markupsafe import escape

from src.api.admin.deps import (
    AdminUser,
    flash_trigger,
    get_db,
    is_htmx,
    provision_app_user,
)
from src.api.admin.org_dups import get_org_dup_count
from src.api.admin.people_dups import get_person_dup_count
from src.core.db import generate_id

templates = Jinja2Templates(directory="src/templates")
router = APIRouter(prefix="/settings/api-keys", tags=["admin-settings-api-keys"])


def generate_api_key() -> tuple[str, str, str]:
    """Return (raw_key, key_hash, key_prefix).

    raw_key:    "pm_" + 32 hex chars (128-bit random via os.urandom)
    key_hash:   SHA-256 hex of raw_key — stored in DB; never returned after creation
    key_prefix: first 8 chars of raw_key — stored for display identification
    """
    raw_key = "pm_" + os.urandom(16).hex()
    key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
    key_prefix = raw_key[:8]
    return raw_key, key_hash, key_prefix
```

- [ ] **Step 4: Run — pass**

```bash
uv run pytest tests/api/admin/test_settings_api_keys.py::test_generate_api_key_format tests/api/admin/test_settings_api_keys.py::test_generate_api_key_is_random tests/api/admin/test_settings_api_keys.py::test_generate_api_key_hash_matches -v --no-cov
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/api/admin/settings_api_keys.py tests/api/admin/test_settings_api_keys.py
git commit -m "#103 feat: add generate_api_key() helper"
```

---

## Task 4: require_api_key dependency + public router scaffold

**Files:**
- Create: `src/api/public/__init__.py`
- Create: `src/api/public/deps.py`
- Create: `src/api/public/router.py`
- Modify: `src/api/main.py`
- Create: `tests/api/public/__init__.py`
- Create: `tests/api/public/test_api_key_auth.py`

- [ ] **Step 1: Write failing integration tests**

```python
# tests/api/public/__init__.py
# (empty)
```

```python
# tests/api/public/test_api_key_auth.py
"""Integration tests for public API X-API-Key authentication."""

import hashlib
import os

import asyncpg
import pytest
from fastapi.testclient import TestClient

from src.api.main import app
from src.core.db import apply_schema, generate_id

pytestmark = pytest.mark.integration


@pytest.fixture
async def db():
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        pytest.skip("DATABASE_URL not set")
    conn = await asyncpg.connect(dsn)
    try:
        await apply_schema(conn)
        yield conn
    finally:
        await conn.close()


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture
async def api_key_pair(db):
    """Insert a test app_user + api_key; yield raw_key; clean up."""
    uid = generate_id()
    kid = generate_id()
    raw_key = "pm_" + os.urandom(16).hex()
    key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
    await db.execute(
        "INSERT INTO app_users (id, email) VALUES ($1,$2)", uid, "apitest@test.com"
    )
    await db.execute(
        "INSERT INTO api_keys (id, user_id, label, key_prefix, key_hash)"
        " VALUES ($1,$2,$3,$4,$5)",
        kid, uid, "Test Key", raw_key[:8], key_hash,
    )
    yield raw_key
    await db.execute("DELETE FROM api_keys WHERE id=$1", kid)
    await db.execute("DELETE FROM app_users WHERE id=$1", uid)


def test_api_root_valid_key_returns_200(client, api_key_pair):
    response = client.get("/api/v1/", headers={"X-API-Key": api_key_pair})
    assert response.status_code == 200


def test_api_root_invalid_key_returns_401(client):
    response = client.get("/api/v1/", headers={"X-API-Key": "pm_notavalidkey"})
    assert response.status_code == 401


def test_api_root_missing_key_returns_403(client):
    """APIKeyHeader returns 403 when header is absent."""
    response = client.get("/api/v1/")
    assert response.status_code == 403


async def test_api_valid_key_updates_last_used_at(client, api_key_pair, db):
    client.get("/api/v1/", headers={"X-API-Key": api_key_pair})
    key_hash = hashlib.sha256(api_key_pair.encode()).hexdigest()
    row = await db.fetchrow(
        "SELECT last_used_at FROM api_keys WHERE key_hash=$1", key_hash
    )
    assert row["last_used_at"] is not None
```

- [ ] **Step 2: Run — fail**

```bash
uv run pytest tests/api/public/test_api_key_auth.py::test_api_root_missing_key_returns_403 -v --no-cov
```

Expected: FAIL — route not found (404).

- [ ] **Step 3: Create public package files**

```python
# src/api/public/__init__.py
# (empty)
```

```python
# src/api/public/deps.py
"""Public API authentication dependency."""

import hashlib

from fastapi import Depends, HTTPException
from fastapi.security import APIKeyHeader

from src.api.admin.deps import get_db

api_key_header = APIKeyHeader(name="X-API-Key")


async def require_api_key(
    raw_key: str = Depends(api_key_header),
    db=Depends(get_db),
) -> str:
    """Validate X-API-Key header; return user_id on success, raise 401 on failure.

    Also updates last_used_at on the matching api_keys row.
    """
    key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
    row = await db.fetchrow(
        "SELECT id, user_id FROM api_keys WHERE key_hash = $1", key_hash
    )
    if not row:
        raise HTTPException(status_code=401, detail="Invalid API key")
    await db.execute(
        "UPDATE api_keys SET last_used_at = NOW() WHERE id = $1", row["id"]
    )
    return row["user_id"]
```

```python
# src/api/public/router.py
"""Public API v1 router — requires X-API-Key on all routes."""

from fastapi import APIRouter, Depends

from src.api.public.deps import require_api_key

router = APIRouter(prefix="/api/v1", tags=["public-api"])


@router.get("/")
async def api_root(user_id: str = Depends(require_api_key)):
    """API health check — returns version info when key is valid."""
    return {"status": "ok", "version": "v1"}
```

- [ ] **Step 4: Mount public router in main.py**

In `src/api/main.py`, add after the admin router import:

```python
from src.api.public.router import router as public_router
```

And after `app.include_router(admin_router)`:

```python
app.include_router(public_router)
```

- [ ] **Step 5: Run — pass**

```bash
uv run pytest tests/api/public/ -v --no-cov
```

Expected: all 4 tests PASS.

- [ ] **Step 6: Commit**

```bash
git add src/api/public/ tests/api/public/ src/api/main.py
git commit -m "#103 feat: add public API router with X-API-Key auth"
```

---

## Task 5: API keys CRUD routes

**Files:**
- Modify: `src/api/admin/settings_api_keys.py`
- Test: `tests/api/admin/test_settings_api_keys.py`

All routes need templates (created in Task 6) to render. In this task, implement the route logic; temporarily return minimal HTML stubs for template-dependent routes, replacing them in Task 6.

- [ ] **Step 1: Add failing route tests**

Append to `tests/api/admin/test_settings_api_keys.py`:

```python
# --- API keys routes ---

async def _make_user_and_key(db, label="My Key"):
    """Helper: insert app_user + api_key, return (uid, kid, raw_key)."""
    uid = generate_id()
    kid = generate_id()
    raw_key = "pm_" + os.urandom(16).hex()
    key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
    await db.execute(
        "INSERT INTO app_users (id, email) VALUES ($1,$2)", uid, f"{uid}@test.com"
    )
    await db.execute(
        "INSERT INTO api_keys (id, user_id, label, key_prefix, key_hash)"
        " VALUES ($1,$2,$3,$4,$5)",
        kid, uid, label, raw_key[:8], key_hash,
    )
    return uid, kid, raw_key


def test_api_keys_list_requires_auth(client):
    r = client.get("/admin/settings/api-keys/", follow_redirects=False)
    assert r.status_code in (302, 307)


def test_api_keys_list_returns_200(client):
    r = client.get("/admin/settings/api-keys/", headers=AUTH_HEADERS)
    assert r.status_code == 200


def test_api_keys_new_row_returns_form(client):
    r = client.get("/admin/settings/api-keys/new-row/", headers=AUTH_HEADERS)
    assert r.status_code == 200
    assert "label" in r.text


async def test_api_keys_create_returns_modal(client, db):
    r = client.post(
        "/admin/settings/api-keys/",
        headers={**AUTH_HEADERS, "HX-Request": "true"},
        data={"label": "Test Key"},
    )
    assert r.status_code == 200
    assert "pm_" in r.text          # raw key in modal
    assert "not be shown again" in r.text
    # Clean up: find the inserted key
    await db.execute("DELETE FROM api_keys WHERE user_id='usr_test'")
    await db.execute("DELETE FROM app_users WHERE id='usr_test'")


async def test_api_keys_create_non_htmx_redirects(client, db):
    r = client.post(
        "/admin/settings/api-keys/",
        headers=AUTH_HEADERS,
        data={"label": "Non-HTMX Key"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"] == "/admin/settings/api-keys/"
    await db.execute("DELETE FROM api_keys WHERE user_id='usr_test'")
    await db.execute("DELETE FROM app_users WHERE id='usr_test'")


async def test_api_keys_edit_row_get(client, db):
    uid, kid, _ = await _make_user_and_key(db)
    try:
        r = client.get(
            f"/admin/settings/api-keys/{kid}/edit-row/", headers=AUTH_HEADERS
        )
        assert r.status_code == 200
        assert "My Key" in r.text
    finally:
        await db.execute("DELETE FROM api_keys WHERE id=$1", kid)
        await db.execute("DELETE FROM app_users WHERE id=$1", uid)


async def test_api_keys_edit_row_post(client, db):
    uid, kid, _ = await _make_user_and_key(db)
    try:
        r = client.post(
            f"/admin/settings/api-keys/{kid}/edit-row/",
            headers={**AUTH_HEADERS, "HX-Request": "true"},
            data={"label": "Renamed Key"},
        )
        assert r.status_code == 200
        assert "Renamed Key" in r.text
    finally:
        await db.execute("DELETE FROM api_keys WHERE id=$1", kid)
        await db.execute("DELETE FROM app_users WHERE id=$1", uid)


async def test_api_keys_read_row(client, db):
    uid, kid, _ = await _make_user_and_key(db)
    try:
        r = client.get(
            f"/admin/settings/api-keys/{kid}/read-row/", headers=AUTH_HEADERS
        )
        assert r.status_code == 200
        assert "My Key" in r.text
    finally:
        await db.execute("DELETE FROM api_keys WHERE id=$1", kid)
        await db.execute("DELETE FROM app_users WHERE id=$1", uid)


async def test_api_keys_delete(client, db):
    uid, kid, _ = await _make_user_and_key(db)
    try:
        r = client.delete(
            f"/admin/settings/api-keys/{kid}/",
            headers={**AUTH_HEADERS, "HX-Request": "true"},
        )
        assert r.status_code == 200
        row = await db.fetchrow("SELECT id FROM api_keys WHERE id=$1", kid)
        assert row is None
    finally:
        await db.execute("DELETE FROM api_keys WHERE id=$1", kid)
        await db.execute("DELETE FROM app_users WHERE id=$1", uid)


async def test_api_keys_delete_404_when_not_found(client, db):
    r = client.delete(
        "/admin/settings/api-keys/nonexistent/",
        headers={**AUTH_HEADERS, "HX-Request": "true"},
    )
    assert r.status_code == 404
```

- [ ] **Step 2: Run — fail**

```bash
uv run pytest tests/api/admin/test_settings_api_keys.py::test_api_keys_list_requires_auth -v --no-cov
```

Expected: FAIL — 404 (router not mounted yet; that's fine, add routes first).

- [ ] **Step 3: Implement all CRUD routes in settings_api_keys.py**

Add after the `generate_api_key` function:

```python
def _base_ctx(user, org_dup_count, person_dup_count):
    return {
        "user": user,
        "active_section": "settings_api_keys",
        "org_dup_count": org_dup_count,
        "person_dup_count": person_dup_count,
    }


@router.get("/")
async def api_keys_list(
    request: Request,
    user: AdminUser = Depends(provision_app_user),
    db=Depends(get_db),
    org_dup_count: int = Depends(get_org_dup_count),
    person_dup_count: int = Depends(get_person_dup_count),
):
    keys = await db.fetch(
        "SELECT id, label, key_prefix, created_at, last_used_at"
        " FROM api_keys WHERE user_id=$1 ORDER BY created_at DESC",
        user.id,
    )
    return templates.TemplateResponse(
        request,
        "admin/settings/api_keys.html",
        {**_base_ctx(user, org_dup_count, person_dup_count), "keys": keys},
    )


@router.get("/new-row/")
async def api_key_new_row(
    request: Request,
    user: AdminUser = Depends(provision_app_user),
):
    return templates.TemplateResponse(
        request,
        "admin/settings/partials/_api_key_edit_row.html",
        {"key": None},
    )


@router.post("/")
async def api_key_create(
    request: Request,
    label: str = Form(...),
    user: AdminUser = Depends(provision_app_user),
    db=Depends(get_db),
    org_dup_count: int = Depends(get_org_dup_count),
    person_dup_count: int = Depends(get_person_dup_count),
):
    label_val = label.strip()
    if not label_val:
        raise HTTPException(status_code=422, detail="label is required")
    raw_key, key_hash, key_prefix = generate_api_key()
    kid = generate_id()
    await db.execute(
        "INSERT INTO api_keys (id, user_id, label, key_prefix, key_hash)"
        " VALUES ($1,$2,$3,$4,$5)",
        kid, user.id, label_val, key_prefix, key_hash,
    )
    if not is_htmx(request):
        return RedirectResponse("/admin/settings/api-keys/", status_code=303)
    return templates.TemplateResponse(
        request,
        "admin/settings/partials/_api_key_new_key_modal.html",
        {"raw_key": raw_key, "label": label_val},
    )


@router.get("/{key_id}/edit-row/")
async def api_key_edit_row_get(
    key_id: str,
    request: Request,
    user: AdminUser = Depends(provision_app_user),
    db=Depends(get_db),
):
    key = await db.fetchrow(
        "SELECT id, label, key_prefix, created_at, last_used_at"
        " FROM api_keys WHERE id=$1 AND user_id=$2",
        key_id, user.id,
    )
    if not key:
        raise HTTPException(status_code=404)
    return templates.TemplateResponse(
        request,
        "admin/settings/partials/_api_key_edit_row.html",
        {"key": key},
    )


@router.post("/{key_id}/edit-row/")
async def api_key_edit_row_post(
    key_id: str,
    request: Request,
    label: str = Form(...),
    user: AdminUser = Depends(provision_app_user),
    db=Depends(get_db),
):
    label_val = label.strip()
    if not label_val:
        raise HTTPException(status_code=422, detail="label is required")
    key = await db.fetchrow(
        "SELECT id FROM api_keys WHERE id=$1 AND user_id=$2", key_id, user.id
    )
    if not key:
        raise HTTPException(status_code=404)
    await db.execute("UPDATE api_keys SET label=$1 WHERE id=$2", label_val, key_id)
    row = await db.fetchrow(
        "SELECT id, label, key_prefix, created_at, last_used_at FROM api_keys WHERE id=$1",
        key_id,
    )
    if not is_htmx(request):
        return RedirectResponse("/admin/settings/api-keys/", status_code=303)
    return templates.TemplateResponse(
        request,
        "admin/settings/partials/_api_key_row.html",
        {"key": row},
        headers=flash_trigger(
            "success", f"Key <strong>{escape(label_val)}</strong> renamed."
        ),
    )


@router.get("/{key_id}/read-row/")
async def api_key_read_row(
    key_id: str,
    request: Request,
    user: AdminUser = Depends(provision_app_user),
    db=Depends(get_db),
):
    row = await db.fetchrow(
        "SELECT id, label, key_prefix, created_at, last_used_at"
        " FROM api_keys WHERE id=$1 AND user_id=$2",
        key_id, user.id,
    )
    if not row:
        raise HTTPException(status_code=404)
    return templates.TemplateResponse(
        request,
        "admin/settings/partials/_api_key_row.html",
        {"key": row},
    )


@router.delete("/{key_id}/")
async def api_key_delete(
    key_id: str,
    request: Request,
    user: AdminUser = Depends(provision_app_user),
    db=Depends(get_db),
):
    existing = await db.fetchrow(
        "SELECT id, label FROM api_keys WHERE id=$1 AND user_id=$2", key_id, user.id
    )
    if not existing:
        raise HTTPException(status_code=404)
    await db.execute("DELETE FROM api_keys WHERE id=$1", key_id)
    return HTMLResponse(
        content="",
        status_code=200,
        headers=flash_trigger(
            "info",
            f"Key <strong>{escape(existing['label'])}</strong> deleted.",
        ),
    )
```

- [ ] **Step 4: Mount the router in router.py**

In `src/api/admin/router.py`, add:

```python
from src.api.admin import settings_api_keys as settings_api_keys_module
```

And:

```python
admin_router.include_router(settings_api_keys_module.router)
```

- [ ] **Step 5: Run route tests — they will fail on missing templates**

```bash
uv run pytest tests/api/admin/test_settings_api_keys.py -v --no-cov -k "route or list or create or edit or read or delete or keys"
```

Expected: some fail with `TemplateNotFound` — that's expected; templates come in Task 6.

- [ ] **Step 6: Commit stub routes (before templates)**

```bash
git add src/api/admin/settings_api_keys.py src/api/admin/router.py tests/api/admin/test_settings_api_keys.py
git commit -m "#103 feat: add API key CRUD routes (templates pending)"
```

---

## Task 6: API keys templates

**Files:**
- Create: `src/templates/admin/settings/api_keys.html`
- Create: `src/templates/admin/settings/partials/_api_key_row.html`
- Create: `src/templates/admin/settings/partials/_api_key_edit_row.html`
- Create: `src/templates/admin/settings/partials/_api_key_new_key_modal.html`

- [ ] **Step 1: Create _api_key_row.html**

```html
{# _api_key_row.html — read-only row; expects: key #}
<tr id="api-key-row-{{ key.id }}">
  <td>{{ key.label }}</td>
  <td style="font-family:monospace;color:var(--color-text-muted)">{{ key.key_prefix }}…</td>
  <td style="color:var(--color-text-muted)">{{ key.created_at.strftime('%Y-%m-%d') if key.created_at else '—' }}</td>
  <td style="color:var(--color-text-muted)">{{ key.last_used_at.strftime('%Y-%m-%d') if key.last_used_at else 'Never' }}</td>
  <td style="text-align:right;white-space:nowrap">
    <button type="button" class="btn btn--sm btn--secondary"
            hx-get="/admin/settings/api-keys/{{ key.id }}/edit-row/"
            hx-target="#api-key-row-{{ key.id }}"
            hx-swap="outerHTML">Edit</button>
    <button type="button" class="btn btn--sm btn--danger"
            hx-delete="/admin/settings/api-keys/{{ key.id }}/"
            hx-target="#api-key-row-{{ key.id }}"
            hx-swap="outerHTML"
            hx-confirm="Delete key '{{ key.label }}'? This cannot be undone."
            data-confirm-title="Delete API key?"
            data-confirm-label="Delete">Delete</button>
  </td>
</tr>
```

- [ ] **Step 2: Create _api_key_edit_row.html**

```html
{# _api_key_edit_row.html — edit/new form row; expects: key (None for new) #}
<tr id="{% if key %}api-key-row-{{ key.id }}{% else %}api-key-row-new{% endif %}">
  <td colspan="5" style="padding:var(--space-2) var(--space-4)">
    <form {% if key %}
          hx-post="/admin/settings/api-keys/{{ key.id }}/edit-row/"
          hx-target="#api-key-row-{{ key.id }}"
          hx-swap="outerHTML"
          {% else %}
          hx-post="/admin/settings/api-keys/"
          hx-target="body"
          hx-swap="beforeend"
          {% endif %}
          style="display:flex;gap:var(--space-2);align-items:center">
      <div class="form-group" style="margin-bottom:0;flex:1;min-width:12rem">
        <input type="text" name="label" required placeholder="Key label (e.g. My Script)"
               value="{{ key.label if key else '' }}" autofocus>
      </div>
      <div style="display:flex;gap:var(--space-2);margin-left:auto;white-space:nowrap">
        <button type="submit" class="btn btn--sm btn--primary">
          {% if key %}Save{% else %}Generate{% endif %}
        </button>
        <button type="button" class="btn btn--sm btn--secondary"
                {% if key %}
                hx-get="/admin/settings/api-keys/{{ key.id }}/read-row/"
                hx-target="#api-key-row-{{ key.id }}"
                hx-swap="outerHTML"
                {% else %}
                onclick="this.closest('tr').remove()"
                {% endif %}>Cancel</button>
      </div>
    </form>
  </td>
</tr>
```

- [ ] **Step 3: Create _api_key_new_key_modal.html**

```html
{# _api_key_new_key_modal.html — one-time key display; expects: raw_key, label #}
<div class="modal-backdrop" id="new-key-modal">
  <div class="modal" role="dialog" aria-modal="true" aria-labelledby="new-key-title">
    <h2 id="new-key-title">API key generated</h2>
    <p>Key <strong>{{ label }}</strong> created. Copy it now — it will not be shown again.</p>
    <div class="form-group" style="margin-bottom:var(--space-4)">
      <label for="new-key-value" class="sr-only">API key</label>
      <input id="new-key-value" type="text" readonly
             value="{{ raw_key }}"
             style="font-family:monospace;font-size:var(--font-size-sm)"
             onclick="this.select()">
    </div>
    <div class="alert alert--warning" role="alert" style="margin-bottom:var(--space-4)">
      This key will not be shown again. If you lose it, delete it and generate a new one.
    </div>
    <div class="modal__actions">
      <button class="btn btn--ghost" type="button" id="new-key-copy">Copy to clipboard</button>
      <a href="/admin/settings/api-keys/" class="btn btn--primary">Done</a>
    </div>
  </div>
</div>
<script>
(function () {
  var modal = document.getElementById('new-key-modal');
  var input = document.getElementById('new-key-value');
  var copyBtn = document.getElementById('new-key-copy');

  // Auto-select on load
  input.select();

  copyBtn.addEventListener('click', function () {
    navigator.clipboard.writeText(input.value).then(function () {
      copyBtn.textContent = 'Copied!';
      copyBtn.disabled = true;
    });
  });

  // Trap focus inside modal
  var focusable = Array.from(modal.querySelectorAll('button, a, input'));
  modal.addEventListener('keydown', function (e) {
    if (e.key === 'Escape') { window.location.href = '/admin/settings/api-keys/'; }
    if (e.key !== 'Tab') return;
    var first = focusable[0], last = focusable[focusable.length - 1];
    if (e.shiftKey) {
      if (document.activeElement === first) { e.preventDefault(); last.focus(); }
    } else {
      if (document.activeElement === last) { e.preventDefault(); first.focus(); }
    }
  });
})();
</script>
```

- [ ] **Step 4: Create api_keys.html**

```html
{% extends "admin/base.html" %}
{% block title %}API Keys{% endblock %}
{% block breadcrumb %}
  <a href="/admin/">Dashboard</a><span class="breadcrumb__sep">›</span>
  <a href="/admin/settings/">Settings</a><span class="breadcrumb__sep">›</span>
  <span>API Keys</span>
{% endblock %}
{% block content %}
<div class="page-header"><h1>API Keys</h1></div>

<div style="margin-bottom:var(--space-4)">
  <p style="color:var(--color-text-muted);margin:0 0 var(--space-3)">
    Keys authenticate requests to the public API (<code>X-API-Key</code> header).
    Each key is shown only once on creation.
  </p>
</div>

<div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:var(--space-3)">
  <h2 style="margin:0;font-size:var(--font-size-md);font-weight:700">Your Keys</h2>
  <button class="btn btn--sm btn--secondary"
          hx-get="/admin/settings/api-keys/new-row/"
          hx-target="#api-keys-body"
          hx-swap="afterbegin">+ Generate new key</button>
</div>

<div class="table-wrapper">
  <table class="data-table">
    <thead>
      <tr>
        <th scope="col">Label</th>
        <th scope="col">Key prefix</th>
        <th scope="col">Created</th>
        <th scope="col">Last used</th>
        <th scope="col"><span class="sr-only">Actions</span></th>
      </tr>
    </thead>
    <tbody id="api-keys-body">
      {% for key in keys %}
      {% include "admin/settings/partials/_api_key_row.html" %}
      {% else %}
      <tr><td colspan="5" style="color:var(--color-text-muted)">No API keys yet.</td></tr>
      {% endfor %}
    </tbody>
  </table>
</div>
{% endblock %}
```

- [ ] **Step 5: Run all API key tests — pass**

```bash
uv run pytest tests/api/admin/test_settings_api_keys.py -v --no-cov
```

Expected: all tests PASS (the `test_api_valid_key_updates_last_used_at` test lives in `tests/api/public/` — run that separately).

- [ ] **Step 6: Commit templates**

```bash
git add src/templates/admin/settings/api_keys.html \
        src/templates/admin/settings/partials/_api_key_row.html \
        src/templates/admin/settings/partials/_api_key_edit_row.html \
        src/templates/admin/settings/partials/_api_key_new_key_modal.html
git commit -m "#103 feat: add API key admin UI templates"
```

---

## Task 7: Settings landing card + sidebar + wiring

**Files:**
- Modify: `src/api/admin/settings.py`
- Modify: `src/templates/admin/settings/index.html`
- Modify: `src/templates/admin/base.html`
- Test: `tests/api/admin/test_settings.py` (update existing test)

- [ ] **Step 1: Write failing test**

In `tests/api/admin/test_settings.py`, update `test_settings_landing_returns_200` to check:

```python
assert "API Keys" in response.text
```

Run:

```bash
uv run pytest tests/api/admin/test_settings.py::test_settings_landing_returns_200 -v --no-cov
```

Expected: FAIL — "API Keys" not in page.

- [ ] **Step 2: Add api_keys_count to settings.py**

In the `settings_index` route, update the `counts` query to add:

```sql
(SELECT COUNT(*) FROM api_keys ak
 JOIN app_users au ON ak.user_id = au.id
 WHERE au.id = $1) AS api_keys
```

Pass `user.id` as the parameter. Update the return:

```python
counts = await db.fetchrow(
    """
    SELECT
        (SELECT COUNT(*) FROM link_types WHERE is_social = FALSE) AS general_link_types,
        (SELECT COUNT(*) FROM link_types WHERE is_social = TRUE)  AS social_link_types,
        (SELECT COUNT(*) FROM entity_identifier_types)            AS identifier_types,
        (SELECT COUNT(*) FROM api_keys WHERE user_id = $1)        AS api_keys
    """,
    user.id,
)
```

Also change `user: AdminUser = Depends(get_admin_user)` to `user: AdminUser = Depends(provision_app_user)` so the row exists before querying. Add `provision_app_user` to the imports from `deps`.

- [ ] **Step 3: Add API Keys card to settings/index.html**

Add after the Identifier Types card block:

```html
  {# API Keys #}
  <div style="background:var(--color-surface-1);border:1px solid var(--color-border);border-radius:var(--radius-lg);padding:var(--space-5)">
    <div style="display:flex;align-items:baseline;justify-content:space-between;margin-bottom:var(--space-2)">
      <h2 style="margin:0;font-size:var(--font-size-md);font-weight:700">API Keys</h2>
      <span style="font-size:var(--font-size-sm);color:var(--color-text-muted)">
        {{ counts.api_keys }} {{ 'key' if counts.api_keys == 1 else 'keys' }}
      </span>
    </div>
    <p style="margin:0 0 var(--space-4);font-size:var(--font-size-sm);color:var(--color-text-muted)">
      Static keys for programmatic access to the public API.
    </p>
    <a href="/admin/settings/api-keys/" class="btn btn--sm btn--secondary">Manage →</a>
  </div>
```

- [ ] **Step 4: Add API Keys link to sidebar in base.html**

After the Identifier Types sidebar link, add:

```html
<a class="admin-sidebar__link" href="/admin/settings/api-keys/" {% if active_section == 'settings_api_keys' %}aria-current="page"{% endif %}>API Keys</a>
```

- [ ] **Step 5: Run landing test — pass**

```bash
uv run pytest tests/api/admin/test_settings.py::test_settings_landing_returns_200 -v --no-cov
```

Expected: PASS.

- [ ] **Step 6: Run full test suite**

```bash
uv run pytest --no-cov -q
```

Expected: all tests pass (bar the 1 pre-existing address normalizer failure).

- [ ] **Step 7: Commit**

```bash
git add src/api/admin/settings.py \
        src/templates/admin/settings/index.html \
        src/templates/admin/base.html \
        tests/api/admin/test_settings.py
git commit -m "#103 feat: add API Keys card to settings landing and sidebar"
```

---

## Task 8: Manual smoke test + exe.dev public proxy

- [ ] **Step 1: Restart dev server and smoke-test the UI**

```bash
fuser -k 8001/tcp 2>/dev/null; sleep 1
cd /home/exedev/power-map/.worktrees/feature/103-api-key-management
export $(cat /etc/power-map/.env | xargs) && export $(cat .env | xargs)
nohup uv run uvicorn src.api.main:app --host 0.0.0.0 --port 8001 --reload > /tmp/power-map-dev.log 2>&1 &
```

Visit `https://power-map.exe.xyz:8001/admin/settings/` — confirm API Keys card appears.
Visit `https://power-map.exe.xyz:8001/admin/settings/api-keys/` — confirm empty list with "Generate new key" button.
Generate a key — confirm modal appears with `pm_` key.
Copy key, click Done — confirm key appears in list with prefix and created date.
Test public API: `curl -H "X-API-Key: <key>" https://power-map.exe.xyz:8001/api/v1/` — expect `{"status":"ok","version":"v1"}`.
Test invalid key: `curl -H "X-API-Key: bad" https://power-map.exe.xyz:8001/api/v1/` — expect 401.

- [ ] **Step 2: Make exe.dev proxy public**

```bash
ssh exe.dev share set-public power-map
```

Re-test from outside (your local machine):

```bash
curl -H "X-API-Key: <key>" https://power-map.exe.xyz/api/v1/
```

Expected: `{"status":"ok","version":"v1"}`.

- [ ] **Step 3: Final commit if any fixups needed**

```bash
git add -A
git commit -m "#103 fix: <description of any fixups>"
```
