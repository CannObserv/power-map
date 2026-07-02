"""Integration tests for org addresses CRUD."""

import json
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
    """Guard against re-introducing the org_and_address teardown leak (#150).

    Snapshots the ``addresses`` rowcount before this module's tests run and
    asserts equality after — fails loudly if any fixture in this module
    stops cleaning up newly-created ``addresses`` rows.
    """
    async with db_pool.acquire() as conn:
        before = await conn.fetchval("SELECT COUNT(*) FROM addresses")
    yield
    async with db_pool.acquire() as conn:
        after = await conn.fetchval("SELECT COUNT(*) FROM addresses")
    assert after == before, (
        f"org_and_address fixture leaked {after - before} addresses row(s) — see #150"
    )


@pytest_asyncio.fixture(loop_scope="session")
async def org_and_address(db_pool):
    oid = generate_id()
    aid = generate_id()
    eaid = generate_id()

    async with db_pool.acquire() as conn:
        await conn.execute("INSERT INTO organizations (id) VALUES ($1)", oid)
        await conn.execute(
            "INSERT INTO addresses (id, address_line_1, city, region, postal_code, country)"
            " VALUES ($1, '123 Main St', 'Olympia', 'WA', '98501', 'US')",
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

    yield oid, eaid

    async with db_pool.acquire() as conn, conn.transaction():
        rows = await conn.fetch("SELECT address_id FROM entity_addresses WHERE entity_id=$1", oid)
        address_ids = [r["address_id"] for r in rows]
        await conn.execute("DELETE FROM entity_addresses WHERE entity_id=$1", oid)
        if address_ids:
            await conn.execute(
                "DELETE FROM addresses WHERE id = ANY($1::text[])"
                " AND NOT EXISTS ("
                "SELECT 1 FROM entity_addresses ea WHERE ea.address_id = addresses.id"
                ")",
                address_ids,
            )
        await conn.execute("DELETE FROM organizations WHERE id=$1", oid)


async def test_addresses_new_row_returns_form(client, org_and_address):
    oid, _ = org_and_address
    r = client.get(f"/admin/orgs/{oid}/addresses/new-row/", headers=HTMX_HEADERS)
    assert r.status_code == 200
    assert "<form" in r.text


async def test_addresses_create(client, org_and_address, db_pool):
    oid, existing_eaid = org_and_address
    r = client.post(
        f"/admin/orgs/{oid}/addresses/",
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
            "SELECT a.address_line_1, a.city, a.region, a.postal_code, ea.address_type"
            " FROM entity_addresses ea JOIN addresses a ON a.id = ea.address_id"
            " WHERE ea.entity_id=$1 AND ea.id != $2",
            oid,
            existing_eaid,
        )
    assert row is not None
    assert row["address_line_1"] == "456 Oak Ave"
    assert row["city"] == "Seattle"
    assert row["region"] == "WA"
    assert row["postal_code"] == "98101"
    assert row["address_type"] == "physical"


async def test_addresses_read_row_returns_row(client, org_and_address):
    oid, eaid = org_and_address
    r = client.get(f"/admin/orgs/{oid}/addresses/{eaid}/read-row/", headers=HTMX_HEADERS)
    assert r.status_code == 200
    assert "123 Main St" in r.text
    assert "<form" not in r.text


async def test_addresses_delete_also_removes_address_row(client, org_and_address, db_pool):
    oid, eaid = org_and_address

    async with db_pool.acquire() as conn:
        aid = await conn.fetchval("SELECT address_id FROM entity_addresses WHERE id=$1", eaid)
    assert aid is not None

    r = client.delete(f"/admin/orgs/{oid}/addresses/{eaid}/", headers=HTMX_HEADERS)
    assert r.status_code == 200

    async with db_pool.acquire() as conn:
        exists = await conn.fetchval("SELECT id FROM addresses WHERE id=$1", aid)
    assert exists is None


async def test_addresses_edit_row_returns_form(client, org_and_address):
    oid, eaid = org_and_address
    r = client.get(f"/admin/orgs/{oid}/addresses/{eaid}/edit-row/", headers=HTMX_HEADERS)
    assert r.status_code == 200
    assert "123 Main St" in r.text and "<form" in r.text


async def test_addresses_update(client, org_and_address, db_pool):
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
            "mode": "save",
        },
    )
    assert r.status_code == 200

    async with db_pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT a.address_line_1, a.city, a.region, a.postal_code, ea.address_type"
            " FROM entity_addresses ea JOIN addresses a ON a.id = ea.address_id"
            " WHERE ea.id=$1",
            eaid,
        )
    assert row is not None
    assert row["address_line_1"] == "789 Pine Rd"
    assert row["city"] == "Tacoma"
    assert row["region"] == "WA"
    assert row["postal_code"] == "98402"
    assert row["address_type"] == "physical"


async def test_addresses_delete(client, org_and_address):
    oid, eaid = org_and_address
    r = client.delete(f"/admin/orgs/{oid}/addresses/{eaid}/", headers=HTMX_HEADERS)
    assert r.status_code == 200


async def test_addresses_delete_unknown_returns_404(client, org_and_address):
    oid, _ = org_and_address
    r = client.delete(f"/admin/orgs/{oid}/addresses/{generate_id()}/", headers=HTMX_HEADERS)
    assert r.status_code == 404


async def test_address_form_row_has_form_group(client, org_and_address):
    oid, _ = org_and_address
    r = client.get(f"/admin/orgs/{oid}/addresses/new-row/", headers=HTMX_HEADERS)
    assert r.status_code == 200
    assert "form-group" in r.text


async def test_addresses_create_returns_success_flash(client, org_and_address):
    oid, _ = org_and_address
    r = client.post(
        f"/admin/orgs/{oid}/addresses/",
        headers=HTMX_HEADERS,
        data={
            "address_line_1": "1 Flash St",
            "city": "Seattle",
            "region": "WA",
            "postal_code": "98101",
            "address_type": "physical",
            "mode": "save",
        },
    )
    assert r.status_code == 200
    trigger = json.loads(r.headers["hx-trigger"])
    assert trigger["showFlash"]["level"] == "success"


async def test_addresses_update_returns_success_flash(client, org_and_address):
    oid, eaid = org_and_address
    r = client.post(
        f"/admin/orgs/{oid}/addresses/{eaid}/edit-row/",
        headers=HTMX_HEADERS,
        data={
            "address_line_1": "999 Flash Ave",
            "city": "Olympia",
            "region": "WA",
            "postal_code": "98501",
            "address_type": "mailing",
            "mode": "save",
        },
    )
    assert r.status_code == 200
    trigger = json.loads(r.headers["hx-trigger"])
    assert trigger["showFlash"]["level"] == "success"


async def test_addresses_delete_returns_info_flash(client, org_and_address):
    oid, eaid = org_and_address
    r = client.delete(f"/admin/orgs/{oid}/addresses/{eaid}/", headers=HTMX_HEADERS)
    assert r.status_code == 200
    trigger = json.loads(r.headers["hx-trigger"])
    assert trigger["showFlash"]["level"] == "info"


async def test_address_create_blank_returns_form_with_error(client, org_and_address):
    oid, _ = org_and_address
    r = client.post(
        f"/admin/orgs/{oid}/addresses/",
        headers=HTMX_HEADERS,
        data={
            "address_line_1": "",
            "city": "",
            "region": "",
            "postal_code": "",
            "address_type": "mailing",
        },
    )
    assert r.status_code == 200
    assert "<form" in r.text
    assert "required" in r.text.lower()


async def test_address_edit_blank_returns_form_with_error(client, org_and_address):
    oid, eaid = org_and_address
    r = client.post(
        f"/admin/orgs/{oid}/addresses/{eaid}/edit-row/",
        headers=HTMX_HEADERS,
        data={
            "address_line_1": "",
            "city": "",
            "region": "",
            "postal_code": "",
            "address_type": "mailing",
        },
    )
    assert r.status_code == 200
    assert "<form" in r.text
    assert "required" in r.text.lower()


async def test_address_create_mode_save_invalid_lat_returns_form_with_error(
    client, org_and_address
):
    oid, _ = org_and_address
    r = client.post(
        f"/admin/orgs/{oid}/addresses/",
        headers=HTMX_HEADERS,
        data={
            "address_line_1": "123 Main St",
            "city": "Seattle",
            "region": "WA",
            "postal_code": "98101",
            "address_type": "mailing",
            "mode": "save",
            "latitude": "not-a-float",
        },
    )
    assert r.status_code == 200
    assert "<form" in r.text
    assert "Invalid address data" in r.text


async def test_address_edit_mode_save_invalid_components_returns_form_with_error(
    client, org_and_address
):
    oid, eaid = org_and_address
    r = client.post(
        f"/admin/orgs/{oid}/addresses/{eaid}/edit-row/",
        headers=HTMX_HEADERS,
        data={
            "address_line_1": "123 Main St",
            "city": "Olympia",
            "region": "WA",
            "postal_code": "98501",
            "address_type": "mailing",
            "mode": "save",
            "components": "{bad json",
        },
    )
    assert r.status_code == 200
    assert "<form" in r.text
    assert "Invalid address data" in r.text


async def test_address_create_mode_save_stores_standardized(client, org_and_address):
    oid, _ = org_and_address
    r = client.post(
        f"/admin/orgs/{oid}/addresses/",
        headers=HTMX_HEADERS,
        data={
            "address_line_1": "456 OAK AVE",
            "city": "SEATTLE",
            "region": "WA",
            "postal_code": "98101",
            "address_type": "mailing",
            "mode": "save",
            "standardized": "456 OAK AVE SEATTLE WA 98101",
            "latitude": "47.6062",
            "longitude": "-122.3321",
            "components": '{"spec":"usps-pub28","spec_version":"unknown","values":{}}',
        },
    )
    assert r.status_code == 200
    assert "456 OAK AVE SEATTLE WA 98101" in r.text
    assert "<form" not in r.text


async def test_address_edit_mode_save_stores_standardized(client, org_and_address):
    oid, eaid = org_and_address
    r = client.post(
        f"/admin/orgs/{oid}/addresses/{eaid}/edit-row/",
        headers=HTMX_HEADERS,
        data={
            "address_line_1": "123 MAIN ST",
            "city": "OLYMPIA",
            "region": "WA",
            "postal_code": "98501",
            "address_type": "mailing",
            "mode": "save",
            "standardized": "123 MAIN ST OLYMPIA WA 98501",
        },
    )
    assert r.status_code == 200
    assert "123 MAIN ST OLYMPIA WA 98501" in r.text
    assert "<form" not in r.text


async def test_address_create_mode_edit_returns_prefilled_form(client, org_and_address):
    oid, _ = org_and_address
    r = client.post(
        f"/admin/orgs/{oid}/addresses/",
        headers=HTMX_HEADERS,
        data={
            "address_line_1": "789 PINE RD",
            "city": "TACOMA",
            "region": "WA",
            "postal_code": "98402",
            "address_type": "physical",
            "mode": "edit",
        },
    )
    assert r.status_code == 200
    assert "<form" in r.text
    assert "789 PINE RD" in r.text


@patch("src.api.admin.orgs_addresses._NORMALIZER")
async def test_address_create_confirm_shows_confirm_modal(mock_normalizer, client, org_and_address):
    oid, _ = org_and_address
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
        f"/admin/orgs/{oid}/addresses/",
        headers=HTMX_HEADERS,
        data={
            "address_line_1": "123 Main St",
            "city": "Seattle",
            "region": "WA",
            "postal_code": "98101",
            "address_type": "mailing",
        },
    )
    assert r.status_code == 200
    assert r.headers.get("hx-retarget") == "#address-confirm-portal"
    assert r.headers.get("hx-reswap") == "innerHTML"
    assert "123 MAIN ST SEATTLE WA 98101" in r.text
    assert "Accept" in r.text
    assert "Keep my input" in r.text


@patch("src.api.admin.orgs_addresses._NORMALIZER")
async def test_address_create_confirm_saves_directly_when_no_standardized(
    mock_normalizer, client, org_and_address
):
    oid, _ = org_and_address
    mock_normalizer.normalize = AsyncMock(
        return_value=MagicMock(
            skipped=False,
            value={
                "standardized": None,
                "address_line_1": "123 Main St",
                "city": "Seattle",
                "region": "WA",
                "postal_code": "98101",
                "country": "US",
                "address_line_2": None,
                "latitude": None,
                "longitude": None,
                "components": None,
            },
            validation_detail=None,
        )
    )
    r = client.post(
        f"/admin/orgs/{oid}/addresses/",
        headers=HTMX_HEADERS,
        data={
            "address_line_1": "123 Main St",
            "city": "Seattle",
            "region": "WA",
            "postal_code": "98101",
            "address_type": "mailing",
        },
    )
    assert r.status_code == 200
    assert "<form" not in r.text
    assert "Accept" not in r.text


@patch("src.api.admin.orgs_addresses._NORMALIZER")
async def test_address_confirm_shows_validation_status(mock_normalizer, client, org_and_address):
    oid, _ = org_and_address
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
                "latitude": 47.6062,
                "longitude": -122.3321,
                "components": None,
            },
            validation_detail={
                "status": "confirmed",
                "dpv_match_code": "Y",
                "provider": "usps",
            },
        )
    )
    r = client.post(
        f"/admin/orgs/{oid}/addresses/",
        headers=HTMX_HEADERS,
        data={
            "address_line_1": "123 Main St",
            "city": "Seattle",
            "region": "WA",
            "postal_code": "98101",
            "address_type": "mailing",
        },
    )
    assert r.status_code == 200
    assert r.headers.get("hx-retarget") == "#address-confirm-portal"
    assert "confirmed" in r.text
    assert "usps" in r.text.lower()


@patch("src.api.admin.orgs_addresses._NORMALIZER")
async def test_address_edit_confirm_shows_confirm_modal(mock_normalizer, client, org_and_address):
    oid, eaid = org_and_address
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
        f"/admin/orgs/{oid}/addresses/{eaid}/edit-row/",
        headers=HTMX_HEADERS,
        data={
            "address_line_1": "123 Main St",
            "city": "Olympia",
            "region": "WA",
            "postal_code": "98501",
            "address_type": "mailing",
        },
    )
    assert r.status_code == 200
    assert r.headers.get("hx-retarget") == "#address-confirm-portal"
    assert r.headers.get("hx-reswap") == "innerHTML"
    assert "123 MAIN ST OLYMPIA WA 98501" in r.text
    assert "Accept" in r.text
    assert "Keep my input" in r.text


@patch("src.api.admin.orgs_addresses._NORMALIZER")
async def test_address_create_confirm_non_htmx_redirects(mock_normalizer, client, org_and_address):
    oid, _ = org_and_address
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
        f"/admin/orgs/{oid}/addresses/",
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
    assert r.headers["location"] == f"/admin/orgs/{oid}/"


@patch("src.api.admin.orgs_addresses._NORMALIZER")
async def test_address_edit_confirm_non_htmx_redirects(mock_normalizer, client, org_and_address):
    oid, eaid = org_and_address
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
        f"/admin/orgs/{oid}/addresses/{eaid}/edit-row/",
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
    assert r.headers["location"] == f"/admin/orgs/{oid}/"


@pytest.mark.integration
async def test_addresses_table_has_normalizer_columns(db_pool):
    async with db_pool.acquire() as conn:
        cols = await conn.fetch(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name='addresses' AND table_schema='public'"
        )
    col_names = {r["column_name"] for r in cols}
    assert "latitude" in col_names
    assert "longitude" in col_names
    assert "components" in col_names


async def test_address_create_persists_country(client, org_and_address):
    oid, _ = org_and_address
    r = client.post(
        f"/admin/orgs/{oid}/addresses/",
        headers=HTMX_HEADERS,
        data={
            "address_line_1": "10 Downing St",
            "city": "London",
            "postal_code": "SW1A 2AA",
            "address_type": "physical",
            "country": "GB",
            "mode": "save",
        },
    )
    assert r.status_code == 200
    assert "<form" not in r.text
    # Country shown for non-US
    assert "GB" in r.text


async def test_address_edit_persists_country(client, org_and_address):
    oid, eaid = org_and_address
    r = client.post(
        f"/admin/orgs/{oid}/addresses/{eaid}/edit-row/",
        headers=HTMX_HEADERS,
        data={
            "address_line_1": "10 Downing St",
            "city": "London",
            "postal_code": "SW1A 2AA",
            "address_type": "physical",
            "country": "GB",
            "mode": "save",
        },
    )
    assert r.status_code == 200
    assert "GB" in r.text


async def test_address_read_row_returns_country(client, org_and_address, db_pool):
    """After creating a GB address, read-row returns the country."""
    oid, _ = org_and_address

    aid = generate_id()
    eaid = generate_id()
    async with db_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO addresses (id, address_line_1, city, postal_code, country)"
            " VALUES ($1, '10 Downing St', 'London', 'SW1A 2AA', 'GB')",
            aid,
        )
        await conn.execute(
            "INSERT INTO entity_addresses"
            " (id, entity_type, entity_id, address_id, address_type)"
            " VALUES ($1, 'organization', $2, $3, 'physical')",
            eaid,
            oid,
            aid,
        )

    try:
        r = client.get(f"/admin/orgs/{oid}/addresses/{eaid}/read-row/", headers=HTMX_HEADERS)
        assert r.status_code == 200
        assert "GB" in r.text
    finally:
        async with db_pool.acquire() as conn:
            await conn.execute("DELETE FROM entity_addresses WHERE id=$1", eaid)
            await conn.execute("DELETE FROM addresses WHERE id=$1", aid)


async def test_address_us_country_not_shown_in_read_row(client, org_and_address):
    """US country is implicit — not shown in the read row."""
    oid, eaid = org_and_address
    r = client.get(f"/admin/orgs/{oid}/addresses/{eaid}/read-row/", headers=HTMX_HEADERS)
    assert r.status_code == 200
    assert ">US<" not in r.text


@pytest.mark.integration
@patch("src.api.admin.orgs_addresses._NORMALIZER")
async def test_confirm_modal_keep_my_input_has_country(mock_normalizer, client, org_and_address):
    """Keep my input form must include country hidden field."""
    oid, _ = org_and_address
    mock_normalizer.normalize = AsyncMock(
        return_value=MagicMock(
            skipped=False,
            value={
                "address_line_1": "10 DOWNING ST",
                "address_line_2": None,
                "city": "LONDON",
                "region": None,
                "postal_code": "SW1A 2AA",
                "country": "GB",
                "standardized": "10 DOWNING ST LONDON SW1A 2AA",
                "latitude": None,
                "longitude": None,
                "components": None,
            },
            validation_detail=None,
        )
    )
    r = client.post(
        f"/admin/orgs/{oid}/addresses/",
        headers=HTMX_HEADERS,
        data={
            "address_line_1": "10 Downing St",
            "city": "London",
            "postal_code": "SW1A 2AA",
            "address_type": "mailing",
            "country": "GB",
        },
    )
    assert r.status_code == 200
    assert r.headers.get("hx-retarget") == "#address-confirm-portal"
    # Both forms (Keep my input and Accept) must carry country
    assert r.text.count('name="country"') == 2


@pytest.mark.integration
@patch("src.api.admin.orgs_addresses._NORMALIZER")
async def test_confirm_modal_shows_country_in_you_entered_when_non_us(
    mock_normalizer, client, org_and_address
):
    oid, _ = org_and_address
    mock_normalizer.normalize = AsyncMock(
        return_value=MagicMock(
            skipped=False,
            value={
                "address_line_1": "10 DOWNING ST",
                "address_line_2": None,
                "city": "LONDON",
                "region": None,
                "postal_code": "SW1A 2AA",
                "country": "GB",
                "standardized": "10 DOWNING ST LONDON SW1A 2AA",
                "latitude": None,
                "longitude": None,
                "components": None,
            },
            validation_detail=None,
        )
    )
    r = client.post(
        f"/admin/orgs/{oid}/addresses/",
        headers=HTMX_HEADERS,
        data={
            "address_line_1": "10 Downing St",
            "city": "London",
            "postal_code": "SW1A 2AA",
            "address_type": "mailing",
            "country": "GB",
        },
    )
    assert "GB" in r.text


async def test_address_form_row_has_country_field(client, org_and_address):
    oid, _ = org_and_address
    r = client.get(f"/admin/orgs/{oid}/addresses/new-row/", headers=HTMX_HEADERS)
    assert r.status_code == 200
    assert 'name="country"' in r.text


async def test_country_format_endpoint_returns_fields_partial(client, org_and_address):
    oid, _ = org_and_address
    with patch(
        "src.api.admin.orgs_addresses.get_country_format",
        new=AsyncMock(
            return_value={
                "country": "CA",
                "fields": [
                    {"key": "address_line_1", "label": "Address line 1", "required": True},
                    {"key": "address_line_2", "label": "Apt/suite", "required": False},
                    {"key": "city", "label": "City", "required": True},
                    {"key": "region", "label": "Province", "required": True},
                    {"key": "postal_code", "label": "Postal code", "required": False},
                ],
            }
        ),
    ):
        r = client.get(
            f"/admin/orgs/{oid}/addresses/country-format/?country=CA",
            headers=HTMX_HEADERS,
        )
    assert r.status_code == 200
    assert "Province" in r.text
    assert "Postal code" in r.text


async def test_country_format_endpoint_us_returns_default_labels(client, org_and_address):
    oid, _ = org_and_address
    with patch(
        "src.api.admin.orgs_addresses.get_country_format",
        new=AsyncMock(
            return_value={
                "country": "US",
                "fields": [
                    {"key": "address_line_1", "label": "Address line 1", "required": True},
                    {"key": "address_line_2", "label": "Address line 2", "required": False},
                    {"key": "city", "label": "City", "required": True},
                    {"key": "region", "label": "State", "required": True},
                    {"key": "postal_code", "label": "ZIP code", "required": False},
                ],
            }
        ),
    ):
        r = client.get(
            f"/admin/orgs/{oid}/addresses/country-format/?country=US",
            headers=HTMX_HEADERS,
        )
    assert r.status_code == 200
    assert "State" in r.text
    assert "ZIP" in r.text


# ---------------------------------------------------------------------------
# Temporal validity window (#181)
# ---------------------------------------------------------------------------


async def test_addresses_create_with_validity_window(client, org_and_address, db_pool):
    oid, existing_eaid = org_and_address
    r = client.post(
        f"/admin/orgs/{oid}/addresses/",
        headers=HTMX_HEADERS,
        data={
            "address_line_1": "789 Window Way",
            "city": "Olympia",
            "region": "WA",
            "postal_code": "98501",
            "address_type": "physical",
            "valid_from": "2024-01-01",
            "valid_until": "2025-06-30",
            "mode": "save",
        },
    )
    assert r.status_code == 200
    assert "2024-01-01" in r.text
    assert "2025-06-30" in r.text

    async with db_pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT ea.valid_from, ea.valid_until FROM entity_addresses ea"
            " WHERE ea.entity_id=$1 AND ea.id != $2",
            oid,
            existing_eaid,
        )
    assert row is not None
    assert str(row["valid_from"]) == "2024-01-01"
    assert str(row["valid_until"]) == "2025-06-30"


async def test_addresses_update_validity_window(client, org_and_address, db_pool):
    oid, eaid = org_and_address
    r = client.post(
        f"/admin/orgs/{oid}/addresses/{eaid}/edit-row/",
        headers=HTMX_HEADERS,
        data={
            "address_line_1": "123 Main St",
            "city": "Olympia",
            "region": "WA",
            "postal_code": "98501",
            "address_type": "mailing",
            "valid_from": "2020-05-01",
            "mode": "save",
        },
    )
    assert r.status_code == 200
    assert "2020-05-01" in r.text

    async with db_pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT valid_from, valid_until FROM entity_addresses WHERE id=$1", eaid
        )
    assert str(row["valid_from"]) == "2020-05-01"
    assert row["valid_until"] is None


async def test_addresses_update_clears_validity_when_blank(client, org_and_address, db_pool):
    oid, eaid = org_and_address
    async with db_pool.acquire() as conn:
        await conn.execute(
            "UPDATE entity_addresses SET valid_from=DATE '2020-01-01',"
            " valid_until=DATE '2021-01-01' WHERE id=$1",
            eaid,
        )
    r = client.post(
        f"/admin/orgs/{oid}/addresses/{eaid}/edit-row/",
        headers=HTMX_HEADERS,
        data={
            "address_line_1": "123 Main St",
            "city": "Olympia",
            "region": "WA",
            "postal_code": "98501",
            "address_type": "mailing",
            "valid_from": "",
            "valid_until": "",
            "mode": "save",
        },
    )
    assert r.status_code == 200

    async with db_pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT valid_from, valid_until FROM entity_addresses WHERE id=$1", eaid
        )
    assert row["valid_from"] is None
    assert row["valid_until"] is None


async def test_addresses_create_inverted_range_returns_error(client, org_and_address, db_pool):
    oid, existing_eaid = org_and_address
    r = client.post(
        f"/admin/orgs/{oid}/addresses/",
        headers=HTMX_HEADERS,
        data={
            "address_line_1": "1 Backwards Blvd",
            "city": "Olympia",
            "region": "WA",
            "postal_code": "98501",
            "address_type": "mailing",
            "valid_from": "2025-06-30",
            "valid_until": "2024-01-01",
            "mode": "save",
        },
    )
    assert r.status_code == 200
    assert "alert--error" in r.text
    assert "on or before" in r.text

    async with db_pool.acquire() as conn:
        count = await conn.fetchval(
            "SELECT count(*) FROM entity_addresses WHERE entity_id=$1 AND id != $2",
            oid,
            existing_eaid,
        )
    assert count == 0


async def test_addresses_edit_row_prefills_validity(client, org_and_address, db_pool):
    oid, eaid = org_and_address
    async with db_pool.acquire() as conn:
        await conn.execute(
            "UPDATE entity_addresses SET valid_from=DATE '2019-03-01' WHERE id=$1", eaid
        )
    r = client.get(f"/admin/orgs/{oid}/addresses/{eaid}/edit-row/", headers=HTMX_HEADERS)
    assert r.status_code == 200
    assert 'name="valid_from"' in r.text
    assert "2019-03-01" in r.text


@patch("src.api.admin.orgs_addresses._NORMALIZER")
async def test_address_confirm_modal_roundtrips_validity(mock_normalizer, client, org_and_address):
    """Dates entered on the form must survive the normalize-confirm hop."""
    oid, _ = org_and_address
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
        f"/admin/orgs/{oid}/addresses/",
        headers=HTMX_HEADERS,
        data={
            "address_line_1": "123 Main St",
            "city": "Seattle",
            "region": "WA",
            "postal_code": "98101",
            "address_type": "mailing",
            "valid_from": "2024-01-01",
            "valid_until": "2025-06-30",
        },
    )
    assert r.status_code == 200
    assert r.headers.get("hx-retarget") == "#address-confirm-portal"
    assert '<input type="hidden" name="valid_from" value="2024-01-01">' in r.text
    assert '<input type="hidden" name="valid_until" value="2025-06-30">' in r.text
