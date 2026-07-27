"""Integration tests for role contact methods CRUD (#326)."""

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
async def role_and_contact(db):
    oid, rid, cid = generate_id(), generate_id(), generate_id()
    await db.execute("INSERT INTO organizations (id) VALUES ($1)", oid)
    await db.execute(
        "INSERT INTO roles (id, organization_id, title) VALUES ($1, $2, 'Chair')", rid, oid
    )
    await db.execute(
        "INSERT INTO contact_methods"
        " (id, entity_type, entity_id, contact_type, value)"
        " VALUES ($1, 'role', $2, 'phone', '+13605551234')",
        cid,
        rid,
    )
    return rid, cid


async def test_new_row_returns_form(client, role_and_contact):
    rid, _ = role_and_contact
    r = await client.get(
        f"/admin/roles/{rid}/contacts/new-row/?contact_type=email", headers=HTMX_HEADERS
    )
    assert r.status_code == 200
    assert "<form" in r.text


async def test_create(client, role_and_contact):
    rid, _ = role_and_contact
    r = await client.post(
        f"/admin/roles/{rid}/contacts/",
        headers=HTMX_HEADERS,
        data={"contact_type": "email", "value": "chair@example.com"},
    )
    assert r.status_code == 200
    assert "chair@example.com" in r.text


async def test_update(client, role_and_contact):
    rid, cid = role_and_contact
    r = await client.post(
        f"/admin/roles/{rid}/contacts/{cid}/edit-row/",
        headers=HTMX_HEADERS,
        data={"value": "360-555-4321"},
    )
    assert r.status_code == 200
    assert "+13605554321" in r.text


async def test_delete(client, role_and_contact):
    rid, cid = role_and_contact
    r = await client.delete(f"/admin/roles/{rid}/contacts/{cid}/", headers=HTMX_HEADERS)
    assert r.status_code == 200
    trigger = json.loads(r.headers["hx-trigger"])
    assert trigger["showFlash"]["level"] == "info"


async def test_create_unknown_role_404(client):
    r = await client.post(
        f"/admin/roles/{generate_id()}/contacts/",
        headers=HTMX_HEADERS,
        data={"contact_type": "email", "value": "x@example.com"},
    )
    assert r.status_code == 404


async def test_detail_renders_contact_section(client, role_and_contact):
    rid, _ = role_and_contact
    r = await client.get(f"/admin/roles/{rid}/", headers=AUTH_HEADERS)
    assert r.status_code == 200
    assert "+13605551234" in r.text
    assert f"/admin/roles/{rid}/contacts/new-row/" in r.text
