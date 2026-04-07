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
    assert b"org-search" in r.content


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
    assert b'name="title"' in r.content


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
