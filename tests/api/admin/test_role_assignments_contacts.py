"""Integration tests for role_assignment contact methods CRUD (#326)."""

import json

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from src.api.admin.deps import get_db
from src.api.main import app
from src.core.db import generate_id

pytestmark = [pytest.mark.integration]
AUTH_HEADERS = {"X-ExeDev-UserID": "usr_test", "X-ExeDev-Email": "admin@test.com"}
HTMX_HEADERS = {**AUTH_HEADERS, "HX-Request": "true"}


@pytest_asyncio.fixture(loop_scope="session")
async def db(db_pool):
    async with db_pool.acquire() as conn:
        tr = conn.transaction()
        await tr.start()
        try:
            yield conn
        finally:
            await tr.rollback()


@pytest_asyncio.fixture(loop_scope="session")
async def client(db):
    async def _get_db_override():
        yield db

    app.dependency_overrides[get_db] = _get_db_override
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test", follow_redirects=True
    ) as c:
        yield c
    app.dependency_overrides.pop(get_db, None)


@pytest_asyncio.fixture(loop_scope="session")
async def assignment_and_contact(db):
    oid, rid, pid, raid, cid = (generate_id() for _ in range(5))
    await db.execute("INSERT INTO organizations (id) VALUES ($1)", oid)
    await db.execute(
        "INSERT INTO roles (id, organization_id, title) VALUES ($1, $2, 'Director')", rid, oid
    )
    await db.execute("INSERT INTO people (id) VALUES ($1)", pid)
    await db.execute(
        "INSERT INTO role_assignments (id, person_id, role_id, is_current)"
        " VALUES ($1, $2, $3, TRUE)",
        raid,
        pid,
        rid,
    )
    await db.execute(
        "INSERT INTO contact_methods"
        " (id, entity_type, entity_id, contact_type, value, display_label)"
        " VALUES ($1, 'role_assignment', $2, 'email', 'dir@wslcb.wa.gov', 'Official')",
        cid,
        raid,
    )
    return raid, cid


async def test_new_row_returns_form(client, assignment_and_contact):
    raid, _ = assignment_and_contact
    r = await client.get(
        f"/admin/role-assignments/{raid}/contacts/new-row/?contact_type=email",
        headers=HTMX_HEADERS,
    )
    assert r.status_code == 200
    assert "<form" in r.text


async def test_create(client, assignment_and_contact):
    raid, _ = assignment_and_contact
    r = await client.post(
        f"/admin/role-assignments/{raid}/contacts/",
        headers=HTMX_HEADERS,
        data={"contact_type": "email", "value": "New@WSLCB.wa.gov"},
    )
    assert r.status_code == 200
    # domain lowercased by the normalizer (local part case preserved per RFC)
    assert "New@wslcb.wa.gov" in r.text


async def test_update(client, assignment_and_contact):
    raid, cid = assignment_and_contact
    r = await client.post(
        f"/admin/role-assignments/{raid}/contacts/{cid}/edit-row/",
        headers=HTMX_HEADERS,
        data={"value": "changed@wslcb.wa.gov"},
    )
    assert r.status_code == 200
    assert "changed@wslcb.wa.gov" in r.text


async def test_delete(client, assignment_and_contact):
    raid, cid = assignment_and_contact
    r = await client.delete(f"/admin/role-assignments/{raid}/contacts/{cid}/", headers=HTMX_HEADERS)
    assert r.status_code == 200
    trigger = json.loads(r.headers["hx-trigger"])
    assert trigger["showFlash"]["level"] == "info"


async def test_detail_renders_contact_section(client, assignment_and_contact):
    raid, _ = assignment_and_contact
    r = await client.get(f"/admin/role-assignments/{raid}/", headers=AUTH_HEADERS)
    assert r.status_code == 200
    assert "dir@wslcb.wa.gov" in r.text
    assert f"/admin/role-assignments/{raid}/contacts/new-row/" in r.text


async def test_archived_assignment_hides_add_buttons(client, assignment_and_contact, db):
    """#326 (CR finding 5): +Add buttons are gated on an active assignment."""
    raid, _ = assignment_and_contact
    await db.execute("UPDATE role_assignments SET archived_at = now() WHERE id = $1", raid)
    r = await client.get(f"/admin/role-assignments/{raid}/", headers=AUTH_HEADERS)
    assert r.status_code == 200
    assert f"/admin/role-assignments/{raid}/contacts/new-row/" not in r.text
    assert f"/admin/role-assignments/{raid}/links/new-row/" not in r.text
    assert f"/admin/role-assignments/{raid}/identifiers/new-row/" not in r.text
    # Existing rows still render (read-only).
    assert "dir@wslcb.wa.gov" in r.text
