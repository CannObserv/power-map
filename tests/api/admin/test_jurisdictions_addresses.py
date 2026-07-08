"""Integration tests for jurisdiction addresses CRUD (#280 non-HTMX confirm persistence)."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient

from src.api.main import app
from src.core.db import generate_id

pytestmark = [
    pytest.mark.integration,
]
AUTH_HEADERS = {"X-ExeDev-UserID": "usr_test", "X-ExeDev-Email": "admin@test.com"}
HTMX_HEADERS = {**AUTH_HEADERS, "HX-Request": "true"}


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


@pytest_asyncio.fixture(scope="module", loop_scope="session", autouse=True)
async def _no_address_leak(db_pool):
    """Guard against address teardown leaks (mirrors #150 guard on org/person suites)."""
    async with db_pool.acquire() as conn:
        before = await conn.fetchval("SELECT COUNT(*) FROM addresses")
    yield
    async with db_pool.acquire() as conn:
        after = await conn.fetchval("SELECT COUNT(*) FROM addresses")
    assert after == before, (
        f"jurisdiction_and_address fixture leaked {after - before} addresses row(s)"
    )


@pytest_asyncio.fixture(loop_scope="session")
async def jurisdiction_and_address(db_pool):
    jid = generate_id()
    aid = generate_id()
    eaid = generate_id()

    async with db_pool.acquire() as conn:
        type_id = await conn.fetchval(
            "SELECT id FROM jurisdiction_types WHERE slug='legislative_district'"
        )
        await conn.execute(
            "INSERT INTO jurisdictions (id, slug, name, type_id) VALUES ($1, $2, $3, $4)",
            jid,
            f"ld-{jid[-8:].lower()}",
            "Test LD",
            type_id,
        )
        await conn.execute(
            "INSERT INTO addresses (id, address_line_1, city, region, postal_code, country)"
            " VALUES ($1, '123 Main St', 'Olympia', 'WA', '98501', 'US')",
            aid,
        )
        await conn.execute(
            "INSERT INTO entity_addresses"
            " (id, entity_type, entity_id, address_id, address_type)"
            " VALUES ($1, 'jurisdiction', $2, $3, 'mailing')",
            eaid,
            jid,
            aid,
        )

    yield jid, eaid

    async with db_pool.acquire() as conn, conn.transaction():
        rows = await conn.fetch("SELECT address_id FROM entity_addresses WHERE entity_id=$1", jid)
        address_ids = [r["address_id"] for r in rows]
        await conn.execute("DELETE FROM entity_addresses WHERE entity_id=$1", jid)
        if address_ids:
            await conn.execute(
                "DELETE FROM addresses WHERE id = ANY($1::text[])"
                " AND NOT EXISTS ("
                "SELECT 1 FROM entity_addresses ea WHERE ea.address_id = addresses.id"
                ")",
                address_ids,
            )
        await conn.execute("DELETE FROM jurisdictions WHERE id=$1", jid)


async def test_addresses_create_saves(client, jurisdiction_and_address, db_pool):
    jid, existing_eaid = jurisdiction_and_address
    r = client.post(
        f"/admin/jurisdictions/{jid}/addresses/",
        headers=HTMX_HEADERS,
        data={
            "address_line_1": "456 Oak Ave",
            "city": "Seattle",
            "region": "WA",
            "postal_code": "98101",
            "address_type": "physical",
            "mode": "save",
        },
    )
    assert r.status_code == 200

    async with db_pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT a.address_line_1 FROM entity_addresses ea"
            " JOIN addresses a ON a.id = ea.address_id"
            " WHERE ea.entity_id=$1 AND ea.id != $2",
            jid,
            existing_eaid,
        )
    assert row is not None
    assert row["address_line_1"] == "456 Oak Ave"


@patch("src.api.admin.jurisdictions_addresses._NORMALIZER")
async def test_address_create_confirm_non_htmx_persists_and_redirects(
    mock_normalizer, client, jurisdiction_and_address, db_pool
):
    """#280: a non-HTMX confirm submit persists the normalized address, no data loss."""
    jid, existing_eaid = jurisdiction_and_address
    mock_normalizer.normalize = AsyncMock(
        return_value=MagicMock(
            skipped=False,
            value={
                "address_line_1": "123 MAIN ST",
                "address_line_2": None,
                "city": "SEATTLE",
                "region": "WA",
                "postal_code": "98101",
                "country": "US",
                "standardized": "123 MAIN ST SEATTLE WA 98101",
                "latitude": None,
                "longitude": None,
                "components": None,
            },
            validation_detail=None,
        )
    )
    r = client.post(
        f"/admin/jurisdictions/{jid}/addresses/",
        headers=AUTH_HEADERS,  # no HX-Request
        data={
            "address_line_1": "123 Main St",
            "city": "Seattle",
            "region": "WA",
            "postal_code": "98101",
            "address_type": "mailing",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"] == f"/admin/jurisdictions/{jid}/"

    async with db_pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT a.address_line_1, a.city, a.region, a.postal_code, a.standardized"
            " FROM entity_addresses ea JOIN addresses a ON a.id = ea.address_id"
            " WHERE ea.entity_id=$1 AND ea.id != $2",
            jid,
            existing_eaid,
        )
    assert row is not None
    assert row["address_line_1"] == "123 MAIN ST"
    assert row["standardized"] == "123 MAIN ST SEATTLE WA 98101"


@patch("src.api.admin.jurisdictions_addresses._NORMALIZER")
async def test_address_edit_confirm_non_htmx_persists_and_redirects(
    mock_normalizer, client, jurisdiction_and_address, db_pool
):
    """#280: a non-HTMX confirm edit submit persists the normalized address."""
    jid, eaid = jurisdiction_and_address
    mock_normalizer.normalize = AsyncMock(
        return_value=MagicMock(
            skipped=False,
            value={
                "address_line_1": "123 MAIN ST",
                "address_line_2": None,
                "city": "OLYMPIA",
                "region": "WA",
                "postal_code": "98501",
                "country": "US",
                "standardized": "123 MAIN ST OLYMPIA WA 98501",
                "latitude": None,
                "longitude": None,
                "components": None,
            },
            validation_detail=None,
        )
    )
    r = client.post(
        f"/admin/jurisdictions/{jid}/addresses/{eaid}/edit-row/",
        headers=AUTH_HEADERS,  # no HX-Request
        data={
            "address_line_1": "123 Main St",
            "city": "Olympia",
            "region": "WA",
            "postal_code": "98501",
            "address_type": "mailing",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"] == f"/admin/jurisdictions/{jid}/"

    async with db_pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT a.address_line_1, a.city, a.standardized"
            " FROM entity_addresses ea JOIN addresses a ON a.id = ea.address_id"
            " WHERE ea.id=$1",
            eaid,
        )
    assert row is not None
    assert row["address_line_1"] == "123 MAIN ST"
    assert row["standardized"] == "123 MAIN ST OLYMPIA WA 98501"
