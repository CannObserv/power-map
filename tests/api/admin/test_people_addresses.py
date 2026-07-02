"""Integration tests for person addresses — temporal validity window (#181)."""

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
    """Guard against re-introducing the address teardown leak (#150).

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
        f"person_and_address fixture leaked {after - before} addresses row(s) — see #150"
    )


@pytest_asyncio.fixture(loop_scope="session")
async def person_and_address(db_pool):
    pid = generate_id()
    aid = generate_id()
    eaid = generate_id()

    async with db_pool.acquire() as conn:
        await conn.execute("INSERT INTO people (id) VALUES ($1)", pid)
        await conn.execute(
            "INSERT INTO addresses (id, address_line_1, city, region, postal_code, country)"
            " VALUES ($1, '123 Main St', 'Olympia', 'WA', '98501', 'US')",
            aid,
        )
        await conn.execute(
            "INSERT INTO entity_addresses"
            " (id, entity_type, entity_id, address_id, address_type)"
            " VALUES ($1, 'person', $2, $3, 'mailing')",
            eaid,
            pid,
            aid,
        )

    yield pid, eaid

    async with db_pool.acquire() as conn, conn.transaction():
        rows = await conn.fetch("SELECT address_id FROM entity_addresses WHERE entity_id=$1", pid)
        address_ids = [r["address_id"] for r in rows]
        await conn.execute("DELETE FROM entity_addresses WHERE entity_id=$1", pid)
        if address_ids:
            await conn.execute(
                "DELETE FROM addresses WHERE id = ANY($1::text[])"
                " AND NOT EXISTS ("
                "SELECT 1 FROM entity_addresses ea WHERE ea.address_id = addresses.id"
                ")",
                address_ids,
            )
        await conn.execute("DELETE FROM people WHERE id=$1", pid)


async def test_address_form_row_type_and_label_lead_panel(client, person_and_address):
    """#181 follow-up: type + label on the first line, ahead of country and validity."""
    pid, _ = person_and_address
    r = client.get(f"/admin/people/{pid}/addresses/new-row/", headers=HTMX_HEADERS)
    assert r.status_code == 200
    type_pos = r.text.index('name="address_type"')
    label_pos = r.text.index('name="display_name"')
    assert type_pos < r.text.index('name="country"')
    assert label_pos < r.text.index('name="country"')
    assert type_pos < r.text.index('name="valid_from"')
    assert label_pos < r.text.index('name="valid_from"')


async def test_addresses_create_with_validity_window(client, person_and_address, db_pool):
    pid, existing_eaid = person_and_address
    r = client.post(
        f"/admin/people/{pid}/addresses/",
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
            pid,
            existing_eaid,
        )
    assert row is not None
    assert str(row["valid_from"]) == "2024-01-01"
    assert str(row["valid_until"]) == "2025-06-30"


async def test_addresses_update_validity_window(client, person_and_address, db_pool):
    pid, eaid = person_and_address
    r = client.post(
        f"/admin/people/{pid}/addresses/{eaid}/edit-row/",
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


async def test_addresses_create_inverted_range_returns_error(client, person_and_address, db_pool):
    pid, existing_eaid = person_and_address
    r = client.post(
        f"/admin/people/{pid}/addresses/",
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
            pid,
            existing_eaid,
        )
    assert count == 0


async def test_addresses_create_malformed_date_returns_format_error(
    client, person_and_address, db_pool
):
    """Malformed date input gets a format message, not the range-order message (#181 CR)."""
    pid, existing_eaid = person_and_address
    r = client.post(
        f"/admin/people/{pid}/addresses/",
        headers=HTMX_HEADERS,
        data={
            "address_line_1": "1 Malformed Way",
            "city": "Olympia",
            "region": "WA",
            "postal_code": "98501",
            "address_type": "mailing",
            "valid_from": "01/02/2024",
            "mode": "save",
        },
    )
    assert r.status_code == 200
    assert "alert--error" in r.text
    assert "YYYY-MM-DD" in r.text
    assert "on or before" not in r.text

    async with db_pool.acquire() as conn:
        count = await conn.fetchval(
            "SELECT count(*) FROM entity_addresses WHERE entity_id=$1 AND id != $2",
            pid,
            existing_eaid,
        )
    assert count == 0
