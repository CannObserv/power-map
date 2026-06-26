"""Integration tests for org events CRUD."""

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


@pytest_asyncio.fixture(loop_scope="session")
async def org_and_event_type(db_pool):
    """One org + one event type seeded for all tests."""
    oid = generate_id()
    etid = generate_id()

    async with db_pool.acquire() as conn:
        await conn.execute("INSERT INTO organizations (id) VALUES ($1)", oid)
        await conn.execute(
            "INSERT INTO entity_event_types (id, slug, display_name, applies_to)"
            " VALUES ($1, $2, $3, 'organization')",
            etid,
            f"test-org-event-{etid[:8]}",
            "Test Org Event",
        )

    yield oid, etid

    async with db_pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM entity_events WHERE entity_id=$1 AND entity_type='organization'", oid
        )
        await conn.execute("DELETE FROM entity_event_types WHERE id=$1", etid)
        await conn.execute("DELETE FROM organizations WHERE id=$1", oid)


@pytest_asyncio.fixture(loop_scope="session")
async def org_with_event(db_pool, org_and_event_type):
    """Org with a single non-archived event."""
    oid, etid = org_and_event_type
    eid = generate_id()

    async with db_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO entity_events"
            " (id, entity_type, entity_id, event_type_id, event_year, visibility)"
            " VALUES ($1, 'organization', $2, $3, 2020, 'public')",
            eid,
            oid,
            etid,
        )

    yield oid, etid, eid

    async with db_pool.acquire() as conn:
        await conn.execute("DELETE FROM entity_events WHERE id=$1", eid)


# ---------------------------------------------------------------------------
# GET new-row
# ---------------------------------------------------------------------------


async def test_event_new_row_returns_form(client, org_and_event_type):
    oid, _ = org_and_event_type
    r = client.get(f"/admin/orgs/{oid}/events/new-row/", headers=HTMX_HEADERS)
    assert r.status_code == 200
    assert "<form" in r.text


async def test_event_new_row_no_auth_returns_307(client, org_and_event_type):
    oid, _ = org_and_event_type
    r = client.get(f"/admin/orgs/{oid}/events/new-row/", follow_redirects=False)
    assert r.status_code == 307


# ---------------------------------------------------------------------------
# POST create
# ---------------------------------------------------------------------------


async def test_event_create_returns_read_row(client, org_and_event_type, db_pool):
    oid, etid = org_and_event_type
    r = client.post(
        f"/admin/orgs/{oid}/events/",
        headers=HTMX_HEADERS,
        data={
            "event_type_id": etid,
            "event_year": "2021",
            "event_month": "6",
            "event_day": "15",
            "notes": "Test org note",
            "visibility": "public",
        },
    )
    assert r.status_code == 200
    assert "Test Org Event" in r.text
    assert "HX-Trigger" in r.headers

    # Clean up
    async with db_pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM entity_events"
            " WHERE entity_id=$1 AND entity_type='organization' AND notes='Test org note'",
            oid,
        )


# ---------------------------------------------------------------------------
# GET edit-row
# ---------------------------------------------------------------------------


async def test_event_edit_row_returns_form(client, org_with_event):
    oid, etid, eid = org_with_event
    r = client.get(f"/admin/orgs/{oid}/events/{eid}/edit-row/", headers=HTMX_HEADERS)
    assert r.status_code == 200
    assert "<form" in r.text


# ---------------------------------------------------------------------------
# POST edit-row (update)
# ---------------------------------------------------------------------------


async def test_event_update_saves_and_returns_read_row(client, org_with_event):
    oid, etid, eid = org_with_event
    r = client.post(
        f"/admin/orgs/{oid}/events/{eid}/edit-row/",
        headers=HTMX_HEADERS,
        data={
            "event_type_id": etid,
            "event_year": "2022",
            "notes": "Updated org note",
            "visibility": "public",
        },
    )
    assert r.status_code == 200
    assert "Test Org Event" in r.text
    assert "<form" not in r.text


# ---------------------------------------------------------------------------
# Archive / Unarchive
# ---------------------------------------------------------------------------


async def test_event_archive_sets_archived_at(client, org_with_event, db_pool):
    oid, etid, eid = org_with_event
    r = client.post(f"/admin/orgs/{oid}/events/{eid}/archive/", headers=HTMX_HEADERS)
    assert r.status_code == 200
    assert "HX-Trigger" in r.headers

    async with db_pool.acquire() as conn:
        row = await conn.fetchrow("SELECT archived_at FROM entity_events WHERE id=$1", eid)
    assert row["archived_at"] is not None

    # Restore for subsequent tests
    async with db_pool.acquire() as conn:
        await conn.execute("UPDATE entity_events SET archived_at=NULL WHERE id=$1", eid)


async def test_event_archive_already_archived_returns_409(client, db_pool, org_and_event_type):
    oid, etid = org_and_event_type
    eid = generate_id()

    async with db_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO entity_events"
            " (id, entity_type, entity_id, event_type_id, visibility, archived_at)"
            " VALUES ($1, 'organization', $2, $3, 'public', NOW())",
            eid,
            oid,
            etid,
        )

    r = client.post(f"/admin/orgs/{oid}/events/{eid}/archive/", headers=HTMX_HEADERS)
    assert r.status_code == 409

    async with db_pool.acquire() as conn:
        await conn.execute("DELETE FROM entity_events WHERE id=$1", eid)


async def test_event_unarchive_clears_archived_at(client, db_pool, org_and_event_type):
    oid, etid = org_and_event_type
    eid = generate_id()

    async with db_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO entity_events"
            " (id, entity_type, entity_id, event_type_id, visibility, archived_at)"
            " VALUES ($1, 'organization', $2, $3, 'public', NOW())",
            eid,
            oid,
            etid,
        )

    r = client.post(f"/admin/orgs/{oid}/events/{eid}/unarchive/", headers=HTMX_HEADERS)
    assert r.status_code == 200

    async with db_pool.acquire() as conn:
        row = await conn.fetchrow("SELECT archived_at FROM entity_events WHERE id=$1", eid)
        await conn.execute("DELETE FROM entity_events WHERE id=$1", eid)
    assert row["archived_at"] is None


async def test_event_unarchive_not_archived_returns_409(client, org_with_event):
    oid, etid, eid = org_with_event
    r = client.post(f"/admin/orgs/{oid}/events/{eid}/unarchive/", headers=HTMX_HEADERS)
    assert r.status_code == 409


# ---------------------------------------------------------------------------
# DELETE
# ---------------------------------------------------------------------------


async def test_event_delete_not_archived_returns_409(client, org_with_event):
    oid, etid, eid = org_with_event
    r = client.delete(f"/admin/orgs/{oid}/events/{eid}/", headers=HTMX_HEADERS)
    assert r.status_code == 409


async def test_event_delete_archived_succeeds(client, db_pool, org_and_event_type):
    oid, etid = org_and_event_type
    eid = generate_id()

    async with db_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO entity_events"
            " (id, entity_type, entity_id, event_type_id, visibility, archived_at)"
            " VALUES ($1, 'organization', $2, $3, 'public', NOW())",
            eid,
            oid,
            etid,
        )

    r = client.delete(f"/admin/orgs/{oid}/events/{eid}/", headers=HTMX_HEADERS)
    assert r.status_code == 200
    assert r.text == ""

    async with db_pool.acquire() as conn:
        row = await conn.fetchrow("SELECT id FROM entity_events WHERE id=$1", eid)
    assert row is None


# ---------------------------------------------------------------------------
# Validation — date/time range and calendar accuracy
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "field,value,expected_fragment",
    [
        ("event_day", "55", "Day must be between 1 and 31"),
        ("event_hour", "25", "Hour must be between 0 and 23"),
        ("event_minute", "60", "Minute must be between 0 and 59"),
        ("event_second", "61", "Second must be between 0 and 59"),
        ("event_year", "10000", "Year must be between"),
    ],
)
async def test_event_create_range_validation(
    client, org_and_event_type, field, value, expected_fragment
):
    oid, etid = org_and_event_type
    data = {
        "event_type_id": etid,
        "event_year": "2021",
        "event_month": "6",
        "event_day": "15",
        "event_hour": "10",
        "event_minute": "30",
        "event_second": "0",
        "visibility": "public",
    }
    data[field] = value
    r = client.post(f"/admin/orgs/{oid}/events/", headers=HTMX_HEADERS, data=data)
    assert r.status_code == 200
    assert "<form" in r.text
    assert expected_fragment in r.text


@pytest.mark.parametrize(
    "month,day,year,expected_fragment",
    [
        ("2", "30", "2021", "does not exist in February 2021"),
        ("2", "29", "2023", "does not exist in February 2023"),
        ("2", "29", "1900", "does not exist in February 1900"),  # non-leap century
        ("4", "31", "2021", "does not exist in April 2021"),
        ("11", "31", "2024", "does not exist in November 2024"),
    ],
)
async def test_event_create_calendar_validation(
    client, org_and_event_type, month, day, year, expected_fragment
):
    oid, etid = org_and_event_type
    data = {
        "event_type_id": etid,
        "event_month": month,
        "event_day": day,
        "visibility": "public",
    }
    if year is not None:
        data["event_year"] = year
    r = client.post(f"/admin/orgs/{oid}/events/", headers=HTMX_HEADERS, data=data)
    assert r.status_code == 200
    assert "<form" in r.text
    assert expected_fragment in r.text


@pytest.mark.parametrize("year", ["2024", "2000"])
async def test_event_create_feb29_leap_year_succeeds(client, org_and_event_type, db_pool, year):
    oid, etid = org_and_event_type
    r = client.post(
        f"/admin/orgs/{oid}/events/",
        headers=HTMX_HEADERS,
        data={
            "event_type_id": etid,
            "event_year": year,
            "event_month": "2",
            "event_day": "29",
            "notes": "Feb29LeapTest",
            "visibility": "public",
        },
    )
    assert r.status_code == 200
    assert "<form" not in r.text

    async with db_pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM entity_events"
            " WHERE entity_id=$1 AND entity_type='organization' AND notes='Feb29LeapTest'",
            oid,
        )


async def test_event_edit_calendar_validation(client, org_with_event):
    """Edit route also validates — confirm wiring."""
    oid, etid, eid = org_with_event
    r = client.post(
        f"/admin/orgs/{oid}/events/{eid}/edit-row/",
        headers=HTMX_HEADERS,
        data={
            "event_type_id": etid,
            "event_year": "2023",
            "event_month": "2",
            "event_day": "29",
            "visibility": "public",
        },
    )
    assert r.status_code == 200
    assert "<form" in r.text
    assert "does not exist in February 2023" in r.text


# ---------------------------------------------------------------------------
# event_place_address_id linkage
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(loop_scope="session")
async def address_city(db_pool):
    """A street-precision address row for use across address-linkage tests."""
    aid = generate_id()
    async with db_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO addresses"
            " (id, raw_input, city, region, country, standardized, precision)"
            " VALUES ($1, 'Seattle WA', 'Seattle', 'WA', 'US',"
            " '123 Main St, Seattle, WA', 'street')",
            aid,
        )
    yield aid
    async with db_pool.acquire() as conn:
        await conn.execute("DELETE FROM addresses WHERE id=$1", aid)


@pytest_asyncio.fixture(loop_scope="session")
async def address_low_precision(db_pool):
    """A region-precision address — below the city threshold."""
    aid = generate_id()
    async with db_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO addresses (id, raw_input, region, country, precision)"
            " VALUES ($1, 'Washington State', 'WA', 'US', 'region')",
            aid,
        )
    yield aid
    async with db_pool.acquire() as conn:
        await conn.execute("DELETE FROM addresses WHERE id=$1", aid)


async def test_event_create_links_address(client, org_and_event_type, address_city, db_pool):
    oid, etid = org_and_event_type
    r = client.post(
        f"/admin/orgs/{oid}/events/",
        headers=HTMX_HEADERS,
        data={
            "event_type_id": etid,
            "event_year": "2021",
            "event_place_text": "Seattle",
            "event_place_address_id": address_city,
            "notes": "AddrLinkTest",
            "visibility": "public",
        },
    )
    assert r.status_code == 200
    assert "<form" not in r.text

    async with db_pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT event_place_address_id FROM entity_events"
            " WHERE entity_id=$1 AND notes='AddrLinkTest'",
            oid,
        )
        await conn.execute(
            "DELETE FROM entity_events WHERE entity_id=$1 AND notes='AddrLinkTest'", oid
        )
    assert row is not None
    assert row["event_place_address_id"] == address_city


async def test_event_create_address_not_found_returns_form_error(client, org_and_event_type):
    oid, etid = org_and_event_type
    r = client.post(
        f"/admin/orgs/{oid}/events/",
        headers=HTMX_HEADERS,
        data={
            "event_type_id": etid,
            "event_place_address_id": "nonexistent-address-id",
            "visibility": "public",
        },
    )
    assert r.status_code == 200
    assert "<form" in r.text
    assert "not found" in r.text.lower()


async def test_event_create_address_low_precision_returns_form_error(
    client, org_and_event_type, address_low_precision
):
    oid, etid = org_and_event_type
    r = client.post(
        f"/admin/orgs/{oid}/events/",
        headers=HTMX_HEADERS,
        data={
            "event_type_id": etid,
            "event_place_address_id": address_low_precision,
            "visibility": "public",
        },
    )
    assert r.status_code == 200
    assert "<form" in r.text
    assert "precision" in r.text.lower()


async def test_event_edit_links_address(client, org_with_event, address_city, db_pool):
    oid, etid, eid = org_with_event
    r = client.post(
        f"/admin/orgs/{oid}/events/{eid}/edit-row/",
        headers=HTMX_HEADERS,
        data={
            "event_type_id": etid,
            "event_year": "2020",
            "event_place_address_id": address_city,
            "visibility": "public",
        },
    )
    assert r.status_code == 200
    assert "<form" not in r.text

    async with db_pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT event_place_address_id FROM entity_events WHERE id=$1", eid
        )
        await conn.execute("UPDATE entity_events SET event_place_address_id=NULL WHERE id=$1", eid)
    assert row["event_place_address_id"] == address_city


async def test_event_edit_clears_address(client, org_and_event_type, address_city, db_pool):
    oid, etid = org_and_event_type
    eid = generate_id()
    async with db_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO entity_events"
            " (id, entity_type, entity_id, event_type_id, event_year,"
            "  event_place_address_id, visibility)"
            " VALUES ($1, 'organization', $2, $3, 2020, $4, 'public')",
            eid,
            oid,
            etid,
            address_city,
        )

    r = client.post(
        f"/admin/orgs/{oid}/events/{eid}/edit-row/",
        headers=HTMX_HEADERS,
        data={
            "event_type_id": etid,
            "event_year": "2020",
            "event_place_address_id": "",
            "visibility": "public",
        },
    )
    assert r.status_code == 200

    async with db_pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT event_place_address_id FROM entity_events WHERE id=$1", eid
        )
        await conn.execute("DELETE FROM entity_events WHERE id=$1", eid)
    assert row["event_place_address_id"] is None


async def test_event_read_row_shows_address_city(client, org_and_event_type, address_city, db_pool):
    oid, etid = org_and_event_type
    eid = generate_id()
    async with db_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO entity_events"
            " (id, entity_type, entity_id, event_type_id, event_year,"
            "  event_place_address_id, visibility)"
            " VALUES ($1, 'organization', $2, $3, 2021, $4, 'public')",
            eid,
            oid,
            etid,
            address_city,
        )

    r = client.get(f"/admin/orgs/{oid}/events/{eid}/read-row/", headers=HTMX_HEADERS)
    assert r.status_code == 200
    assert "Seattle" in r.text

    async with db_pool.acquire() as conn:
        await conn.execute("DELETE FROM entity_events WHERE id=$1", eid)


@pytest.mark.parametrize(
    "data_override,expected_fragment",
    [
        # month without year
        ({"event_month": "6"}, "year is required when a month"),
        # day without month
        ({"event_day": "15"}, "month is required when a day"),
        # hour without day (month+year present, day absent)
        (
            {"event_month": "6", "event_year": "2020", "event_hour": "10"},
            "date is required when a time",
        ),
        # minute without hour
        (
            {"event_month": "6", "event_year": "2020", "event_day": "15", "event_minute": "30"},
            "Hour is required when minute",
        ),
        # second without minute
        (
            {
                "event_month": "6",
                "event_year": "2020",
                "event_day": "15",
                "event_hour": "10",
                "event_second": "45",
            },
            "Minute is required when second",
        ),
    ],
)
async def test_event_create_presence_validation(
    client, org_and_event_type, data_override, expected_fragment
):
    oid, etid = org_and_event_type
    data = {"event_type_id": etid, "visibility": "public", **data_override}
    r = client.post(f"/admin/orgs/{oid}/events/", headers=HTMX_HEADERS, data=data)
    assert r.status_code == 200
    assert "<form" in r.text
    assert expected_fragment in r.text


# ---------------------------------------------------------------------------
# Linked-entity validation + edit prefill (#172) — org side of the shared
# factory (logic lives in _events_shared.py; people side covered separately).
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(loop_scope="session")
async def linkable_person(db_pool):
    """A person with a display name, usable as a linked entity."""
    pid = generate_id()
    async with db_pool.acquire() as conn:
        await conn.execute("INSERT INTO people (id) VALUES ($1)", pid)
        await conn.execute(
            "INSERT INTO person_names (id, person_id, name, is_canonical)"
            " VALUES ($1, $2, $3, TRUE)",
            generate_id(),
            pid,
            "Linked Target Person",
        )

    yield pid, "Linked Target Person"

    async with db_pool.acquire() as conn:
        await conn.execute("DELETE FROM person_names WHERE person_id=$1", pid)
        await conn.execute("DELETE FROM people WHERE id=$1", pid)


async def test_org_event_create_accepts_valid_linked_entity(
    client, org_and_event_type, linkable_person, db_pool
):
    oid, etid = org_and_event_type
    pid, _ = linkable_person
    r = client.post(
        f"/admin/orgs/{oid}/events/",
        headers=HTMX_HEADERS,
        data={
            "event_type_id": etid,
            "event_year": "2020",
            "linked_entity_type": "person",
            "linked_entity_id": pid,
            "visibility": "public",
        },
    )
    assert r.status_code == 200
    assert "Linked entity" not in r.text  # no validation error
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT linked_entity_type, linked_entity_id FROM entity_events"
            " WHERE entity_id=$1 AND linked_entity_id=$2",
            oid,
            pid,
        )
    assert row is not None
    assert row["linked_entity_type"] == "person"


async def test_org_event_create_rejects_unknown_linked_entity(client, org_and_event_type):
    oid, etid = org_and_event_type
    r = client.post(
        f"/admin/orgs/{oid}/events/",
        headers=HTMX_HEADERS,
        data={
            "event_type_id": etid,
            "event_year": "2020",
            "linked_entity_type": "person",
            "linked_entity_id": "per_doesnotexist",
            "visibility": "public",
        },
    )
    assert r.status_code == 200
    assert "Linked entity not found" in r.text


async def test_org_event_edit_row_prefills_linked_entity_name(
    client, org_and_event_type, linkable_person, db_pool
):
    oid, etid = org_and_event_type
    pid, pname = linkable_person
    eid = generate_id()
    async with db_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO entity_events"
            " (id, entity_type, entity_id, event_type_id, event_year,"
            "  linked_entity_type, linked_entity_id, visibility)"
            " VALUES ($1, 'organization', $2, $3, 2020, 'person', $4, 'public')",
            eid,
            oid,
            etid,
            pid,
        )
    try:
        r = client.get(f"/admin/orgs/{oid}/events/{eid}/edit-row/", headers=HTMX_HEADERS)
        assert r.status_code == 200
        assert pname in r.text  # display name, not the raw ULID
    finally:
        async with db_pool.acquire() as conn:
            await conn.execute("DELETE FROM entity_events WHERE id=$1", eid)
