"""Integration tests for org identifiers CRUD."""

import json

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from src.api.admin.deps import get_db
from src.api.main import app
from src.core.db import generate_id

pytestmark = [
    pytest.mark.integration,
]
AUTH_HEADERS = {"X-ExeDev-UserID": "usr_test", "X-ExeDev-Email": "admin@test.com"}
HTMX_HEADERS = {**AUTH_HEADERS, "HX-Request": "true"}


@pytest_asyncio.fixture(loop_scope="session")
async def db(db_pool):
    """Pool-acquired connection wrapped in a rolled-back transaction."""
    async with db_pool.acquire() as conn:
        tr = conn.transaction()
        await tr.start()
        try:
            yield conn
        finally:
            await tr.rollback()


@pytest_asyncio.fixture(loop_scope="session")
async def client(db):
    """AsyncClient with app, overriding get_db to use the test connection."""

    async def _get_db_override():
        yield db

    app.dependency_overrides[get_db] = _get_db_override
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test", follow_redirects=True
    ) as c:
        yield c
    app.dependency_overrides.pop(get_db, None)


@pytest_asyncio.fixture(loop_scope="session")
async def org_id_and_type(db):
    """Yields (org_id, identifier_type_id) for an org with an identifier type seeded."""
    oid = generate_id()

    await db.execute("INSERT INTO organizations (id) VALUES ($1)", oid)
    row = await db.fetchrow(
        "SELECT id FROM entity_identifier_types WHERE entity_type='organization' LIMIT 1"
    )
    if not row:
        pytest.skip("No organization identifier types seeded")
    type_id = row["id"]

    return oid, type_id


@pytest_asyncio.fixture(loop_scope="session")
async def org_and_identifier(db, org_id_and_type):
    oid, type_id = org_id_and_type
    iid = generate_id()

    await db.execute(
        "INSERT INTO identifiers (id, entity_id, entity_identifier_type_id, value)"
        " VALUES ($1, $2, $3, 'TEST-123')",
        iid,
        oid,
        type_id,
    )

    return oid, iid, type_id


async def test_identifier_form_row_has_form_group(client, org_id_and_type):
    oid, _ = org_id_and_type
    r = await client.get(f"/admin/orgs/{oid}/identifiers/new-row/", headers=HTMX_HEADERS)
    assert r.status_code == 200
    assert "form-group" in r.text


async def test_identifiers_new_row_returns_form(client, org_id_and_type):
    oid, _ = org_id_and_type
    r = await client.get(f"/admin/orgs/{oid}/identifiers/new-row/", headers=HTMX_HEADERS)
    assert r.status_code == 200
    assert "<form" in r.text


async def test_identifiers_create(client, org_id_and_type):
    oid, type_id = org_id_and_type
    r = await client.post(
        f"/admin/orgs/{oid}/identifiers/",
        headers=HTMX_HEADERS,
        data={"entity_identifier_type_id": type_id, "value": "UBI-999"},
    )
    assert r.status_code == 200
    assert "UBI-999" in r.text


async def test_identifiers_read_row_returns_row(client, org_and_identifier):
    oid, iid, _ = org_and_identifier
    r = await client.get(f"/admin/orgs/{oid}/identifiers/{iid}/read-row/", headers=HTMX_HEADERS)
    assert r.status_code == 200
    assert "TEST-123" in r.text
    assert "<form" not in r.text


async def test_identifiers_edit_row_returns_form(client, org_and_identifier):
    oid, iid, _ = org_and_identifier
    r = await client.get(f"/admin/orgs/{oid}/identifiers/{iid}/edit-row/", headers=HTMX_HEADERS)
    assert r.status_code == 200
    assert "<form" in r.text


async def test_identifiers_update(client, org_and_identifier):
    oid, iid, type_id = org_and_identifier
    r = await client.post(
        f"/admin/orgs/{oid}/identifiers/{iid}/edit-row/",
        headers=HTMX_HEADERS,
        data={"entity_identifier_type_id": type_id, "value": "UBI-456"},
    )
    assert r.status_code == 200
    assert "UBI-456" in r.text


async def test_identifiers_delete(client, org_and_identifier):
    oid, iid, _ = org_and_identifier
    r = await client.delete(f"/admin/orgs/{oid}/identifiers/{iid}/", headers=HTMX_HEADERS)
    assert r.status_code == 200


async def test_identifiers_delete_unknown_returns_404(client, org_id_and_type):
    oid, _ = org_id_and_type
    r = await client.delete(f"/admin/orgs/{oid}/identifiers/{generate_id()}/", headers=HTMX_HEADERS)
    assert r.status_code == 404


async def test_identifiers_create_returns_success_flash(client, org_id_and_type):
    oid, type_id = org_id_and_type
    r = await client.post(
        f"/admin/orgs/{oid}/identifiers/",
        headers=HTMX_HEADERS,
        data={"entity_identifier_type_id": type_id, "value": "FLASH-001"},
    )
    assert r.status_code == 200
    trigger = json.loads(r.headers["hx-trigger"])
    assert trigger["showFlash"]["level"] == "success"
    assert "FLASH-001" in trigger["showFlash"]["body"]


async def test_identifiers_update_returns_success_flash(client, org_and_identifier):
    oid, iid, type_id = org_and_identifier
    r = await client.post(
        f"/admin/orgs/{oid}/identifiers/{iid}/edit-row/",
        headers=HTMX_HEADERS,
        data={"entity_identifier_type_id": type_id, "value": "FLASH-002"},
    )
    assert r.status_code == 200
    trigger = json.loads(r.headers["hx-trigger"])
    assert trigger["showFlash"]["level"] == "success"
    assert "FLASH-002" in trigger["showFlash"]["body"]


async def test_identifiers_delete_returns_info_flash(client, org_and_identifier):
    oid, iid, _ = org_and_identifier
    r = await client.delete(f"/admin/orgs/{oid}/identifiers/{iid}/", headers=HTMX_HEADERS)
    assert r.status_code == 200
    trigger = json.loads(r.headers["hx-trigger"])
    assert trigger["showFlash"]["level"] == "info"
