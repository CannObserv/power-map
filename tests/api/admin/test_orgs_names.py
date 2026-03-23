"""Integration tests for org names CRUD."""

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
def org_and_name():
    dsn = _dsn()
    oid, nid = generate_id(), generate_id()

    async def setup():
        conn = await asyncpg.connect(dsn)
        await apply_schema(conn)
        try:
            await conn.execute("INSERT INTO organizations (id) VALUES ($1)", oid)
            await conn.execute(
                "INSERT INTO organization_names (id, organization_id, name, is_canonical)"
                " VALUES ($1, $2, 'Original Name', TRUE)",
                nid,
                oid,
            )
        finally:
            await conn.close()

    async def teardown():
        conn = await asyncpg.connect(dsn)
        try:
            await conn.execute("DELETE FROM organization_names WHERE organization_id=$1", oid)
            await conn.execute("DELETE FROM organizations WHERE id=$1", oid)
        finally:
            await conn.close()

    asyncio.run(setup())
    yield oid, nid
    asyncio.run(teardown())


def test_names_new_row_returns_form(client, org_and_name):
    oid, _ = org_and_name
    r = client.get(f"/admin/orgs/{oid}/names/new-row/", headers=HTMX_HEADERS)
    assert r.status_code == 200
    assert "<form" in r.text


def test_names_create(client, org_and_name):
    oid, _ = org_and_name
    r = client.post(
        f"/admin/orgs/{oid}/names/",
        headers=HTMX_HEADERS,
        data={"name": "DBA Name", "name_type": "dba", "is_canonical": ""},
    )
    assert r.status_code == 200
    assert "DBA Name" in r.text


def test_names_read_row_returns_row(client, org_and_name):
    oid, nid = org_and_name
    r = client.get(f"/admin/orgs/{oid}/names/{nid}/read-row/", headers=HTMX_HEADERS)
    assert r.status_code == 200
    assert "Original Name" in r.text
    assert "<form" not in r.text


def test_names_edit_row_returns_form(client, org_and_name):
    oid, nid = org_and_name
    r = client.get(f"/admin/orgs/{oid}/names/{nid}/edit-row/", headers=HTMX_HEADERS)
    assert r.status_code == 200
    assert "Original Name" in r.text


def test_names_update(client, org_and_name):
    oid, nid = org_and_name
    r = client.post(
        f"/admin/orgs/{oid}/names/{nid}/edit-row/",
        headers=HTMX_HEADERS,
        data={"name": "Updated Name", "name_type": "legal", "is_canonical": "true"},
    )
    assert r.status_code == 200
    assert "Updated Name" in r.text


def test_names_delete(client, org_and_name):
    dsn = _dsn()
    oid, _ = org_and_name
    nid2 = generate_id()

    async def add():
        conn = await asyncpg.connect(dsn)
        await apply_schema(conn)
        try:
            await conn.execute(
                "INSERT INTO organization_names (id, organization_id, name, is_canonical)"
                " VALUES ($1, $2, 'Former Name', FALSE)",
                nid2,
                oid,
            )
        finally:
            await conn.close()

    asyncio.run(add())
    r = client.delete(f"/admin/orgs/{oid}/names/{nid2}/", headers=HTMX_HEADERS)
    assert r.status_code == 200


def test_names_delete_unknown_returns_404(client, org_and_name):
    oid, _ = org_and_name
    r = client.delete(f"/admin/orgs/{oid}/names/{generate_id()}/", headers=HTMX_HEADERS)
    assert r.status_code == 404
