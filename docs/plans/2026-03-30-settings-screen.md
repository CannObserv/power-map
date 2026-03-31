# Settings Screen Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the admin "Reference / Lookups" section with a Settings screen: a landing page card grid plus inline-row-editing sub-pages for Link Types and Identifier Types.

**Architecture:** New `settings.py` router at `/admin/settings/` replaces `lookups.py`. Editable tables use the established inline row-editing HTMX pattern (GET/POST `/{id}/edit-row/`, GET `/{id}/read-row/`, GET `/{scope}/new-row/`). The landing page shows editable-table count cards and read-only schema-constrained type chips, all in one handler.

**Tech Stack:** FastAPI, asyncpg, Jinja2, HTMX, existing admin CSS (`admin.css`)

---

## File Map

**Create:**
- `src/api/admin/settings.py` — settings router (replaces `lookups.py`)
- `src/templates/admin/settings/index.html` — landing page
- `src/templates/admin/settings/link_types.html` — combined general + social link types page
- `src/templates/admin/settings/identifier_types.html` — identifier types page
- `src/templates/admin/settings/partials/_link_type_row.html` — read row
- `src/templates/admin/settings/partials/_link_type_edit_row.html` — edit/new form row
- `src/templates/admin/settings/partials/_link_type_rows.html` — full tbody for post-create re-sort
- `src/templates/admin/settings/partials/_identifier_type_row.html` — read row
- `src/templates/admin/settings/partials/_identifier_type_edit_row.html` — edit/new form row
- `src/templates/admin/settings/partials/_identifier_type_rows.html` — full tbody for post-create re-sort
- `tests/api/admin/test_settings.py` — all settings tests

**Modify:**
- `src/api/admin/router.py` — swap `lookups` import for `settings`
- `src/templates/admin/base.html` — sidebar: replace "Reference / Lookups" with "Settings"

**Delete:**
- `src/api/admin/lookups.py`
- `src/templates/admin/lookups/` (entire directory)
- `tests/api/admin/test_lookups.py`

---

### Task 1: Port existing tests to new URLs and wire new router

This task migrates the existing lookup tests to new settings URLs, creates a minimal `settings.py` that makes those tests pass, wires the router, and deletes the old module.

**Files:**
- Create: `tests/api/admin/test_settings.py`
- Create: `src/api/admin/settings.py`
- Modify: `src/api/admin/router.py`
- Delete: `tests/api/admin/test_lookups.py`, `src/api/admin/lookups.py`, `src/templates/admin/lookups/`

- [ ] **Step 1: Create `tests/api/admin/test_settings.py` with ported tests**

Copy the existing test patterns but use new URLs. These will fail until routes exist.

```python
"""Integration tests for admin settings views."""

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
        yield conn
    finally:
        await conn.close()


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


# --- Landing page ---

def test_settings_landing_returns_200(client):
    response = client.get("/admin/settings/", headers=AUTH_HEADERS)
    assert response.status_code == 200
    # Verify all 5 cards render
    for label in ("Link Types", "Identifier Types", "Organization Name Types",
                  "Person Name Types", "Address Types"):
        assert label in response.text
    # Read-only chips present
    assert "legal" in response.text
    assert "mailing" in response.text
    # Sidebar active section
    assert 'aria-current="page"' in response.text


def test_settings_landing_redirects_unauthenticated(client):
    response = client.get("/admin/settings/", follow_redirects=False)
    assert response.status_code in (302, 307)
    assert "/__exe.dev/login" in response.headers["location"]


# --- Link Types page ---

def test_link_types_page_returns_200(client):
    response = client.get("/admin/settings/link-types/", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert "General" in response.text
    assert "Social" in response.text


def test_link_types_page_redirects_unauthenticated(client):
    response = client.get("/admin/settings/link-types/", follow_redirects=False)
    assert response.status_code in (302, 307)


# --- Identifier Types page ---

def test_identifier_types_page_returns_200(client):
    response = client.get("/admin/settings/identifier-types/", headers=AUTH_HEADERS)
    assert response.status_code == 200


# --- Link Type new-row ---

def test_link_type_new_row_general(client):
    response = client.get("/admin/settings/link-types/general/new-row/", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert "display_name" in response.text
    assert "slug" in response.text


def test_link_type_new_row_social(client):
    response = client.get("/admin/settings/link-types/social/new-row/", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert "display_name" in response.text


def test_link_type_new_row_invalid_scope(client):
    response = client.get("/admin/settings/link-types/bad/new-row/", headers=AUTH_HEADERS)
    assert response.status_code == 404


# --- Link Type create ---

async def test_create_general_link_type(client, db):
    slug = f"test-general-{generate_id()}"
    response = client.post(
        "/admin/settings/link-types/general/",
        headers=AUTH_HEADERS,
        data={"display_name": "Test General", "slug": slug},
    )
    assert response.status_code == 200
    assert slug in response.text
    await db.execute("DELETE FROM link_types WHERE slug=$1", slug)


async def test_create_social_link_type(client, db):
    slug = f"test-social-{generate_id()}"
    response = client.post(
        "/admin/settings/link-types/social/",
        headers=AUTH_HEADERS,
        data={"display_name": "Test Social", "slug": slug},
    )
    assert response.status_code == 200
    assert slug in response.text
    await db.execute("DELETE FROM link_types WHERE slug=$1", slug)


# --- Link Type edit-row ---

async def test_link_type_edit_row_get(client, db):
    lid = generate_id()
    await db.execute(
        "INSERT INTO link_types (id, display_name, slug, is_social) VALUES ($1, $2, $3, FALSE)",
        lid, "Edit Me", f"edit-me-{lid}",
    )
    try:
        response = client.get(
            f"/admin/settings/link-types/general/{lid}/edit-row/", headers=AUTH_HEADERS
        )
        assert response.status_code == 200
        assert "Edit Me" in response.text
    finally:
        await db.execute("DELETE FROM link_types WHERE id=$1", lid)


async def test_link_type_edit_row_post(client, db):
    lid = generate_id()
    await db.execute(
        "INSERT INTO link_types (id, display_name, slug, is_social) VALUES ($1, $2, $3, FALSE)",
        lid, "Before", f"before-{lid}",
    )
    try:
        response = client.post(
            f"/admin/settings/link-types/general/{lid}/edit-row/",
            headers=AUTH_HEADERS,
            data={"display_name": "After", "slug": f"after-{lid}"},
        )
        assert response.status_code == 200
        assert "After" in response.text
    finally:
        await db.execute("DELETE FROM link_types WHERE id=$1", lid)


async def test_link_type_read_row(client, db):
    lid = generate_id()
    await db.execute(
        "INSERT INTO link_types (id, display_name, slug, is_social) VALUES ($1, $2, $3, FALSE)",
        lid, "Read Me", f"read-me-{lid}",
    )
    try:
        response = client.get(
            f"/admin/settings/link-types/general/{lid}/read-row/", headers=AUTH_HEADERS
        )
        assert response.status_code == 200
        assert "Read Me" in response.text
    finally:
        await db.execute("DELETE FROM link_types WHERE id=$1", lid)


# --- Link Type delete ---

async def test_delete_general_link_type(client, db):
    lid = generate_id()
    await db.execute(
        "INSERT INTO link_types (id, display_name, slug, is_social) VALUES ($1, $2, $3, FALSE)",
        lid, "Delete Me", f"del-{lid}",
    )
    response = client.delete(
        f"/admin/settings/link-types/general/{lid}/", headers=AUTH_HEADERS
    )
    assert response.status_code == 200
    row = await db.fetchrow("SELECT id FROM link_types WHERE id=$1", lid)
    assert row is None


async def test_delete_link_type_in_use_htmx_returns_flash(client, db):
    """Delete of an in-use link type via HTMX returns 200 with error flash, row preserved."""
    lid = generate_id()
    await db.execute(
        "INSERT INTO link_types (id, display_name, slug, is_social) VALUES ($1, $2, $3, FALSE)",
        lid, "In Use Type", f"in-use-{lid}",
    )
    oid = generate_id()
    link_id = generate_id()
    await db.execute("INSERT INTO organizations (id, active) VALUES ($1, TRUE)", oid)
    await db.execute(
        "INSERT INTO links (id, entity_type, entity_id, url, link_type_id, is_active)"
        " VALUES ($1, 'organization', $2, 'https://example.com', $3, TRUE)",
        link_id, oid, lid,
    )
    try:
        response = client.delete(
            f"/admin/settings/link-types/general/{lid}/",
            headers={**AUTH_HEADERS, "HX-Request": "true"},
        )
        assert response.status_code == 200
        assert "HX-Trigger" in response.headers
        assert "showFlash" in response.headers["HX-Trigger"]
        # Row still exists
        row = await db.fetchrow("SELECT id FROM link_types WHERE id=$1", lid)
        assert row is not None
    finally:
        await db.execute("DELETE FROM links WHERE id=$1", link_id)
        await db.execute("DELETE FROM link_types WHERE id=$1", lid)
        await db.execute("DELETE FROM organizations WHERE id=$1", oid)


async def test_delete_link_type_in_use_non_htmx_returns_409(client, db):
    """Delete of an in-use link type without HTMX returns 409."""
    lid = generate_id()
    await db.execute(
        "INSERT INTO link_types (id, display_name, slug, is_social) VALUES ($1, $2, $3, FALSE)",
        lid, "In Use Type 409", f"in-use-409-{lid}",
    )
    oid = generate_id()
    link_id = generate_id()
    await db.execute("INSERT INTO organizations (id, active) VALUES ($1, TRUE)", oid)
    await db.execute(
        "INSERT INTO links (id, entity_type, entity_id, url, link_type_id, is_active)"
        " VALUES ($1, 'organization', $2, 'https://example.com', $3, TRUE)",
        link_id, oid, lid,
    )
    try:
        response = client.delete(
            f"/admin/settings/link-types/general/{lid}/", headers=AUTH_HEADERS
        )
        assert response.status_code == 409
    finally:
        await db.execute("DELETE FROM links WHERE id=$1", link_id)
        await db.execute("DELETE FROM link_types WHERE id=$1", lid)
        await db.execute("DELETE FROM organizations WHERE id=$1", oid)


async def test_create_link_type_non_htmx_redirects(client, db):
    """Non-HTMX POST create redirects to link-types page."""
    slug = f"test-nonhtmx-{generate_id()}"
    response = client.post(
        "/admin/settings/link-types/general/",
        headers=AUTH_HEADERS,  # no HX-Request header
        data={"display_name": "Non-HTMX Test", "slug": slug},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/admin/settings/link-types/"
    await db.execute("DELETE FROM link_types WHERE slug=$1", slug)


async def test_link_type_edit_row_post_non_htmx_redirects(client, db):
    """Non-HTMX POST edit redirects to link-types page."""
    lid = generate_id()
    await db.execute(
        "INSERT INTO link_types (id, display_name, slug, is_social) VALUES ($1, $2, $3, FALSE)",
        lid, "Non-HTMX Edit", f"nonhtmx-edit-{lid}",
    )
    try:
        response = client.post(
            f"/admin/settings/link-types/general/{lid}/edit-row/",
            headers=AUTH_HEADERS,  # no HX-Request header
            data={"display_name": "Non-HTMX Edited", "slug": f"nonhtmx-edited-{lid}"},
            follow_redirects=False,
        )
        assert response.status_code == 303
        assert response.headers["location"] == "/admin/settings/link-types/"
    finally:
        await db.execute("DELETE FROM link_types WHERE id=$1", lid)


async def test_identifier_type_edit_row_post_non_htmx_redirects(client, db):
    """Non-HTMX POST edit for identifier type redirects to identifier-types page."""
    iid = generate_id()
    await db.execute(
        "INSERT INTO entity_identifier_types (id, display_name, slug, full_name, entity_type)"
        " VALUES ($1, $2, $3, $4, $5)",
        iid, "Non-HTMX ID Edit", f"nonhtmx-id-{iid}", "Non-HTMX Full", "organization",
    )
    try:
        response = client.post(
            f"/admin/settings/identifier-types/{iid}/edit-row/",
            headers=AUTH_HEADERS,  # no HX-Request header
            data={
                "display_name": "Non-HTMX Edited ID",
                "slug": f"nonhtmx-edited-id-{iid}",
                "full_name": "Non-HTMX Edited Full",
                "entity_type": "person",
            },
            follow_redirects=False,
        )
        assert response.status_code == 303
        assert response.headers["location"] == "/admin/settings/identifier-types/"
    finally:
        await db.execute("DELETE FROM entity_identifier_types WHERE id=$1", iid)


def test_identifier_type_new_row(client):
    response = client.get("/admin/settings/identifier-types/new-row/", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert "display_name" in response.text
    assert "entity_type" in response.text


async def test_create_identifier_type_non_htmx_redirects(client, db):
    """Non-HTMX POST create for identifier type redirects to listing page."""
    slug = f"test-id-nonhtmx-{generate_id()}"
    response = client.post(
        "/admin/settings/identifier-types/",
        headers=AUTH_HEADERS,  # no HX-Request header
        data={
            "display_name": "Non-HTMX ID Create",
            "slug": slug,
            "full_name": "Non-HTMX ID Full",
            "entity_type": "organization",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/admin/settings/identifier-types/"
    await db.execute("DELETE FROM entity_identifier_types WHERE slug=$1", slug)


async def test_identifier_type_read_row(client, db):
    """GET read-row returns read partial (used by Cancel on edit form)."""
    iid = generate_id()
    await db.execute(
        "INSERT INTO entity_identifier_types (id, display_name, slug, full_name, entity_type)"
        " VALUES ($1, $2, $3, $4, $5)",
        iid, "Read Row ID", f"read-row-id-{iid}", "Read Row Full", "organization",
    )
    try:
        response = client.get(
            f"/admin/settings/identifier-types/{iid}/read-row/", headers=AUTH_HEADERS
        )
        assert response.status_code == 200
        assert "Read Row ID" in response.text
    finally:
        await db.execute("DELETE FROM entity_identifier_types WHERE id=$1", iid)


async def test_delete_identifier_type_in_use_htmx_returns_flash(client, db):
    """Delete in-use identifier type via HTMX returns 200 with error flash, row preserved."""
    iid = generate_id()
    await db.execute(
        "INSERT INTO entity_identifier_types (id, display_name, slug, full_name, entity_type)"
        " VALUES ($1, $2, $3, $4, $5)",
        iid, "In Use ID Type", f"in-use-id-{iid}", "In Use Full", "organization",
    )
    oid = generate_id()
    identifier_id = generate_id()
    await db.execute("INSERT INTO organizations (id, active) VALUES ($1, TRUE)", oid)
    await db.execute(
        "INSERT INTO identifiers (id, entity_type, entity_id, entity_identifier_type_id, value)"
        " VALUES ($1, 'organization', $2, $3, '99999')",
        identifier_id, oid, iid,
    )
    try:
        response = client.delete(
            f"/admin/settings/identifier-types/{iid}/",
            headers={**AUTH_HEADERS, "HX-Request": "true"},
        )
        assert response.status_code == 200
        assert "HX-Trigger" in response.headers
        assert "showFlash" in response.headers["HX-Trigger"]
        row = await db.fetchrow("SELECT id FROM entity_identifier_types WHERE id=$1", iid)
        assert row is not None
    finally:
        await db.execute("DELETE FROM identifiers WHERE id=$1", identifier_id)
        await db.execute("DELETE FROM entity_identifier_types WHERE id=$1", iid)
        await db.execute("DELETE FROM organizations WHERE id=$1", oid)


async def test_delete_identifier_type_in_use_non_htmx_returns_409(client, db):
    """Delete in-use identifier type without HTMX returns 409."""
    iid = generate_id()
    await db.execute(
        "INSERT INTO entity_identifier_types (id, display_name, slug, full_name, entity_type)"
        " VALUES ($1, $2, $3, $4, $5)",
        iid, "In Use ID 409", f"in-use-id-409-{iid}", "In Use Full 409", "organization",
    )
    oid = generate_id()
    identifier_id = generate_id()
    await db.execute("INSERT INTO organizations (id, active) VALUES ($1, TRUE)", oid)
    await db.execute(
        "INSERT INTO identifiers (id, entity_type, entity_id, entity_identifier_type_id, value)"
        " VALUES ($1, 'organization', $2, $3, '88888')",
        identifier_id, oid, iid,
    )
    try:
        response = client.delete(
            f"/admin/settings/identifier-types/{iid}/", headers=AUTH_HEADERS
        )
        assert response.status_code == 409
    finally:
        await db.execute("DELETE FROM identifiers WHERE id=$1", identifier_id)
        await db.execute("DELETE FROM entity_identifier_types WHERE id=$1", iid)
        await db.execute("DELETE FROM organizations WHERE id=$1", oid)


# --- Usage count ---

async def test_link_type_usage_count_shown(client, db):
    lid = generate_id()
    await db.execute(
        "INSERT INTO link_types (id, display_name, slug, is_social) VALUES ($1, $2, $3, FALSE)",
        lid, "Counted Type", f"counted-{lid}",
    )
    oid = generate_id()
    link_id = generate_id()
    await db.execute("INSERT INTO organizations (id, active) VALUES ($1, TRUE)", oid)
    await db.execute(
        "INSERT INTO links (id, entity_type, entity_id, url, link_type_id, is_active)"
        " VALUES ($1, 'organization', $2, 'https://example.com', $3, TRUE)",
        link_id, oid, lid,
    )
    try:
        response = client.get("/admin/settings/link-types/", headers=AUTH_HEADERS)
        assert response.status_code == 200
        assert "1" in response.text  # usage count visible
    finally:
        await db.execute("DELETE FROM links WHERE id=$1", link_id)
        await db.execute("DELETE FROM link_types WHERE id=$1", lid)
        await db.execute("DELETE FROM organizations WHERE id=$1", oid)


# --- Identifier Types ---

async def test_create_identifier_type(client, db):
    slug = f"test-id-{generate_id()}"
    response = client.post(
        "/admin/settings/identifier-types/",
        headers=AUTH_HEADERS,
        data={
            "display_name": "Test ID",
            "slug": slug,
            "full_name": "Test Identifier Full Name",
            "entity_type": "organization",
        },
    )
    assert response.status_code == 200
    assert slug in response.text
    await db.execute("DELETE FROM entity_identifier_types WHERE slug=$1", slug)


async def test_identifier_type_edit_row_get(client, db):
    iid = generate_id()
    await db.execute(
        "INSERT INTO entity_identifier_types (id, display_name, slug, full_name, entity_type)"
        " VALUES ($1, $2, $3, $4, $5)",
        iid, "Edit ID", f"edit-id-{iid}", "Edit ID Full", "organization",
    )
    try:
        response = client.get(
            f"/admin/settings/identifier-types/{iid}/edit-row/", headers=AUTH_HEADERS
        )
        assert response.status_code == 200
        assert "Edit ID" in response.text
    finally:
        await db.execute("DELETE FROM entity_identifier_types WHERE id=$1", iid)


async def test_identifier_type_edit_row_post(client, db):
    iid = generate_id()
    await db.execute(
        "INSERT INTO entity_identifier_types (id, display_name, slug, full_name, entity_type)"
        " VALUES ($1, $2, $3, $4, $5)",
        iid, "Before ID", f"before-id-{iid}", "Before Full", "organization",
    )
    try:
        response = client.post(
            f"/admin/settings/identifier-types/{iid}/edit-row/",
            headers=AUTH_HEADERS,
            data={
                "display_name": "After ID",
                "slug": f"after-id-{iid}",
                "full_name": "After Full",
                "entity_type": "person",
            },
        )
        assert response.status_code == 200
        assert "After ID" in response.text
    finally:
        await db.execute("DELETE FROM entity_identifier_types WHERE id=$1", iid)


async def test_delete_identifier_type(client, db):
    iid = generate_id()
    await db.execute(
        "INSERT INTO entity_identifier_types (id, display_name, slug, full_name, entity_type)"
        " VALUES ($1, $2, $3, $4, $5)",
        iid, "Delete ID", f"del-id-{iid}", "Delete Full", "organization",
    )
    response = client.delete(
        f"/admin/settings/identifier-types/{iid}/", headers=AUTH_HEADERS
    )
    assert response.status_code == 200
    row = await db.fetchrow("SELECT id FROM entity_identifier_types WHERE id=$1", iid)
    assert row is None


async def test_identifier_type_usage_count_shown(client, db):
    iid = generate_id()
    await db.execute(
        "INSERT INTO entity_identifier_types (id, display_name, slug, full_name, entity_type)"
        " VALUES ($1, $2, $3, $4, $5)",
        iid, "Counted ID", f"counted-id-{iid}", "Counted Full", "organization",
    )
    oid = generate_id()
    identifier_id = generate_id()
    await db.execute("INSERT INTO organizations (id, active) VALUES ($1, TRUE)", oid)
    await db.execute(
        "INSERT INTO identifiers (id, entity_type, entity_id, entity_identifier_type_id, value)"
        " VALUES ($1, 'organization', $2, $3, '12345')",
        identifier_id, oid, iid,
    )
    try:
        response = client.get("/admin/settings/identifier-types/", headers=AUTH_HEADERS)
        assert response.status_code == 200
        assert "1" in response.text
    finally:
        await db.execute("DELETE FROM identifiers WHERE id=$1", identifier_id)
        await db.execute("DELETE FROM entity_identifier_types WHERE id=$1", iid)
        await db.execute("DELETE FROM organizations WHERE id=$1", oid)
```

- [ ] **Step 2: Run new tests to confirm they fail**

```bash
export $(cat /etc/power-map/.env | xargs) 2>/dev/null && export $(cat .env | xargs) 2>/dev/null
uv run pytest tests/api/admin/test_settings.py --no-cov -q 2>&1 | tail -20
```

Expected: multiple 404/connection errors — routes don't exist yet.

- [ ] **Step 3: Create `src/api/admin/settings.py`**

```python
"""Admin settings views: link types, identifier types."""

import asyncpg
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from markupsafe import escape

from src.api.admin.deps import (
    AdminUser,
    check_auth,
    flash_trigger,
    get_admin_user,
    get_db,
    is_htmx,
)
from src.api.admin.org_dups import get_org_dup_count
from src.core.db import generate_id

templates = Jinja2Templates(directory="src/templates")
router = APIRouter(prefix="/settings", tags=["admin-settings"])

_SCOPE_MAP = {"general": False, "social": True}


def _scope_to_is_social(scope: str) -> bool:
    if scope not in _SCOPE_MAP:
        raise HTTPException(status_code=404, detail="Invalid scope")
    return _SCOPE_MAP[scope]


async def _fetch_link_types(db, is_social: bool) -> list:
    return await db.fetch(
        """
        SELECT lt.*, COUNT(l.id) AS usage_count
        FROM link_types lt
        LEFT JOIN links l ON l.link_type_id = lt.id
        WHERE lt.is_social = $1
        GROUP BY lt.id
        ORDER BY lt.display_name
        """,
        is_social,
    )


async def _fetch_identifier_types(db) -> list:
    return await db.fetch(
        """
        SELECT eit.*, COUNT(i.id) AS usage_count
        FROM entity_identifier_types eit
        LEFT JOIN identifiers i ON i.entity_identifier_type_id = eit.id
        GROUP BY eit.id
        ORDER BY eit.display_name
        """
    )


def _base_ctx(user, org_dup_count):
    return {"user": user, "active_section": "settings", "org_dup_count": org_dup_count}


# ---------------------------------------------------------------------------
# Landing page
# ---------------------------------------------------------------------------


@router.get("/")
async def settings_index(
    request: Request,
    user: AdminUser | RedirectResponse = Depends(get_admin_user),
    db=Depends(get_db),
    org_dup_count: int = Depends(get_org_dup_count),
):
    redirect, user = check_auth(user)
    if redirect:
        return redirect
    counts = await db.fetchrow(
        """
        SELECT
            (SELECT COUNT(*) FROM link_types WHERE is_social = FALSE) AS general_link_types,
            (SELECT COUNT(*) FROM link_types WHERE is_social = TRUE)  AS social_link_types,
            (SELECT COUNT(*) FROM entity_identifier_types)            AS identifier_types
        """
    )
    return templates.TemplateResponse(
        request,
        "admin/settings/index.html",
        {**_base_ctx(user, org_dup_count), "counts": counts},
    )


# ---------------------------------------------------------------------------
# Link Types
# ---------------------------------------------------------------------------


@router.get("/link-types/")
async def link_types_page(
    request: Request,
    user: AdminUser | RedirectResponse = Depends(get_admin_user),
    db=Depends(get_db),
    org_dup_count: int = Depends(get_org_dup_count),
):
    redirect, user = check_auth(user)
    if redirect:
        return redirect
    general = await _fetch_link_types(db, False)
    social = await _fetch_link_types(db, True)
    return templates.TemplateResponse(
        request,
        "admin/settings/link_types.html",
        {**_base_ctx(user, org_dup_count), "general": general, "social": social},
    )


@router.get("/link-types/{scope}/new-row/")
async def link_type_new_row(
    scope: str,
    request: Request,
    user: AdminUser | RedirectResponse = Depends(get_admin_user),
):
    redirect, user = check_auth(user)
    if redirect:
        return redirect
    is_social = _scope_to_is_social(scope)
    return templates.TemplateResponse(
        request,
        "admin/settings/partials/_link_type_edit_row.html",
        {"lt": None, "scope": scope, "is_social": is_social},
    )


@router.post("/link-types/{scope}/")
async def link_type_create(
    scope: str,
    request: Request,
    display_name: str = Form(...),
    slug: str = Form(...),
    user: AdminUser | RedirectResponse = Depends(get_admin_user),
    db=Depends(get_db),
):
    redirect, user = check_auth(user)
    if redirect:
        return redirect
    is_social = _scope_to_is_social(scope)
    lid = generate_id()
    await db.execute(
        "INSERT INTO link_types (id, display_name, slug, is_social) VALUES ($1, $2, $3, $4)",
        lid, display_name.strip(), slug.strip() or None, is_social,
    )
    rows = await _fetch_link_types(db, is_social)
    if not is_htmx(request):
        return RedirectResponse("/admin/settings/link-types/", status_code=303)
    return templates.TemplateResponse(
        request,
        "admin/settings/partials/_link_type_rows.html",
        {"rows": rows, "scope": scope},
        headers=flash_trigger(
            "success", f"Link type <strong>{escape(display_name.strip())}</strong> added."
        ),
    )


@router.get("/link-types/{scope}/{item_id}/edit-row/")
async def link_type_edit_row_get(
    scope: str,
    item_id: str,
    request: Request,
    user: AdminUser | RedirectResponse = Depends(get_admin_user),
    db=Depends(get_db),
):
    redirect, user = check_auth(user)
    if redirect:
        return redirect
    is_social = _scope_to_is_social(scope)
    lt = await db.fetchrow(
        "SELECT * FROM link_types WHERE id=$1 AND is_social=$2", item_id, is_social
    )
    if not lt:
        raise HTTPException(status_code=404)
    return templates.TemplateResponse(
        request,
        "admin/settings/partials/_link_type_edit_row.html",
        {"lt": lt, "scope": scope, "is_social": is_social},
    )


@router.post("/link-types/{scope}/{item_id}/edit-row/")
async def link_type_edit_row_post(
    scope: str,
    item_id: str,
    request: Request,
    display_name: str = Form(...),
    slug: str = Form(...),
    user: AdminUser | RedirectResponse = Depends(get_admin_user),
    db=Depends(get_db),
):
    redirect, user = check_auth(user)
    if redirect:
        return redirect
    is_social = _scope_to_is_social(scope)
    lt = await db.fetchrow(
        "SELECT id FROM link_types WHERE id=$1 AND is_social=$2", item_id, is_social
    )
    if not lt:
        raise HTTPException(status_code=404)
    await db.execute(
        "UPDATE link_types SET display_name=$1, slug=$2 WHERE id=$3",
        display_name.strip(), slug.strip() or None, item_id,
    )
    row = await db.fetchrow(
        """
        SELECT lt.*, COUNT(l.id) AS usage_count
        FROM link_types lt
        LEFT JOIN links l ON l.link_type_id = lt.id
        WHERE lt.id = $1
        GROUP BY lt.id
        """,
        item_id,
    )
    if not is_htmx(request):
        return RedirectResponse("/admin/settings/link-types/", status_code=303)
    return templates.TemplateResponse(
        request,
        "admin/settings/partials/_link_type_row.html",
        {"lt": row, "scope": scope},
        headers=flash_trigger(
            "success", f"Link type <strong>{escape(display_name.strip())}</strong> saved."
        ),
    )


@router.get("/link-types/{scope}/{item_id}/read-row/")
async def link_type_read_row(
    scope: str,
    item_id: str,
    request: Request,
    user: AdminUser | RedirectResponse = Depends(get_admin_user),
    db=Depends(get_db),
):
    redirect, user = check_auth(user)
    if redirect:
        return redirect
    is_social = _scope_to_is_social(scope)
    row = await db.fetchrow(
        """
        SELECT lt.*, COUNT(l.id) AS usage_count
        FROM link_types lt
        LEFT JOIN links l ON l.link_type_id = lt.id
        WHERE lt.id = $1 AND lt.is_social = $2
        GROUP BY lt.id
        """,
        item_id, is_social,
    )
    if not row:
        raise HTTPException(status_code=404)
    return templates.TemplateResponse(
        request,
        "admin/settings/partials/_link_type_row.html",
        {"lt": row, "scope": scope},
    )


@router.delete("/link-types/{scope}/{item_id}/")
async def link_type_delete(
    scope: str,
    item_id: str,
    request: Request,
    user: AdminUser | RedirectResponse = Depends(get_admin_user),
    db=Depends(get_db),
):
    redirect, user = check_auth(user)
    if redirect:
        return redirect
    is_social = _scope_to_is_social(scope)
    existing = await db.fetchrow(
        "SELECT id FROM link_types WHERE id=$1 AND is_social=$2", item_id, is_social
    )
    if not existing:
        raise HTTPException(status_code=404)
    try:
        await db.execute("DELETE FROM link_types WHERE id=$1", item_id)
    except asyncpg.ForeignKeyViolationError:
        if not is_htmx(request):
            raise HTTPException(status_code=409, detail="Cannot delete: record is in use")
        return HTMLResponse(
            content="",
            status_code=200,
            headers=flash_trigger("error", "Cannot delete: this link type is in use."),
        )
    return HTMLResponse(
        content="",
        status_code=200,
        headers=flash_trigger("info", "Link type deleted."),
    )


# ---------------------------------------------------------------------------
# Identifier Types
# ---------------------------------------------------------------------------


@router.get("/identifier-types/")
async def identifier_types_page(
    request: Request,
    user: AdminUser | RedirectResponse = Depends(get_admin_user),
    db=Depends(get_db),
    org_dup_count: int = Depends(get_org_dup_count),
):
    redirect, user = check_auth(user)
    if redirect:
        return redirect
    rows = await _fetch_identifier_types(db)
    return templates.TemplateResponse(
        request,
        "admin/settings/identifier_types.html",
        {**_base_ctx(user, org_dup_count), "rows": rows},
    )


@router.get("/identifier-types/new-row/")
async def identifier_type_new_row(
    request: Request,
    user: AdminUser | RedirectResponse = Depends(get_admin_user),
):
    redirect, user = check_auth(user)
    if redirect:
        return redirect
    return templates.TemplateResponse(
        request,
        "admin/settings/partials/_identifier_type_edit_row.html",
        {"eit": None},
    )


@router.post("/identifier-types/")
async def identifier_type_create(
    request: Request,
    display_name: str = Form(...),
    slug: str = Form(...),
    full_name: str = Form(...),
    entity_type: str = Form(...),
    user: AdminUser | RedirectResponse = Depends(get_admin_user),
    db=Depends(get_db),
):
    redirect, user = check_auth(user)
    if redirect:
        return redirect
    iid = generate_id()
    await db.execute(
        "INSERT INTO entity_identifier_types"
        " (id, display_name, slug, full_name, entity_type)"
        " VALUES ($1, $2, $3, $4, $5)",
        iid, display_name.strip(), slug.strip() or None,
        full_name.strip() or None, entity_type,
    )
    rows = await _fetch_identifier_types(db)
    if not is_htmx(request):
        return RedirectResponse("/admin/settings/identifier-types/", status_code=303)
    return templates.TemplateResponse(
        request,
        "admin/settings/partials/_identifier_type_rows.html",
        {"rows": rows},
        headers=flash_trigger(
            "success", f"Identifier type <strong>{escape(display_name.strip())}</strong> added."
        ),
    )


@router.get("/identifier-types/{item_id}/edit-row/")
async def identifier_type_edit_row_get(
    item_id: str,
    request: Request,
    user: AdminUser | RedirectResponse = Depends(get_admin_user),
    db=Depends(get_db),
):
    redirect, user = check_auth(user)
    if redirect:
        return redirect
    eit = await db.fetchrow(
        "SELECT * FROM entity_identifier_types WHERE id=$1", item_id
    )
    if not eit:
        raise HTTPException(status_code=404)
    return templates.TemplateResponse(
        request,
        "admin/settings/partials/_identifier_type_edit_row.html",
        {"eit": eit},
    )


@router.post("/identifier-types/{item_id}/edit-row/")
async def identifier_type_edit_row_post(
    item_id: str,
    request: Request,
    display_name: str = Form(...),
    slug: str = Form(...),
    full_name: str = Form(...),
    entity_type: str = Form(...),
    user: AdminUser | RedirectResponse = Depends(get_admin_user),
    db=Depends(get_db),
):
    redirect, user = check_auth(user)
    if redirect:
        return redirect
    existing = await db.fetchrow(
        "SELECT id FROM entity_identifier_types WHERE id=$1", item_id
    )
    if not existing:
        raise HTTPException(status_code=404)
    await db.execute(
        "UPDATE entity_identifier_types"
        " SET display_name=$1, slug=$2, full_name=$3, entity_type=$4"
        " WHERE id=$5",
        display_name.strip(), slug.strip() or None,
        full_name.strip() or None, entity_type, item_id,
    )
    row = await db.fetchrow(
        """
        SELECT eit.*, COUNT(i.id) AS usage_count
        FROM entity_identifier_types eit
        LEFT JOIN identifiers i ON i.entity_identifier_type_id = eit.id
        WHERE eit.id = $1
        GROUP BY eit.id
        """,
        item_id,
    )
    if not is_htmx(request):
        return RedirectResponse("/admin/settings/identifier-types/", status_code=303)
    return templates.TemplateResponse(
        request,
        "admin/settings/partials/_identifier_type_row.html",
        {"eit": row},
        headers=flash_trigger(
            "success", f"Identifier type <strong>{escape(display_name.strip())}</strong> saved."
        ),
    )


@router.get("/identifier-types/{item_id}/read-row/")
async def identifier_type_read_row(
    item_id: str,
    request: Request,
    user: AdminUser | RedirectResponse = Depends(get_admin_user),
    db=Depends(get_db),
):
    redirect, user = check_auth(user)
    if redirect:
        return redirect
    row = await db.fetchrow(
        """
        SELECT eit.*, COUNT(i.id) AS usage_count
        FROM entity_identifier_types eit
        LEFT JOIN identifiers i ON i.entity_identifier_type_id = eit.id
        WHERE eit.id = $1
        GROUP BY eit.id
        """,
        item_id,
    )
    if not row:
        raise HTTPException(status_code=404)
    return templates.TemplateResponse(
        request,
        "admin/settings/partials/_identifier_type_row.html",
        {"eit": row},
    )


@router.delete("/identifier-types/{item_id}/")
async def identifier_type_delete(
    item_id: str,
    request: Request,
    user: AdminUser | RedirectResponse = Depends(get_admin_user),
    db=Depends(get_db),
):
    redirect, user = check_auth(user)
    if redirect:
        return redirect
    existing = await db.fetchrow(
        "SELECT id FROM entity_identifier_types WHERE id=$1", item_id
    )
    if not existing:
        raise HTTPException(status_code=404)
    try:
        await db.execute("DELETE FROM entity_identifier_types WHERE id=$1", item_id)
    except asyncpg.ForeignKeyViolationError:
        if not is_htmx(request):
            raise HTTPException(status_code=409, detail="Cannot delete: record is in use")
        return HTMLResponse(
            content="",
            status_code=200,
            headers=flash_trigger("error", "Cannot delete: this identifier type is in use."),
        )
    return HTMLResponse(
        content="",
        status_code=200,
        headers=flash_trigger("info", "Identifier type deleted."),
    )
```

- [ ] **Step 4: Update `src/api/admin/router.py`**

Replace the `lookups` import with `settings`:

```python
# Remove:
from src.api.admin import lookups as lookups_module
# Add:
from src.api.admin import settings as settings_module
```

And replace:
```python
# Remove:
admin_router.include_router(lookups_module.router)
# Add:
admin_router.include_router(settings_module.router)
```

- [ ] **Step 5: Run tests to confirm routes are wired but templates are missing**

```bash
export $(cat /etc/power-map/.env | xargs) 2>/dev/null && export $(cat .env | xargs) 2>/dev/null
uv run pytest tests/api/admin/test_settings.py --no-cov -q 2>&1 | tail -20
```

Expected: `TemplateNotFound` errors — routes exist but templates don't yet.

---

### Task 2: Create all templates

**Files:**
- Create: `src/templates/admin/settings/index.html`
- Create: `src/templates/admin/settings/link_types.html`
- Create: `src/templates/admin/settings/identifier_types.html`
- Create: `src/templates/admin/settings/partials/_link_type_row.html`
- Create: `src/templates/admin/settings/partials/_link_type_edit_row.html`
- Create: `src/templates/admin/settings/partials/_link_type_rows.html`
- Create: `src/templates/admin/settings/partials/_identifier_type_row.html`
- Create: `src/templates/admin/settings/partials/_identifier_type_edit_row.html`
- Create: `src/templates/admin/settings/partials/_identifier_type_rows.html`

- [ ] **Step 1: Create `src/templates/admin/settings/index.html`**

```html
{% extends "admin/base.html" %}
{% block title %}Settings{% endblock %}
{% block breadcrumb %}<a href="/admin/">Dashboard</a><span class="breadcrumb__sep">›</span><span>Settings</span>{% endblock %}
{% block content %}
<div class="page-header"><h1>Settings</h1></div>

<div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:var(--space-5)">

  {# Editable: Link Types #}
  <div style="background:var(--color-surface-1);border:1px solid var(--color-border);border-radius:var(--radius-lg);padding:var(--space-5)">
    <div style="display:flex;align-items:baseline;justify-content:space-between;margin-bottom:var(--space-2)">
      <h2 style="margin:0;font-size:var(--font-size-md);font-weight:700">Link Types</h2>
      <span style="font-size:var(--font-size-sm);color:var(--color-text-muted)">
        {{ counts.general_link_types }} general · {{ counts.social_link_types }} social
      </span>
    </div>
    <p style="margin:0 0 var(--space-4);font-size:var(--font-size-sm);color:var(--color-text-muted)">
      URL categories (general) and social platform types (social).
    </p>
    <a href="/admin/settings/link-types/" class="btn btn--sm btn--secondary">Manage →</a>
  </div>

  {# Editable: Identifier Types #}
  <div style="background:var(--color-surface-1);border:1px solid var(--color-border);border-radius:var(--radius-lg);padding:var(--space-5)">
    <div style="display:flex;align-items:baseline;justify-content:space-between;margin-bottom:var(--space-2)">
      <h2 style="margin:0;font-size:var(--font-size-md);font-weight:700">Identifier Types</h2>
      <span style="font-size:var(--font-size-sm);color:var(--color-text-muted)">
        {{ counts.identifier_types }} records
      </span>
    </div>
    <p style="margin:0 0 var(--space-4);font-size:var(--font-size-sm);color:var(--color-text-muted)">
      External identifier schemas (e.g. UBI, EIN) per entity type.
    </p>
    <a href="/admin/settings/identifier-types/" class="btn btn--sm btn--secondary">Manage →</a>
  </div>

  {# Read-only: Organization Name Types #}
  <div style="background:var(--color-surface-1);border:1px solid var(--color-border);border-radius:var(--radius-lg);padding:var(--space-5)">
    <h2 style="margin:0 0 var(--space-3);font-size:var(--font-size-md);font-weight:700">Organization Name Types</h2>
    <div style="display:flex;flex-wrap:wrap;gap:var(--space-2);margin-bottom:var(--space-3)">
      {% for t in ('legal', 'dba', 'former') %}
      <span class="badge badge--inactive">{{ t }}</span>
      {% endfor %}
    </div>
    <p style="margin:0;font-size:var(--font-size-sm);color:var(--color-text-muted)">
      Schema-defined — contact a developer to change.
    </p>
  </div>

  {# Read-only: Person Name Types #}
  <div style="background:var(--color-surface-1);border:1px solid var(--color-border);border-radius:var(--radius-lg);padding:var(--space-5)">
    <h2 style="margin:0 0 var(--space-3);font-size:var(--font-size-md);font-weight:700">Person Name Types</h2>
    <div style="display:flex;flex-wrap:wrap;gap:var(--space-2);margin-bottom:var(--space-3)">
      {% for t in ('legal', 'former', 'preferred', 'alias', 'initials') %}
      <span class="badge badge--inactive">{{ t }}</span>
      {% endfor %}
    </div>
    <p style="margin:0;font-size:var(--font-size-sm);color:var(--color-text-muted)">
      Schema-defined — contact a developer to change.
    </p>
  </div>

  {# Read-only: Address Types #}
  <div style="background:var(--color-surface-1);border:1px solid var(--color-border);border-radius:var(--radius-lg);padding:var(--space-5)">
    <h2 style="margin:0 0 var(--space-3);font-size:var(--font-size-md);font-weight:700">Address Types</h2>
    <div style="display:flex;flex-wrap:wrap;gap:var(--space-2);margin-bottom:var(--space-3)">
      {% for t in ('mailing', 'physical', 'other') %}
      <span class="badge badge--inactive">{{ t }}</span>
      {% endfor %}
    </div>
    <p style="margin:0;font-size:var(--font-size-sm);color:var(--color-text-muted)">
      Schema-defined — contact a developer to change.
    </p>
  </div>

</div>
{% endblock %}
```

- [ ] **Step 2: Create `src/templates/admin/settings/link_types.html`**

Rows are rendered inline on initial page load (no partial needed). The `_link_type_rows.html` partial is only used for HTMX create responses. Both tbody elements have explicit IDs (`general-link-types-body`, `social-link-types-body`) — all HTMX targets must reference these IDs.

```html
{% extends "admin/base.html" %}
{% block title %}Link Types{% endblock %}
{% block breadcrumb %}
  <a href="/admin/">Dashboard</a><span class="breadcrumb__sep">›</span>
  <a href="/admin/settings/">Settings</a><span class="breadcrumb__sep">›</span>
  <span>Link Types</span>
{% endblock %}
{% block content %}
<div class="page-header"><h1>Link Types</h1></div>

{# General Link Types #}
<div style="margin-bottom:var(--space-6)">
  <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:var(--space-3)">
    <h2 style="margin:0;font-size:var(--font-size-md);font-weight:700">General</h2>
    <button class="btn btn--sm btn--secondary"
            hx-get="/admin/settings/link-types/general/new-row/"
            hx-target="#general-link-types-body"
            hx-swap="beforeend">+ Add</button>
  </div>
  <div class="table-wrapper">
    <table class="data-table">
      <caption class="sr-only">General link types</caption>
      <thead>
        <tr>
          <th scope="col">Display Name</th>
          <th scope="col">Slug</th>
          <th scope="col">In Use</th>
          <th scope="col"><span class="sr-only">Actions</span></th>
        </tr>
      </thead>
      <tbody id="general-link-types-body">
        {% for lt in general %}
        {% set scope = "general" %}
        {% include "admin/settings/partials/_link_type_row.html" %}
        {% else %}
        <tr><td colspan="4" style="color:var(--color-text-muted)">No general link types.</td></tr>
        {% endfor %}
      </tbody>
    </table>
  </div>
</div>

{# Social Link Types #}
<div>
  <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:var(--space-3)">
    <h2 style="margin:0;font-size:var(--font-size-md);font-weight:700">Social</h2>
    <button class="btn btn--sm btn--secondary"
            hx-get="/admin/settings/link-types/social/new-row/"
            hx-target="#social-link-types-body"
            hx-swap="beforeend">+ Add</button>
  </div>
  <div class="table-wrapper">
    <table class="data-table">
      <caption class="sr-only">Social link types</caption>
      <thead>
        <tr>
          <th scope="col">Display Name</th>
          <th scope="col">Slug</th>
          <th scope="col">In Use</th>
          <th scope="col"><span class="sr-only">Actions</span></th>
        </tr>
      </thead>
      <tbody id="social-link-types-body">
        {% for lt in social %}
        {% set scope = "social" %}
        {% include "admin/settings/partials/_link_type_row.html" %}
        {% else %}
        <tr><td colspan="4" style="color:var(--color-text-muted)">No social link types.</td></tr>
        {% endfor %}
      </tbody>
    </table>
  </div>
</div>
{% endblock %}
```

- [ ] **Step 3: Create `src/templates/admin/settings/partials/_link_type_row.html`**

```html
{# _link_type_row.html — read-only row; expects: lt, scope #}
<tr id="link-type-row-{{ lt.id }}">
  <td>{{ lt.display_name }}</td>
  <td style="color:var(--color-text-muted);font-family:monospace">{{ lt.slug or '—' }}</td>
  <td>{{ lt.usage_count if lt.usage_count else '—' }}</td>
  <td style="text-align:right;white-space:nowrap">
    <button type="button" class="btn btn--sm btn--secondary"
            hx-get="/admin/settings/link-types/{{ scope }}/{{ lt.id }}/edit-row/"
            hx-target="#link-type-row-{{ lt.id }}"
            hx-swap="outerHTML">Edit</button>
    <button type="button" class="btn btn--sm btn--danger"
            hx-delete="/admin/settings/link-types/{{ scope }}/{{ lt.id }}/"
            hx-target="#link-type-row-{{ lt.id }}"
            hx-swap="outerHTML"
            hx-confirm="Delete '{{ lt.display_name }}'?{% if lt.usage_count %} Warning: {{ lt.usage_count }} record{{ 's' if lt.usage_count != 1 else '' }} use this type.{% endif %}">Delete</button>
  </td>
</tr>
```

- [ ] **Step 4: Create `src/templates/admin/settings/partials/_link_type_edit_row.html`**

```html
{# _link_type_edit_row.html — edit/new form row; expects: lt (None for new), scope #}
<tr id="{% if lt %}link-type-row-{{ lt.id }}{% else %}link-type-row-new{% endif %}">
  <td colspan="4" style="padding:var(--space-2) var(--space-4)">
    <form {% if lt %}
          hx-post="/admin/settings/link-types/{{ scope }}/{{ lt.id }}/edit-row/"
          hx-target="#link-type-row-{{ lt.id }}"
          hx-swap="outerHTML"
          {% else %}
          hx-post="/admin/settings/link-types/{{ scope }}/"
          hx-target="#{% if scope == 'general' %}general{% else %}social{% endif %}-link-types-body"
          hx-swap="innerHTML"
          {% endif %}
          style="display:flex;gap:var(--space-2);align-items:center">
      <div class="form-group" style="margin-bottom:0;flex:2;min-width:10rem">
        <input type="text" name="display_name" required placeholder="Display Name"
               value="{{ lt.display_name if lt else '' }}">
      </div>
      <div class="form-group" style="margin-bottom:0;flex:1;min-width:8rem">
        <input type="text" name="slug" placeholder="slug"
               value="{{ lt.slug if lt else '' }}">
      </div>
      <div style="display:flex;gap:var(--space-2);margin-left:auto;white-space:nowrap">
        <button type="submit" class="btn btn--sm btn--primary">Save</button>
        <button type="button" class="btn btn--sm btn--secondary"
                {% if lt %}
                hx-get="/admin/settings/link-types/{{ scope }}/{{ lt.id }}/read-row/"
                hx-target="#link-type-row-{{ lt.id }}"
                hx-swap="outerHTML"
                {% else %}
                onclick="this.closest('tr').remove()"
                {% endif %}>Cancel</button>
      </div>
    </form>
  </td>
</tr>
```

- [ ] **Step 5: Create `src/templates/admin/settings/partials/_link_type_rows.html`**

Returned by the create POST to replace the full tbody (re-sorted). Handler passes `{"rows": rows, "scope": scope}` — Jinja2 `include` inherits parent template context, so `scope` is available inside `_link_type_row.html` without explicit passing.

```html
{# _link_type_rows.html — full tbody replacement after create; expects: rows, scope #}
{% for lt in rows %}
{% include "admin/settings/partials/_link_type_row.html" %}
{% else %}
<tr><td colspan="4" style="color:var(--color-text-muted)">No link types.</td></tr>
{% endfor %}
```

- [ ] **Step 6: Create `src/templates/admin/settings/identifier_types.html`**

```html
{% extends "admin/base.html" %}
{% block title %}Identifier Types{% endblock %}
{% block breadcrumb %}
  <a href="/admin/">Dashboard</a><span class="breadcrumb__sep">›</span>
  <a href="/admin/settings/">Settings</a><span class="breadcrumb__sep">›</span>
  <span>Identifier Types</span>
{% endblock %}
{% block content %}
<div class="page-header">
  <h1>Identifier Types</h1>
  <button class="btn btn--secondary"
          hx-get="/admin/settings/identifier-types/new-row/"
          hx-target="#identifier-types-body"
          hx-swap="beforeend">+ Add</button>
</div>
<div class="table-wrapper">
  <table class="data-table" id="identifier-types-table">
    <thead>
      <tr>
        <th scope="col">Display Name</th>
        <th scope="col">Slug</th>
        <th scope="col">Full Name</th>
        <th scope="col">Entity Type</th>
        <th scope="col">In Use</th>
        <th scope="col"><span class="sr-only">Actions</span></th>
      </tr>
    </thead>
    <tbody id="identifier-types-body">
      {% for eit in rows %}
      {% include "admin/settings/partials/_identifier_type_row.html" %}
      {% else %}
      <tr><td colspan="6" style="color:var(--color-text-muted)">No identifier types.</td></tr>
      {% endfor %}
    </tbody>
  </table>
</div>
{% endblock %}
```

- [ ] **Step 7: Create `src/templates/admin/settings/partials/_identifier_type_row.html`**

```html
{# _identifier_type_row.html — read-only row; expects: eit #}
<tr id="identifier-type-row-{{ eit.id }}">
  <td>{{ eit.display_name }}</td>
  <td style="color:var(--color-text-muted);font-family:monospace">{{ eit.slug or '—' }}</td>
  <td>{{ eit.full_name or '—' }}</td>
  <td><span class="badge badge--inactive">{{ eit.entity_type }}</span></td>
  <td>{{ eit.usage_count if eit.usage_count else '—' }}</td>
  <td style="text-align:right;white-space:nowrap">
    <button type="button" class="btn btn--sm btn--secondary"
            hx-get="/admin/settings/identifier-types/{{ eit.id }}/edit-row/"
            hx-target="#identifier-type-row-{{ eit.id }}"
            hx-swap="outerHTML">Edit</button>
    <button type="button" class="btn btn--sm btn--danger"
            hx-delete="/admin/settings/identifier-types/{{ eit.id }}/"
            hx-target="#identifier-type-row-{{ eit.id }}"
            hx-swap="outerHTML"
            hx-confirm="Delete '{{ eit.display_name }}'?{% if eit.usage_count %} Warning: {{ eit.usage_count }} record{{ 's' if eit.usage_count != 1 else '' }} use this type.{% endif %}">Delete</button>
  </td>
</tr>
```

- [ ] **Step 8: Create `src/templates/admin/settings/partials/_identifier_type_edit_row.html`**

```html
{# _identifier_type_edit_row.html — edit/new form row; expects: eit (None for new) #}
<tr id="{% if eit %}identifier-type-row-{{ eit.id }}{% else %}identifier-type-row-new{% endif %}">
  <td colspan="6" style="padding:var(--space-2) var(--space-4)">
    <form {% if eit %}
          hx-post="/admin/settings/identifier-types/{{ eit.id }}/edit-row/"
          hx-target="#identifier-type-row-{{ eit.id }}"
          hx-swap="outerHTML"
          {% else %}
          hx-post="/admin/settings/identifier-types/"
          hx-target="#identifier-types-body"
          hx-swap="innerHTML"
          {% endif %}
          style="display:flex;flex-wrap:wrap;gap:var(--space-2);align-items:center">
      <div class="form-group" style="margin-bottom:0;flex:2;min-width:9rem">
        <input type="text" name="display_name" required placeholder="Display Name"
               value="{{ eit.display_name if eit else '' }}">
      </div>
      <div class="form-group" style="margin-bottom:0;flex:1;min-width:6rem">
        <input type="text" name="slug" placeholder="slug"
               value="{{ eit.slug if eit else '' }}">
      </div>
      <div class="form-group" style="margin-bottom:0;flex:2;min-width:10rem">
        <input type="text" name="full_name" placeholder="Full Name"
               value="{{ eit.full_name if eit else '' }}">
      </div>
      <div class="form-group" style="margin-bottom:0;min-width:9rem">
        <select name="entity_type" required>
          {% for et in ('organization', 'person', 'role_assignment') %}
          <option value="{{ et }}"{% if eit and eit.entity_type == et %} selected{% endif %}>{{ et }}</option>
          {% endfor %}
        </select>
      </div>
      <div style="display:flex;gap:var(--space-2);margin-left:auto;white-space:nowrap">
        <button type="submit" class="btn btn--sm btn--primary">Save</button>
        <button type="button" class="btn btn--sm btn--secondary"
                {% if eit %}
                hx-get="/admin/settings/identifier-types/{{ eit.id }}/read-row/"
                hx-target="#identifier-type-row-{{ eit.id }}"
                hx-swap="outerHTML"
                {% else %}
                onclick="this.closest('tr').remove()"
                {% endif %}>Cancel</button>
      </div>
    </form>
  </td>
</tr>
```

- [ ] **Step 9: Create `src/templates/admin/settings/partials/_identifier_type_rows.html`**

```html
{# _identifier_type_rows.html — full tbody replacement after create; expects: rows #}
{% for eit in rows %}
{% include "admin/settings/partials/_identifier_type_row.html" %}
{% else %}
<tr><td colspan="6" style="color:var(--color-text-muted)">No identifier types.</td></tr>
{% endfor %}
```

- [ ] **Step 10: Run tests**

```bash
export $(cat /etc/power-map/.env | xargs) 2>/dev/null && export $(cat .env | xargs) 2>/dev/null
uv run pytest tests/api/admin/test_settings.py --no-cov -q 2>&1 | tail -20
```

Expected: All tests pass.

- [ ] **Step 11: Commit**

```bash
git add src/api/admin/settings.py src/templates/admin/settings/ tests/api/admin/test_settings.py src/api/admin/router.py
git commit -m "#51 feat: add settings router, templates, and tests"
```

---

### Task 3: Update sidebar and remove lookups

**Files:**
- Modify: `src/templates/admin/base.html`
- Delete: `src/api/admin/lookups.py`, `src/templates/admin/lookups/`, `tests/api/admin/test_lookups.py`

- [ ] **Step 1: Update `src/templates/admin/base.html` sidebar**

Find the "Reference" group (around line 56–57):

```html
      <span class="admin-sidebar__group-label">Reference</span>
      <a class="admin-sidebar__link" href="/admin/lookups/link-types-social/" {% if active_section == 'lookups' %}aria-current="page"{% endif %}>Lookups</a>
```

Replace with:

```html
      <span class="admin-sidebar__group-label">Settings</span>
      <a class="admin-sidebar__link" href="/admin/settings/" {% if active_section == 'settings' %}aria-current="page"{% endif %}>Settings</a>
```

- [ ] **Step 2: Delete old files**

```bash
rm src/api/admin/lookups.py
rm -r src/templates/admin/lookups/
rm tests/api/admin/test_lookups.py
```

- [ ] **Step 3: Run full test suite**

```bash
export $(cat /etc/power-map/.env | xargs) 2>/dev/null && export $(cat .env | xargs) 2>/dev/null
uv run pytest --no-cov -q 2>&1 | tail -10
```

Expected: All tests pass; no references to `lookups` routes remain.

- [ ] **Step 4: Commit**

```bash
git add src/templates/admin/base.html
git rm src/api/admin/lookups.py tests/api/admin/test_lookups.py
git rm -r src/templates/admin/lookups/
git commit -m "#51 feat: replace lookups nav with settings, delete old module"
```

---

### Task 4: Manual smoke test

- [ ] **Step 1: Verify dev server is running from the worktree**

```bash
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8001/admin/
```

Expected: `307` (redirect to login — server is up).

- [ ] **Step 2: Open in browser and test the following**

URL: `https://power-map.exe.xyz:8001/admin/settings/`

Checklist:
- [ ] Sidebar shows "Settings" link (not "Lookups")
- [ ] Landing page loads with 5 cards: Link Types, Identifier Types, Org Name Types, Person Name Types, Address Types
- [ ] Read-only cards show chips (`legal`, `dba`, `former`, etc.)
- [ ] "Manage →" on Link Types navigates to `/admin/settings/link-types/`
- [ ] Link types page shows two tables: General (first), Social (second)
- [ ] "+ Add" appends an inline form row to the correct table
- [ ] Save creates a row; table re-renders sorted; flash appears
- [ ] Edit button loads edit form inline; Save updates in-place; flash appears
- [ ] Cancel on edit restores read row
- [ ] Delete removes row; flash appears
- [ ] Delete of in-use type shows error flash, row remains
- [ ] Same Add/Edit/Delete flows work on Identifier Types page
- [ ] hx-boost nav (sidebar clicks) loads full pages correctly — no bare fragments

- [ ] **Step 3: Run full test suite one final time**

```bash
export $(cat /etc/power-map/.env | xargs) 2>/dev/null && export $(cat .env | xargs) 2>/dev/null
uv run pytest --no-cov -q 2>&1 | tail -10
```

Expected: All tests pass.
