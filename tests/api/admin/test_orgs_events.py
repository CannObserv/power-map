"""Integration tests for org events CRUD."""

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
