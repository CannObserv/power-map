"""Integration tests for person events CRUD."""

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
async def person_and_event_type(db_pool):
    """One person + one event type seeded for all tests."""
    pid = generate_id()
    etid = generate_id()

    async with db_pool.acquire() as conn:
        await conn.execute("INSERT INTO people (id) VALUES ($1)", pid)
        await conn.execute(
            "INSERT INTO entity_event_types (id, slug, display_name, applies_to)"
            " VALUES ($1, $2, $3, 'person')",
            etid,
            f"test-event-{etid[:8]}",
            "Test Event",
        )

    yield pid, etid

    async with db_pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM entity_events WHERE entity_id=$1 AND entity_type='person'", pid
        )
        await conn.execute("DELETE FROM entity_event_types WHERE id=$1", etid)
        await conn.execute("DELETE FROM people WHERE id=$1", pid)


@pytest_asyncio.fixture(loop_scope="session")
async def person_with_event(db_pool, person_and_event_type):
    """Person with a single non-archived event."""
    pid, etid = person_and_event_type
    eid = generate_id()

    async with db_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO entity_events"
            " (id, entity_type, entity_id, event_type_id, event_year, visibility)"
            " VALUES ($1, 'person', $2, $3, 2020, 'public')",
            eid,
            pid,
            etid,
        )

    yield pid, etid, eid

    async with db_pool.acquire() as conn:
        await conn.execute("DELETE FROM entity_events WHERE id=$1", eid)


# ---------------------------------------------------------------------------
# GET new-row
# ---------------------------------------------------------------------------


async def test_event_new_row_returns_form(client, person_and_event_type):
    pid, _ = person_and_event_type
    r = client.get(f"/admin/people/{pid}/events/new-row/", headers=HTMX_HEADERS)
    assert r.status_code == 200
    assert "<form" in r.text


async def test_event_new_row_no_auth_returns_307(client, person_and_event_type):
    pid, _ = person_and_event_type
    r = client.get(f"/admin/people/{pid}/events/new-row/", follow_redirects=False)
    assert r.status_code == 307


# ---------------------------------------------------------------------------
# POST create
# ---------------------------------------------------------------------------


async def test_event_create_returns_read_row(client, person_and_event_type, db_pool):
    pid, etid = person_and_event_type
    r = client.post(
        f"/admin/people/{pid}/events/",
        headers=HTMX_HEADERS,
        data={
            "event_type_id": etid,
            "event_year": "2021",
            "event_month": "6",
            "event_day": "15",
            "notes": "Test note",
            "visibility": "public",
        },
    )
    assert r.status_code == 200
    assert "Test Event" in r.text

    # Clean up
    async with db_pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM entity_events"
            " WHERE entity_id=$1 AND entity_type='person' AND notes='Test note'",
            pid,
        )


async def test_event_create_requires_year_for_typed_event(client, db_pool):
    """Event type with requires_year=True must reject missing year."""
    pid = generate_id()
    etid = generate_id()

    async with db_pool.acquire() as conn:
        await conn.execute("INSERT INTO people (id) VALUES ($1)", pid)
        await conn.execute(
            "INSERT INTO entity_event_types"
            " (id, slug, display_name, applies_to, requires_year)"
            " VALUES ($1, $2, 'Dated Event', 'person', TRUE)",
            etid,
            f"dated-event-{etid[:8]}",
        )

    r = client.post(
        f"/admin/people/{pid}/events/",
        headers=HTMX_HEADERS,
        data={"event_type_id": etid, "event_year": "", "visibility": "public"},
    )
    assert r.status_code == 200
    assert "Year is required" in r.text

    async with db_pool.acquire() as conn:
        await conn.execute("DELETE FROM entity_event_types WHERE id=$1", etid)
        await conn.execute("DELETE FROM people WHERE id=$1", pid)


# ---------------------------------------------------------------------------
# GET read-row / edit-row
# ---------------------------------------------------------------------------


async def test_event_read_row_returns_row(client, person_with_event):
    pid, etid, eid = person_with_event
    r = client.get(f"/admin/people/{pid}/events/{eid}/read-row/", headers=HTMX_HEADERS)
    assert r.status_code == 200
    assert "<form" not in r.text
    assert "Test Event" in r.text


async def test_event_edit_row_returns_form(client, person_with_event):
    pid, etid, eid = person_with_event
    r = client.get(f"/admin/people/{pid}/events/{eid}/edit-row/", headers=HTMX_HEADERS)
    assert r.status_code == 200
    assert "<form" in r.text


# ---------------------------------------------------------------------------
# POST edit-row (update)
# ---------------------------------------------------------------------------


async def test_event_update_saves_and_returns_read_row(client, person_with_event):
    pid, etid, eid = person_with_event
    r = client.post(
        f"/admin/people/{pid}/events/{eid}/edit-row/",
        headers=HTMX_HEADERS,
        data={
            "event_type_id": etid,
            "event_year": "2022",
            "notes": "Updated note",
            "visibility": "public",
        },
    )
    assert r.status_code == 200
    assert "Test Event" in r.text
    assert "<form" not in r.text


# ---------------------------------------------------------------------------
# Archive / Unarchive
# ---------------------------------------------------------------------------


async def test_event_archive_sets_archived_at(client, person_with_event, db_pool):
    pid, etid, eid = person_with_event
    r = client.post(f"/admin/people/{pid}/events/{eid}/archive/", headers=HTMX_HEADERS)
    assert r.status_code == 200

    async with db_pool.acquire() as conn:
        row = await conn.fetchrow("SELECT archived_at FROM entity_events WHERE id=$1", eid)
    assert row["archived_at"] is not None

    # Restore for subsequent tests
    async with db_pool.acquire() as conn:
        await conn.execute("UPDATE entity_events SET archived_at=NULL WHERE id=$1", eid)


async def test_event_archive_already_archived_returns_409(client, db_pool, person_and_event_type):
    pid, etid = person_and_event_type
    eid = generate_id()

    async with db_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO entity_events"
            " (id, entity_type, entity_id, event_type_id, visibility, archived_at)"
            " VALUES ($1, 'person', $2, $3, 'public', NOW())",
            eid,
            pid,
            etid,
        )

    r = client.post(f"/admin/people/{pid}/events/{eid}/archive/", headers=HTMX_HEADERS)
    assert r.status_code == 409

    async with db_pool.acquire() as conn:
        await conn.execute("DELETE FROM entity_events WHERE id=$1", eid)


async def test_event_unarchive_clears_archived_at(client, db_pool, person_and_event_type):
    pid, etid = person_and_event_type
    eid = generate_id()

    async with db_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO entity_events"
            " (id, entity_type, entity_id, event_type_id, visibility, archived_at)"
            " VALUES ($1, 'person', $2, $3, 'public', NOW())",
            eid,
            pid,
            etid,
        )

    r = client.post(f"/admin/people/{pid}/events/{eid}/unarchive/", headers=HTMX_HEADERS)
    assert r.status_code == 200

    async with db_pool.acquire() as conn:
        row = await conn.fetchrow("SELECT archived_at FROM entity_events WHERE id=$1", eid)
        await conn.execute("DELETE FROM entity_events WHERE id=$1", eid)
    assert row["archived_at"] is None


async def test_event_unarchive_not_archived_returns_409(client, person_with_event):
    pid, etid, eid = person_with_event
    r = client.post(f"/admin/people/{pid}/events/{eid}/unarchive/", headers=HTMX_HEADERS)
    assert r.status_code == 409


# ---------------------------------------------------------------------------
# DELETE
# ---------------------------------------------------------------------------


async def test_event_delete_not_archived_returns_409(client, person_with_event):
    pid, etid, eid = person_with_event
    r = client.delete(f"/admin/people/{pid}/events/{eid}/", headers=HTMX_HEADERS)
    assert r.status_code == 409


async def test_event_delete_archived_succeeds(client, db_pool, person_and_event_type):
    pid, etid = person_and_event_type
    eid = generate_id()

    async with db_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO entity_events"
            " (id, entity_type, entity_id, event_type_id, visibility, archived_at)"
            " VALUES ($1, 'person', $2, $3, 'public', NOW())",
            eid,
            pid,
            etid,
        )

    r = client.delete(f"/admin/people/{pid}/events/{eid}/", headers=HTMX_HEADERS)
    assert r.status_code == 200
    assert r.text == ""

    async with db_pool.acquire() as conn:
        row = await conn.fetchrow("SELECT id FROM entity_events WHERE id=$1", eid)
    assert row is None
