"""Integration tests for org contact methods CRUD."""

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
def org_and_contact():
    dsn = _dsn()
    oid, cid = generate_id(), generate_id()

    async def setup():
        conn = await asyncpg.connect(dsn)
        await apply_schema(conn)
        try:
            await conn.execute("INSERT INTO organizations (id) VALUES ($1)", oid)
            await conn.execute(
                "INSERT INTO contact_methods"
                " (id, entity_type, entity_id, contact_type, value)"
                " VALUES ($1, 'organization', $2, 'phone', '+13605551234')",
                cid,
                oid,
            )
        finally:
            await conn.close()

    async def teardown():
        conn = await asyncpg.connect(dsn)
        try:
            await conn.execute("DELETE FROM contact_methods WHERE entity_id=$1", oid)
            await conn.execute("DELETE FROM organizations WHERE id=$1", oid)
        finally:
            await conn.close()

    asyncio.run(setup())
    yield oid, cid
    asyncio.run(teardown())


def test_contacts_new_row_returns_form(client, org_and_contact):
    oid, _ = org_and_contact
    r = client.get(f"/admin/orgs/{oid}/contacts/new-row/", headers=HTMX_HEADERS)
    assert r.status_code == 200
    assert "<form" in r.text


def test_contacts_create(client, org_and_contact):
    oid, _ = org_and_contact
    r = client.post(
        f"/admin/orgs/{oid}/contacts/",
        headers=HTMX_HEADERS,
        data={"contact_type": "email", "value": "info@example.com"},
    )
    assert r.status_code == 200
    assert "info@example.com" in r.text


def test_contacts_read_row_returns_row(client, org_and_contact):
    oid, cid = org_and_contact
    r = client.get(f"/admin/orgs/{oid}/contacts/{cid}/read-row/", headers=HTMX_HEADERS)
    assert r.status_code == 200
    assert "+13605551234" in r.text
    assert "<form" not in r.text


def test_contacts_edit_row_returns_form(client, org_and_contact):
    oid, cid = org_and_contact
    r = client.get(f"/admin/orgs/{oid}/contacts/{cid}/edit-row/", headers=HTMX_HEADERS)
    assert r.status_code == 200
    assert "<form" in r.text


def test_contacts_update(client, org_and_contact):
    oid, cid = org_and_contact
    r = client.post(
        f"/admin/orgs/{oid}/contacts/{cid}/edit-row/",
        headers=HTMX_HEADERS,
        data={"contact_type": "phone", "value": "+12065559876"},
    )
    assert r.status_code == 200
    assert "+12065559876" in r.text


def test_contacts_delete(client, org_and_contact):
    oid, cid = org_and_contact
    r = client.delete(f"/admin/orgs/{oid}/contacts/{cid}/", headers=HTMX_HEADERS)
    assert r.status_code == 200


def test_contacts_delete_unknown_returns_404(client, org_and_contact):
    oid, _ = org_and_contact
    r = client.delete(f"/admin/orgs/{oid}/contacts/{generate_id()}/", headers=HTMX_HEADERS)
    assert r.status_code == 404
