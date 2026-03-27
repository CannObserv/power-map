"""Integration tests for org addresses CRUD."""

import asyncio
import json
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
def org_and_address():
    dsn = _dsn()
    oid = generate_id()
    aid = generate_id()   # addresses.id
    eaid = generate_id()  # entity_addresses.id

    async def setup():
        conn = await asyncpg.connect(dsn)
        await apply_schema(conn)
        try:
            await conn.execute("INSERT INTO organizations (id) VALUES ($1)", oid)
            await conn.execute(
                "INSERT INTO addresses (id, address_line_1, city, region, postal_code)"
                " VALUES ($1, '123 Main St', 'Olympia', 'WA', '98501')",
                aid,
            )
            await conn.execute(
                "INSERT INTO entity_addresses"
                " (id, entity_type, entity_id, address_id, address_type)"
                " VALUES ($1, 'organization', $2, $3, 'mailing')",
                eaid,
                oid,
                aid,
            )
        finally:
            await conn.close()

    async def teardown():
        conn = await asyncpg.connect(dsn)
        try:
            await conn.execute("DELETE FROM entity_addresses WHERE entity_id=$1", oid)
            await conn.execute("DELETE FROM addresses WHERE id=$1", aid)
            await conn.execute("DELETE FROM organizations WHERE id=$1", oid)
        finally:
            await conn.close()

    asyncio.run(setup())
    yield oid, eaid
    asyncio.run(teardown())


def test_addresses_new_row_returns_form(client, org_and_address):
    oid, _ = org_and_address
    r = client.get(f"/admin/orgs/{oid}/addresses/new-row/", headers=HTMX_HEADERS)
    assert r.status_code == 200
    assert "<form" in r.text


def test_addresses_create(client, org_and_address):
    oid, _ = org_and_address
    r = client.post(
        f"/admin/orgs/{oid}/addresses/",
        headers=HTMX_HEADERS,
        data={
            "address_line_1": "456 Oak Ave",
            "city": "Seattle",
            "region": "WA",
            "postal_code": "98101",
            "address_type": "physical",
        },
    )
    assert r.status_code == 200
    assert "456 Oak Ave" in r.text


def test_addresses_read_row_returns_row(client, org_and_address):
    oid, eaid = org_and_address
    r = client.get(f"/admin/orgs/{oid}/addresses/{eaid}/read-row/", headers=HTMX_HEADERS)
    assert r.status_code == 200
    assert "123 Main St" in r.text
    assert "<form" not in r.text


def test_addresses_delete_also_removes_address_row(client, org_and_address):
    dsn = _dsn()
    oid, eaid = org_and_address

    async def get_address_id():
        conn = await asyncpg.connect(dsn)
        try:
            return await conn.fetchval(
                "SELECT address_id FROM entity_addresses WHERE id=$1", eaid
            )
        finally:
            await conn.close()

    async def address_exists(aid):
        conn = await asyncpg.connect(dsn)
        try:
            return await conn.fetchval("SELECT id FROM addresses WHERE id=$1", aid)
        finally:
            await conn.close()

    aid = asyncio.run(get_address_id())
    assert aid is not None
    r = client.delete(f"/admin/orgs/{oid}/addresses/{eaid}/", headers=HTMX_HEADERS)
    assert r.status_code == 200
    assert asyncio.run(address_exists(aid)) is None


def test_addresses_edit_row_returns_form(client, org_and_address):
    oid, eaid = org_and_address
    r = client.get(f"/admin/orgs/{oid}/addresses/{eaid}/edit-row/", headers=HTMX_HEADERS)
    assert r.status_code == 200
    assert "123 Main St" in r.text and "<form" in r.text


def test_addresses_update(client, org_and_address):
    oid, eaid = org_and_address
    r = client.post(
        f"/admin/orgs/{oid}/addresses/{eaid}/edit-row/",
        headers=HTMX_HEADERS,
        data={
            "address_line_1": "789 Pine Rd",
            "city": "Tacoma",
            "region": "WA",
            "postal_code": "98402",
            "address_type": "physical",
        },
    )
    assert r.status_code == 200
    assert "789 Pine Rd" in r.text


def test_addresses_delete(client, org_and_address):
    oid, eaid = org_and_address
    r = client.delete(f"/admin/orgs/{oid}/addresses/{eaid}/", headers=HTMX_HEADERS)
    assert r.status_code == 200


def test_addresses_delete_unknown_returns_404(client, org_and_address):
    oid, _ = org_and_address
    r = client.delete(f"/admin/orgs/{oid}/addresses/{generate_id()}/", headers=HTMX_HEADERS)
    assert r.status_code == 404


def test_address_form_row_has_form_group(client, org_and_address):
    oid, _ = org_and_address
    r = client.get(f"/admin/orgs/{oid}/addresses/new-row/", headers=HTMX_HEADERS)
    assert r.status_code == 200
    assert "form-group" in r.text


def test_addresses_create_returns_success_flash(client, org_and_address):
    oid, _ = org_and_address
    r = client.post(
        f"/admin/orgs/{oid}/addresses/",
        headers=HTMX_HEADERS,
        data={"address_line_1": "1 Flash St", "city": "Seattle", "region": "WA",
              "postal_code": "98101", "address_type": "physical"},
    )
    assert r.status_code == 200
    trigger = json.loads(r.headers["hx-trigger"])
    assert trigger["showFlash"]["level"] == "success"


def test_addresses_update_returns_success_flash(client, org_and_address):
    oid, eaid = org_and_address
    r = client.post(
        f"/admin/orgs/{oid}/addresses/{eaid}/edit-row/",
        headers=HTMX_HEADERS,
        data={"address_line_1": "999 Flash Ave", "city": "Olympia", "region": "WA",
              "postal_code": "98501", "address_type": "mailing"},
    )
    assert r.status_code == 200
    trigger = json.loads(r.headers["hx-trigger"])
    assert trigger["showFlash"]["level"] == "success"


def test_addresses_delete_returns_info_flash(client, org_and_address):
    oid, eaid = org_and_address
    r = client.delete(f"/admin/orgs/{oid}/addresses/{eaid}/", headers=HTMX_HEADERS)
    assert r.status_code == 200
    trigger = json.loads(r.headers["hx-trigger"])
    assert trigger["showFlash"]["level"] == "info"


def test_address_create_blank_returns_form_with_error(client, org_and_address):
    oid, _ = org_and_address
    r = client.post(
        f"/admin/orgs/{oid}/addresses/",
        headers=HTMX_HEADERS,
        data={"address_line_1": "", "city": "", "region": "", "postal_code": "",
              "address_type": "mailing"},
    )
    assert r.status_code == 200
    assert "<form" in r.text
    assert "required" in r.text.lower()


def test_address_edit_blank_returns_form_with_error(client, org_and_address):
    oid, eaid = org_and_address
    r = client.post(
        f"/admin/orgs/{oid}/addresses/{eaid}/edit-row/",
        headers=HTMX_HEADERS,
        data={"address_line_1": "", "city": "", "region": "", "postal_code": "",
              "address_type": "mailing"},
    )
    assert r.status_code == 200
    assert "<form" in r.text
    assert "required" in r.text.lower()


@pytest.mark.integration
def test_addresses_table_has_normalizer_columns():
    dsn = _dsn()

    async def check():
        conn = await asyncpg.connect(dsn)
        try:
            await apply_schema(conn)
            cols = await conn.fetch(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name='addresses' AND table_schema='public'"
            )
            return {r["column_name"] for r in cols}
        finally:
            await conn.close()

    col_names = asyncio.run(check())
    assert "latitude" in col_names
    assert "longitude" in col_names
    assert "components" in col_names
