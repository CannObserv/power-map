"""Integration tests for org detail inline routes: active toggle, notes, name create.

Run with:
    TEST_DATABASE_URL=postgres://... uv run pytest -m integration -v \
        tests/api/admin/test_orgs_detail_inline.py
"""

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


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def db():
    """Live connection wrapped in a rolled-back transaction."""
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
async def org_id(db):
    """Insert a minimal org with a canonical name; return its id."""
    oid = generate_id()
    await db.execute("INSERT INTO organizations (id) VALUES ($1)", oid)
    await db.execute(
        "INSERT INTO organization_names (id, organization_id, name, is_canonical)"
        " VALUES ($1, $2, $3, TRUE)",
        generate_id(),
        oid,
        "Test Org",
    )
    return oid


@pytest.fixture
async def client(db):
    """AsyncClient with app, overriding get_db to use the test connection."""

    async def _get_db_override():
        yield db

    app.dependency_overrides[get_db] = _get_db_override
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c
    app.dependency_overrides.pop(get_db, None)


# ---------------------------------------------------------------------------
# Active toggle
# ---------------------------------------------------------------------------


async def test_active_post_sets_inactive(client, org_id, db):
    # Omitting 'active' key → active == "" → new_active = False
    r = await client.post(
        f"/admin/orgs/{org_id}/inline/active/",
        data={},
        headers=HTMX_HEADERS,
    )
    assert r.status_code == 200
    row = await db.fetchrow("SELECT active FROM organizations WHERE id=$1", org_id)
    assert row["active"] is False


async def test_active_post_sets_active(client, org_id, db):
    await db.execute("UPDATE organizations SET active=FALSE WHERE id=$1", org_id)
    r = await client.post(
        f"/admin/orgs/{org_id}/inline/active/",
        data={"active": "true"},
        headers=HTMX_HEADERS,
    )
    assert r.status_code == 200
    row = await db.fetchrow("SELECT active FROM organizations WHERE id=$1", org_id)
    assert row["active"] is True


async def test_active_post_returns_toggle_partial(client, org_id):
    r = await client.post(
        f"/admin/orgs/{org_id}/inline/active/",
        data={"active": "true"},
        headers=HTMX_HEADERS,
    )
    assert r.status_code == 200
    assert b"active-toggle" in r.content


async def test_active_post_non_htmx_redirects(client, org_id):
    r = await client.post(
        f"/admin/orgs/{org_id}/inline/active/",
        data={"active": "true"},
        headers=AUTH_HEADERS,
        follow_redirects=False,
    )
    assert r.status_code == 303


# ---------------------------------------------------------------------------
# Notes
# ---------------------------------------------------------------------------


async def test_notes_get_returns_partial(client, org_id):
    r = await client.get(
        f"/admin/orgs/{org_id}/inline/notes/", headers=HTMX_HEADERS
    )
    assert r.status_code == 200
    assert b"notes-field" in r.content


async def test_notes_edit_get_returns_form(client, org_id):
    r = await client.get(
        f"/admin/orgs/{org_id}/inline/notes/edit/", headers=HTMX_HEADERS
    )
    assert r.status_code == 200
    assert b"notes-textarea" in r.content


async def test_notes_post_saves_value(client, org_id, db):
    r = await client.post(
        f"/admin/orgs/{org_id}/inline/notes/",
        data={"notes": "Test note content"},
        headers=HTMX_HEADERS,
    )
    assert r.status_code == 200
    row = await db.fetchrow("SELECT notes FROM organizations WHERE id=$1", org_id)
    assert row["notes"] == "Test note content"


async def test_notes_post_clears_whitespace_to_null(client, org_id, db):
    await db.execute("UPDATE organizations SET notes='existing' WHERE id=$1", org_id)
    r = await client.post(
        f"/admin/orgs/{org_id}/inline/notes/",
        data={"notes": "   "},
        headers=HTMX_HEADERS,
    )
    assert r.status_code == 200
    row = await db.fetchrow("SELECT notes FROM organizations WHERE id=$1", org_id)
    assert row["notes"] is None


async def test_notes_post_returns_read_partial(client, org_id):
    r = await client.post(
        f"/admin/orgs/{org_id}/inline/notes/",
        data={"notes": "hello"},
        headers=HTMX_HEADERS,
    )
    assert r.status_code == 200
    assert b"notes-field" in r.content
    assert b"notes-textarea" not in r.content  # read partial, not form


# ---------------------------------------------------------------------------
# Name create — tbody replacement (re-sort)
# ---------------------------------------------------------------------------


async def test_name_create_returns_sorted_tbody(client, org_id, db):
    """Create returns full tbody (innerHTML target), not just the new row."""
    await db.execute(
        "INSERT INTO organization_names (id, organization_id, name, name_type, is_canonical)"
        " VALUES ($1, $2, $3, 'dba', FALSE)",
        generate_id(),
        org_id,
        "Alt Name",
    )
    r = await client.post(
        f"/admin/orgs/{org_id}/names/",
        data={"name": "New Name", "name_type": "legal", "is_canonical": ""},
        headers=HTMX_HEADERS,
    )
    assert r.status_code == 200
    # All three names appear in the tbody replacement
    assert b"Test Org" in r.content
    assert b"New Name" in r.content
    assert b"Alt Name" in r.content


# ---------------------------------------------------------------------------
# Flash headers
# ---------------------------------------------------------------------------


async def test_active_post_activate_returns_success_flash(client, org_id):
    r = await client.post(
        f"/admin/orgs/{org_id}/inline/active/",
        data={"active": "true"},
        headers=HTMX_HEADERS,
    )
    assert r.status_code == 200
    trigger = json.loads(r.headers["hx-trigger"])
    assert trigger["showFlash"]["level"] == "success"


async def test_active_post_deactivate_returns_info_flash(client, org_id):
    r = await client.post(
        f"/admin/orgs/{org_id}/inline/active/",
        data={},
        headers=HTMX_HEADERS,
    )
    assert r.status_code == 200
    trigger = json.loads(r.headers["hx-trigger"])
    assert trigger["showFlash"]["level"] == "info"


async def test_notes_post_returns_success_flash(client, org_id):
    r = await client.post(
        f"/admin/orgs/{org_id}/inline/notes/",
        data={"notes": "some notes"},
        headers=HTMX_HEADERS,
    )
    assert r.status_code == 200
    trigger = json.loads(r.headers["hx-trigger"])
    assert trigger["showFlash"]["level"] == "success"


async def test_parent_post_set_returns_success_flash(client, org_id, db):
    parent_id = generate_id()
    await db.execute("INSERT INTO organizations (id) VALUES ($1)", parent_id)
    await db.execute(
        "INSERT INTO organization_names (id, organization_id, name, is_canonical)"
        " VALUES ($1, $2, 'Parent Org', TRUE)",
        generate_id(),
        parent_id,
    )
    r = await client.post(
        f"/admin/orgs/{org_id}/inline/parent/",
        data={"parent_id": parent_id},
        headers=HTMX_HEADERS,
    )
    assert r.status_code == 200
    trigger = json.loads(r.headers["hx-trigger"])
    assert trigger["showFlash"]["level"] == "success"


async def test_parent_post_clear_returns_info_flash(client, org_id, db):
    parent_id = generate_id()
    await db.execute("INSERT INTO organizations (id) VALUES ($1)", parent_id)
    await db.execute(
        "UPDATE organizations SET parent_id=$1 WHERE id=$2", parent_id, org_id
    )
    r = await client.post(
        f"/admin/orgs/{org_id}/inline/parent/",
        data={"parent_id": ""},
        headers=HTMX_HEADERS,
    )
    assert r.status_code == 200
    trigger = json.loads(r.headers["hx-trigger"])
    assert trigger["showFlash"]["level"] == "info"
