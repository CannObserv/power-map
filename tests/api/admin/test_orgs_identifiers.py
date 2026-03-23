"""Integration tests for org identifiers CRUD."""

import asyncio
import os

import asyncpg
import pytest
from fastapi.testclient import TestClient

from src.api.main import app
from src.core.db import apply_schema, generate_id

pytestmark = pytest.mark.integration
AUTH_HEADERS = {"X-ExeDev-UserID": "usr_test", "X-ExeDev-Email": "admin@test.com"}
HTMX_HEADERS = {**AUTH_HEADERS, "HX-Request": "true"}


def _dsn():
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        pytest.skip("DATABASE_URL not set")
    return dsn


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture
def org_id_and_type():
    """Yields (org_id, identifier_type_id) for an org with an identifier type seeded."""
    dsn = _dsn()
    oid = generate_id()

    async def setup():
        conn = await asyncpg.connect(dsn)
        await apply_schema(conn)
        try:
            await conn.execute("INSERT INTO organizations (id) VALUES ($1)", oid)
        finally:
            await conn.close()

    async def teardown():
        conn = await asyncpg.connect(dsn)
        try:
            await conn.execute("DELETE FROM identifiers WHERE entity_id=$1", oid)
            await conn.execute("DELETE FROM organizations WHERE id=$1", oid)
        finally:
            await conn.close()

    asyncio.run(setup())

    async def get_type_id():
        conn = await asyncpg.connect(dsn)
        try:
            row = await conn.fetchrow(
                "SELECT id FROM entity_identifier_types WHERE entity_type='organization' LIMIT 1"
            )
            if not row:
                pytest.skip("No organization identifier types seeded")
            return row["id"]
        finally:
            await conn.close()

    type_id = asyncio.run(get_type_id())
    yield oid, type_id
    asyncio.run(teardown())


@pytest.fixture
def org_and_identifier(org_id_and_type):
    dsn = _dsn()
    oid, type_id = org_id_and_type
    iid = generate_id()

    async def setup():
        conn = await asyncpg.connect(dsn)
        await apply_schema(conn)
        try:
            await conn.execute(
                "INSERT INTO identifiers (id, entity_id, entity_identifier_type_id, value)"
                " VALUES ($1, $2, $3, 'TEST-123')",
                iid,
                oid,
                type_id,
            )
        finally:
            await conn.close()

    async def teardown():
        conn = await asyncpg.connect(dsn)
        try:
            await conn.execute("DELETE FROM identifiers WHERE id=$1", iid)
        finally:
            await conn.close()

    asyncio.run(setup())
    yield oid, iid, type_id
    asyncio.run(teardown())


def test_identifiers_new_row_returns_form(client, org_id_and_type):
    oid, _ = org_id_and_type
    r = client.get(f"/admin/orgs/{oid}/identifiers/new-row/", headers=HTMX_HEADERS)
    assert r.status_code == 200
    assert "<form" in r.text


def test_identifiers_create(client, org_id_and_type):
    oid, type_id = org_id_and_type
    r = client.post(
        f"/admin/orgs/{oid}/identifiers/",
        headers=HTMX_HEADERS,
        data={"entity_identifier_type_id": type_id, "value": "UBI-999"},
    )
    assert r.status_code == 200
    assert "UBI-999" in r.text


def test_identifiers_read_row_returns_row(client, org_and_identifier):
    oid, iid, _ = org_and_identifier
    r = client.get(f"/admin/orgs/{oid}/identifiers/{iid}/read-row/", headers=HTMX_HEADERS)
    assert r.status_code == 200
    assert "TEST-123" in r.text
    assert "<form" not in r.text


def test_identifiers_edit_row_returns_form(client, org_and_identifier):
    oid, iid, _ = org_and_identifier
    r = client.get(f"/admin/orgs/{oid}/identifiers/{iid}/edit-row/", headers=HTMX_HEADERS)
    assert r.status_code == 200
    assert "<form" in r.text


def test_identifiers_update(client, org_and_identifier):
    oid, iid, type_id = org_and_identifier
    r = client.post(
        f"/admin/orgs/{oid}/identifiers/{iid}/edit-row/",
        headers=HTMX_HEADERS,
        data={"entity_identifier_type_id": type_id, "value": "UBI-456"},
    )
    assert r.status_code == 200
    assert "UBI-456" in r.text


def test_identifiers_delete(client, org_and_identifier):
    oid, iid, _ = org_and_identifier
    r = client.delete(f"/admin/orgs/{oid}/identifiers/{iid}/", headers=HTMX_HEADERS)
    assert r.status_code == 200


def test_identifiers_delete_unknown_returns_404(client, org_id_and_type):
    oid, _ = org_id_and_type
    r = client.delete(f"/admin/orgs/{oid}/identifiers/{generate_id()}/", headers=HTMX_HEADERS)
    assert r.status_code == 404
