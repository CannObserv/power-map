"""Integration tests for org identifiers CRUD."""

import json

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient

from src.api.main import app
from src.core.db import generate_id

pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
]
AUTH_HEADERS = {"X-ExeDev-UserID": "usr_test", "X-ExeDev-Email": "admin@test.com"}
HTMX_HEADERS = {**AUTH_HEADERS, "HX-Request": "true"}


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


@pytest_asyncio.fixture(loop_scope="session")
async def org_id_and_type(db_pool):
    """Yields (org_id, identifier_type_id) for an org with an identifier type seeded."""
    oid = generate_id()

    async with db_pool.acquire() as conn:
        await conn.execute("INSERT INTO organizations (id) VALUES ($1)", oid)
        row = await conn.fetchrow(
            "SELECT id FROM entity_identifier_types WHERE entity_type='organization' LIMIT 1"
        )
        if not row:
            pytest.skip("No organization identifier types seeded")
        type_id = row["id"]

    yield oid, type_id

    async with db_pool.acquire() as conn:
        await conn.execute("DELETE FROM identifiers WHERE entity_id=$1", oid)
        await conn.execute("DELETE FROM organizations WHERE id=$1", oid)


@pytest_asyncio.fixture(loop_scope="session")
async def org_and_identifier(db_pool, org_id_and_type):
    oid, type_id = org_id_and_type
    iid = generate_id()

    async with db_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO identifiers (id, entity_id, entity_identifier_type_id, value)"
            " VALUES ($1, $2, $3, 'TEST-123')",
            iid,
            oid,
            type_id,
        )

    yield oid, iid, type_id

    async with db_pool.acquire() as conn:
        await conn.execute("DELETE FROM identifiers WHERE id=$1", iid)


async def test_identifier_form_row_has_form_group(client, org_id_and_type):
    oid, _ = org_id_and_type
    r = client.get(f"/admin/orgs/{oid}/identifiers/new-row/", headers=HTMX_HEADERS)
    assert r.status_code == 200
    assert "form-group" in r.text


async def test_identifiers_new_row_returns_form(client, org_id_and_type):
    oid, _ = org_id_and_type
    r = client.get(f"/admin/orgs/{oid}/identifiers/new-row/", headers=HTMX_HEADERS)
    assert r.status_code == 200
    assert "<form" in r.text


async def test_identifiers_create(client, org_id_and_type):
    oid, type_id = org_id_and_type
    r = client.post(
        f"/admin/orgs/{oid}/identifiers/",
        headers=HTMX_HEADERS,
        data={"entity_identifier_type_id": type_id, "value": "UBI-999"},
    )
    assert r.status_code == 200
    assert "UBI-999" in r.text


async def test_identifiers_read_row_returns_row(client, org_and_identifier):
    oid, iid, _ = org_and_identifier
    r = client.get(f"/admin/orgs/{oid}/identifiers/{iid}/read-row/", headers=HTMX_HEADERS)
    assert r.status_code == 200
    assert "TEST-123" in r.text
    assert "<form" not in r.text


async def test_identifiers_edit_row_returns_form(client, org_and_identifier):
    oid, iid, _ = org_and_identifier
    r = client.get(f"/admin/orgs/{oid}/identifiers/{iid}/edit-row/", headers=HTMX_HEADERS)
    assert r.status_code == 200
    assert "<form" in r.text


async def test_identifiers_update(client, org_and_identifier):
    oid, iid, type_id = org_and_identifier
    r = client.post(
        f"/admin/orgs/{oid}/identifiers/{iid}/edit-row/",
        headers=HTMX_HEADERS,
        data={"entity_identifier_type_id": type_id, "value": "UBI-456"},
    )
    assert r.status_code == 200
    assert "UBI-456" in r.text


async def test_identifiers_delete(client, org_and_identifier):
    oid, iid, _ = org_and_identifier
    r = client.delete(f"/admin/orgs/{oid}/identifiers/{iid}/", headers=HTMX_HEADERS)
    assert r.status_code == 200


async def test_identifiers_delete_unknown_returns_404(client, org_id_and_type):
    oid, _ = org_id_and_type
    r = client.delete(f"/admin/orgs/{oid}/identifiers/{generate_id()}/", headers=HTMX_HEADERS)
    assert r.status_code == 404


async def test_identifiers_create_returns_success_flash(client, org_id_and_type):
    oid, type_id = org_id_and_type
    r = client.post(
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
    r = client.post(
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
    r = client.delete(f"/admin/orgs/{oid}/identifiers/{iid}/", headers=HTMX_HEADERS)
    assert r.status_code == 200
    trigger = json.loads(r.headers["hx-trigger"])
    assert trigger["showFlash"]["level"] == "info"
