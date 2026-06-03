"""Tests for GET /api/v1/people/{id}/events and GET /api/v1/orgs/{id}/events."""

import hashlib
import os

import pytest
import pytest_asyncio

from src.core.db import generate_id

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(loop_scope="session")
async def api_key(db):
    """Insert a test app_user + api_key; yield raw_key; clean up."""
    uid = generate_id()
    kid = generate_id()
    raw_key = "pm_" + os.urandom(16).hex()
    key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
    await db.execute(
        "INSERT INTO app_users (id, email) VALUES ($1,$2)", uid, "entityevents@test.com"
    )
    await db.execute(
        "INSERT INTO api_keys (id, user_id, label, key_prefix, key_hash) VALUES ($1,$2,$3,$4,$5)",
        kid,
        uid,
        "Entity Events Test Key",
        raw_key[:8],
        key_hash,
    )
    yield raw_key
    await db.execute("DELETE FROM api_keys WHERE id=$1", kid)
    await db.execute("DELETE FROM app_users WHERE id=$1", uid)


@pytest_asyncio.fixture(loop_scope="session")
async def person_fixture(db):
    """Create a test person; yield person_id; clean up."""
    person_id = generate_id()
    await db.execute("INSERT INTO people (id) VALUES ($1)", person_id)
    yield person_id
    await db.execute("DELETE FROM entity_events WHERE entity_id=$1", person_id)
    await db.execute("DELETE FROM people WHERE id=$1", person_id)


@pytest_asyncio.fixture(loop_scope="session")
async def org_fixture(db):
    """Create a test organization; yield org_id; clean up."""
    org_id = generate_id()
    await db.execute("INSERT INTO organizations (id) VALUES ($1)", org_id)
    yield org_id
    await db.execute("DELETE FROM entity_events WHERE entity_id=$1", org_id)
    await db.execute("DELETE FROM organizations WHERE id=$1", org_id)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _birth_type_id(db) -> str:
    return await db.fetchval("SELECT id FROM entity_event_types WHERE slug = 'birth'")


async def _founded_type_id(db) -> str:
    return await db.fetchval("SELECT id FROM entity_event_types WHERE slug = 'founded'")


# ---------------------------------------------------------------------------
# GET /api/v1/people/{id}/events — basic shape
# ---------------------------------------------------------------------------


async def test_list_person_events_empty(client, api_key, person_fixture):
    """200 with empty data list when no events exist."""
    r = client.get(f"/api/v1/people/{person_fixture}/events", headers={"X-API-Key": api_key})
    assert r.status_code == 200
    body = r.json()
    assert body["data"] == []
    assert body["meta"]["count"] == 0
    assert body["meta"]["has_more"] is False
    assert body["meta"]["limit"] == 20
    assert body["meta"]["offset"] == 0


async def test_list_person_events_returns_event_with_all_fields(
    client, api_key, person_fixture, db
):
    """Returns event with all expected fields including inlined event_type and structured date."""
    birth_id = await _birth_type_id(db)
    event_id = generate_id()
    await db.execute(
        """
        INSERT INTO entity_events
            (id, entity_type, entity_id, event_type_id, event_year, event_month, event_day,
             event_place_text, notes, visibility)
        VALUES ($1, 'person', $2, $3, 1985, 6, 15, 'Seattle, WA', 'Test note', 'public')
        """,
        event_id,
        person_fixture,
        birth_id,
    )
    try:
        r = client.get(f"/api/v1/people/{person_fixture}/events", headers={"X-API-Key": api_key})
        assert r.status_code == 200
        body = r.json()
        assert body["meta"]["count"] == 1
        item = body["data"][0]

        assert item["id"] == event_id
        assert item["visibility"] == "public"
        assert item["event_place_text"] == "Seattle, WA"
        assert item["notes"] == "Test note"
        assert item["created_at"].endswith("Z")

        # inlined event_type
        et = item["event_type"]
        assert et["slug"] == "birth"
        assert "id" in et
        assert "display_name" in et

        # structured date
        d = item["date"]
        assert d["year"] == 1985
        assert d["month"] == 6
        assert d["day"] == 15
        assert d["hour"] is None
        assert d["minute"] is None
        assert d["second"] is None
        assert d["at"] is None
    finally:
        await db.execute("DELETE FROM entity_events WHERE id=$1", event_id)


async def test_list_person_events_excludes_hidden_events(client, api_key, person_fixture, db):
    """Events with visibility != 'public' are excluded."""
    birth_id = await _birth_type_id(db)
    hidden_id = generate_id()
    legal_id = generate_id()
    await db.execute(
        """
        INSERT INTO entity_events (id, entity_type, entity_id, event_type_id, visibility)
        VALUES ($1, 'person', $2, $3, 'hidden')
        """,
        hidden_id,
        person_fixture,
        birth_id,
    )
    await db.execute(
        """
        INSERT INTO entity_events (id, entity_type, entity_id, event_type_id, visibility)
        VALUES ($1, 'person', $2, $3, 'legal_only')
        """,
        legal_id,
        person_fixture,
        birth_id,
    )
    try:
        r = client.get(f"/api/v1/people/{person_fixture}/events", headers={"X-API-Key": api_key})
        assert r.status_code == 200
        ids = [e["id"] for e in r.json()["data"]]
        assert hidden_id not in ids
        assert legal_id not in ids
    finally:
        await db.execute("DELETE FROM entity_events WHERE id=$1", hidden_id)
        await db.execute("DELETE FROM entity_events WHERE id=$1", legal_id)


async def test_list_person_events_excludes_archived_events(client, api_key, person_fixture, db):
    """Archived events (archived_at IS NOT NULL) are excluded."""
    birth_id = await _birth_type_id(db)
    event_id = generate_id()
    await db.execute(
        """
        INSERT INTO entity_events
            (id, entity_type, entity_id, event_type_id, visibility, archived_at)
        VALUES ($1, 'person', $2, $3, 'public', NOW())
        """,
        event_id,
        person_fixture,
        birth_id,
    )
    try:
        r = client.get(f"/api/v1/people/{person_fixture}/events", headers={"X-API-Key": api_key})
        assert r.status_code == 200
        ids = [e["id"] for e in r.json()["data"]]
        assert event_id not in ids
    finally:
        await db.execute("DELETE FROM entity_events WHERE id=$1", event_id)


async def test_list_person_events_pagination(client, api_key, person_fixture, db):
    """limit/offset/has_more work correctly."""
    birth_id = await _birth_type_id(db)
    ids = [generate_id() for _ in range(3)]
    for i, eid in enumerate(ids):
        await db.execute(
            """
            INSERT INTO entity_events
                (id, entity_type, entity_id, event_type_id, event_year, visibility)
            VALUES ($1, 'person', $2, $3, $4, 'public')
            """,
            eid,
            person_fixture,
            birth_id,
            2000 + i,
        )
    try:
        r = client.get(
            f"/api/v1/people/{person_fixture}/events",
            params={"limit": 2, "offset": 0},
            headers={"X-API-Key": api_key},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["meta"]["limit"] == 2
        assert body["meta"]["offset"] == 0
        assert body["meta"]["count"] == 2
        assert body["meta"]["has_more"] is True

        r2 = client.get(
            f"/api/v1/people/{person_fixture}/events",
            params={"limit": 2, "offset": 2},
            headers={"X-API-Key": api_key},
        )
        assert r2.status_code == 200
        body2 = r2.json()
        assert body2["meta"]["count"] == 1
        assert body2["meta"]["has_more"] is False
    finally:
        for eid in ids:
            await db.execute("DELETE FROM entity_events WHERE id=$1", eid)


async def test_list_person_events_404_when_person_not_found(client, api_key):
    """404 when person_id does not exist."""
    r = client.get(
        "/api/v1/people/01DOESNOTEXIST00000000000000/events",
        headers={"X-API-Key": api_key},
    )
    assert r.status_code == 404


async def test_list_person_events_401_with_invalid_key(client):
    """401 when X-API-Key is invalid."""
    r = client.get(
        "/api/v1/people/someid/events",
        headers={"X-API-Key": "pm_invalid_key"},
    )
    assert r.status_code == 401


# ---------------------------------------------------------------------------
# GET /api/v1/organizations/{id}/events
# ---------------------------------------------------------------------------


async def test_list_org_events_returns_org_event(client, api_key, org_fixture, db):
    """200, returns org event with inlined event_type."""
    founded_id = await _founded_type_id(db)
    event_id = generate_id()
    await db.execute(
        """
        INSERT INTO entity_events
            (id, entity_type, entity_id, event_type_id, event_year, visibility)
        VALUES ($1, 'organization', $2, $3, 1999, 'public')
        """,
        event_id,
        org_fixture,
        founded_id,
    )
    try:
        r = client.get(f"/api/v1/orgs/{org_fixture}/events", headers={"X-API-Key": api_key})
        assert r.status_code == 200
        body = r.json()
        assert body["meta"]["count"] == 1
        item = body["data"][0]
        assert item["id"] == event_id
        assert item["event_type"]["slug"] == "founded"
        assert item["date"]["year"] == 1999
    finally:
        await db.execute("DELETE FROM entity_events WHERE id=$1", event_id)


async def test_list_org_events_404_when_org_not_found(client, api_key):
    """404 when org_id does not exist."""
    r = client.get(
        "/api/v1/orgs/01DOESNOTEXIST00000000000000/events",
        headers={"X-API-Key": api_key},
    )
    assert r.status_code == 404


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
async def test_list_org_events_401_with_invalid_key(client):
    """GET /api/v1/orgs/{id}/events with invalid key returns 401."""
    response = client.get(
        "/api/v1/orgs/someid/events",
        headers={"X-API-Key": "invalid-key"},
    )
    assert response.status_code == 401


def test_list_org_events_403_without_key(unit_client):
    """GET /api/v1/orgs/{id}/events without key returns 403."""
    response = unit_client.get("/api/v1/orgs/01ANYORGID000000000000000000/events")
    assert response.status_code == 403
