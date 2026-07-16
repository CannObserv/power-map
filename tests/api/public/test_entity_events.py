"""Tests for GET /api/v1/people/{id}/events and GET /api/v1/orgs/{id}/events."""

import hashlib
import os

import pytest
import pytest_asyncio

from src.core.db import generate_id

pytestmark = pytest.mark.integration


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
    return raw_key


@pytest_asyncio.fixture(loop_scope="session")
async def person_fixture(db):
    """Create a test person; yield person_id; clean up."""
    person_id = generate_id()
    await db.execute("INSERT INTO people (id) VALUES ($1)", person_id)
    return person_id


@pytest_asyncio.fixture(loop_scope="session")
async def org_fixture(db):
    """Create a test organization; yield org_id; clean up."""
    org_id = generate_id()
    await db.execute("INSERT INTO organizations (id) VALUES ($1)", org_id)
    return org_id


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
    r = await client.get(f"/api/v1/people/{person_fixture}/events", headers={"X-API-Key": api_key})
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
    r = await client.get(f"/api/v1/people/{person_fixture}/events", headers={"X-API-Key": api_key})
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

    assert item["event_place_address"] is None


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
    r = await client.get(f"/api/v1/people/{person_fixture}/events", headers={"X-API-Key": api_key})
    assert r.status_code == 200
    ids = [e["id"] for e in r.json()["data"]]
    assert hidden_id not in ids
    assert legal_id not in ids


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
    r = await client.get(f"/api/v1/people/{person_fixture}/events", headers={"X-API-Key": api_key})
    assert r.status_code == 200
    ids = [e["id"] for e in r.json()["data"]]
    assert event_id not in ids


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
    r = await client.get(
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

    r2 = await client.get(
        f"/api/v1/people/{person_fixture}/events",
        params={"limit": 2, "offset": 2},
        headers={"X-API-Key": api_key},
    )
    assert r2.status_code == 200
    body2 = r2.json()
    assert body2["meta"]["count"] == 1
    assert body2["meta"]["has_more"] is False


async def test_list_person_events_404_when_person_not_found(client, api_key):
    """404 when person_id does not exist."""
    r = await client.get(
        "/api/v1/people/01DOESNOTEXIST00000000000000/events",
        headers={"X-API-Key": api_key},
    )
    assert r.status_code == 404


async def test_list_person_events_401_with_invalid_key(client):
    """401 when X-API-Key is invalid."""
    r = await client.get(
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
    r = await client.get(f"/api/v1/orgs/{org_fixture}/events", headers={"X-API-Key": api_key})
    assert r.status_code == 200
    body = r.json()
    assert body["meta"]["count"] == 1
    item = body["data"][0]
    assert item["id"] == event_id
    assert item["event_type"]["slug"] == "founded"
    assert item["date"]["year"] == 1999


async def test_list_org_events_404_when_org_not_found(client, api_key):
    """404 when org_id does not exist."""
    r = await client.get(
        "/api/v1/orgs/01DOESNOTEXIST00000000000000/events",
        headers={"X-API-Key": api_key},
    )
    assert r.status_code == 404


@pytest.mark.integration
async def test_list_org_events_401_with_invalid_key(client):
    """GET /api/v1/orgs/{id}/events with invalid key returns 401."""
    response = await client.get(
        "/api/v1/orgs/someid/events",
        headers={"X-API-Key": "invalid-key"},
    )
    assert response.status_code == 401


def test_list_org_events_403_without_key(unit_client):
    """GET /api/v1/orgs/{id}/events without key returns 403."""
    response = unit_client.get("/api/v1/orgs/01ANYORGID000000000000000000/events")
    assert response.status_code == 403


def test_list_person_events_403_without_key(unit_client):
    """GET /api/v1/people/{id}/events without key returns 403."""
    response = unit_client.get("/api/v1/people/nonexistent-id/events")
    assert response.status_code == 403


async def test_list_person_events_includes_event_place_address(client, api_key, person_fixture, db):
    """event_place_address object is populated in GET response when an address is linked."""
    birth_id = await _birth_type_id(db)
    aid = generate_id()
    await db.execute(
        "INSERT INTO addresses (id, raw_input, city, region, country, standardized, precision)"
        " VALUES ($1, 'Austin TX', 'Austin', 'TX', 'US', '1 Congress Ave, Austin, TX', 'city')",
        aid,
    )
    event_id = generate_id()
    await db.execute(
        """
        INSERT INTO entity_events
            (id, entity_type, entity_id, event_type_id, event_year,
             event_place_text, event_place_address_id, visibility)
        VALUES ($1, 'person', $2, $3, 1975, 'Austin, TX', $4, 'public')
        """,
        event_id,
        person_fixture,
        birth_id,
        aid,
    )
    r = await client.get(f"/api/v1/people/{person_fixture}/events", headers={"X-API-Key": api_key})
    assert r.status_code == 200
    items = r.json()["data"]
    item = next((e for e in items if e["id"] == event_id), None)
    assert item is not None
    addr = item["event_place_address"]
    assert addr is not None
    assert addr["id"] == aid
    assert addr["city"] == "Austin"
    assert addr["region"] == "TX"
    assert addr["precision"] == "city"


async def test_list_person_events_event_place_address_null_when_unlinked(
    client, api_key, person_fixture, db
):
    """event_place_address is null in GET response when no address is linked."""
    birth_id = await _birth_type_id(db)
    event_id = generate_id()
    await db.execute(
        """
        INSERT INTO entity_events
            (id, entity_type, entity_id, event_type_id, event_year, visibility)
        VALUES ($1, 'person', $2, $3, 1985, 'public')
        """,
        event_id,
        person_fixture,
        birth_id,
    )
    r = await client.get(f"/api/v1/people/{person_fixture}/events", headers={"X-API-Key": api_key})
    assert r.status_code == 200
    items = r.json()["data"]
    item = next((e for e in items if e["id"] == event_id), None)
    assert item is not None
    assert item["event_place_address"] is None


# ---------------------------------------------------------------------------
# Conditional requests — ETag / 304 (#292)
# ---------------------------------------------------------------------------


async def _seed_person_event(db, person_id, year=1985):
    birth_id = await _birth_type_id(db)
    event_id = generate_id()
    await db.execute(
        """
        INSERT INTO entity_events
            (id, entity_type, entity_id, event_type_id, event_year, visibility)
        VALUES ($1, 'person', $2, $3, $4, 'public')
        """,
        event_id,
        person_id,
        birth_id,
        year,
    )
    return event_id


async def test_person_events_200_carries_cache_headers(client, api_key, person_fixture, db):
    await _seed_person_event(db, person_fixture)
    r = await client.get(f"/api/v1/people/{person_fixture}/events", headers={"X-API-Key": api_key})
    assert r.status_code == 200
    assert r.headers.get("etag")
    assert r.headers.get("last-modified")
    assert r.headers.get("cache-control") == "no-cache"
    assert r.headers.get("vary") == "X-API-Key"


async def test_person_events_if_none_match_returns_304(client, api_key, person_fixture, db):
    await _seed_person_event(db, person_fixture)
    url = f"/api/v1/people/{person_fixture}/events"
    first = await client.get(url, headers={"X-API-Key": api_key})
    etag = first.headers["etag"]
    r = await client.get(url, headers={"X-API-Key": api_key, "If-None-Match": etag})
    assert r.status_code == 304
    assert r.headers.get("etag") == etag


async def test_person_events_empty_list_still_revalidates(client, api_key, person_fixture):
    """The 99.9%-empty-poll case must be 304-able too; no Last-Modified without events."""
    url = f"/api/v1/people/{person_fixture}/events"
    first = await client.get(url, headers={"X-API-Key": api_key})
    assert first.status_code == 200
    etag = first.headers["etag"]
    assert "last-modified" not in first.headers
    r = await client.get(url, headers={"X-API-Key": api_key, "If-None-Match": etag})
    assert r.status_code == 304


async def test_person_events_etag_changes_when_event_added(client, api_key, person_fixture, db):
    url = f"/api/v1/people/{person_fixture}/events"
    await _seed_person_event(db, person_fixture, year=1985)
    first = await client.get(url, headers={"X-API-Key": api_key})
    etag = first.headers["etag"]
    await _seed_person_event(db, person_fixture, year=1990)
    r = await client.get(url, headers={"X-API-Key": api_key, "If-None-Match": etag})
    assert r.status_code == 200
    assert r.headers["etag"] != etag


async def test_person_events_etag_varies_by_pagination(client, api_key, person_fixture, db):
    await _seed_person_event(db, person_fixture, year=1985)
    await _seed_person_event(db, person_fixture, year=1990)
    url = f"/api/v1/people/{person_fixture}/events"
    r1 = await client.get(url, params={"limit": 1}, headers={"X-API-Key": api_key})
    r2 = await client.get(url, params={"limit": 2}, headers={"X-API-Key": api_key})
    assert r1.headers["etag"] != r2.headers["etag"]


async def test_org_events_if_none_match_returns_304(client, api_key, org_fixture, db):
    founded_id = await _founded_type_id(db)
    await db.execute(
        """
        INSERT INTO entity_events
            (id, entity_type, entity_id, event_type_id, event_year, visibility)
        VALUES ($1, 'organization', $2, $3, 1999, 'public')
        """,
        generate_id(),
        org_fixture,
        founded_id,
    )
    url = f"/api/v1/orgs/{org_fixture}/events"
    first = await client.get(url, headers={"X-API-Key": api_key})
    assert first.status_code == 200
    etag = first.headers["etag"]
    r = await client.get(url, headers={"X-API-Key": api_key, "If-None-Match": etag})
    assert r.status_code == 304


# ---------------------------------------------------------------------------
# Stable pagination under tied date + created_at sort key (#297)
# ---------------------------------------------------------------------------


async def test_list_events_pagination_stable_under_tied_sort_key(
    client, api_key, person_fixture, db
):
    """Offset pagination is complete + duplicate-free when events tie on the sort key.

    Undated events (NULL year/month/day) inserted together share one created_at
    (Postgres now() is constant within the rollback transaction), so every row
    ties on (event_year, event_month, event_day, created_at). Without the ee.id
    tiebreaker, offset windows over them skip and duplicate. This shape backs
    both the person- and org-events lists.
    """
    birth_id = await _birth_type_id(db)
    event_ids = [generate_id() for _ in range(12)]
    for eid in event_ids:
        await db.execute(
            "INSERT INTO entity_events"
            " (id, entity_type, entity_id, event_type_id, visibility)"
            " VALUES ($1, 'person', $2, $3, 'public')",
            eid,
            person_fixture,
            birth_id,
        )

    limit = 3
    collected: list[str] = []
    offset = 0
    while True:
        r = await client.get(
            f"/api/v1/people/{person_fixture}/events",
            params={"limit": limit, "offset": offset},
            headers={"X-API-Key": api_key},
        )
        assert r.status_code == 200
        body = r.json()
        collected.extend(item["id"] for item in body["data"])
        if not body["meta"]["has_more"]:
            break
        offset += limit

    # Complete and duplicate-free: every seeded event appears exactly once.
    assert len(collected) == len(event_ids)
    assert set(collected) == set(event_ids)
    # Deterministic total order: full tie → ee.id DESC.
    assert collected == sorted(event_ids, reverse=True)
