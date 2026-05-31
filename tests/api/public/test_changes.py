"""Tests for GET /api/v1/changes — entity change feed."""

import hashlib
import os

import pytest
import pytest_asyncio

from src.core.db import generate_id

pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
]


@pytest_asyncio.fixture(loop_scope="session")
async def api_key(db):
    uid = generate_id()
    kid = generate_id()
    raw_key = "pm_" + os.urandom(16).hex()
    key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
    await db.execute("INSERT INTO app_users (id, email) VALUES ($1,$2)", uid, "changes@test.com")
    await db.execute(
        "INSERT INTO api_keys (id, user_id, label, key_prefix, key_hash) VALUES ($1,$2,$3,$4,$5)",
        kid,
        uid,
        "Changes Test Key",
        raw_key[:8],
        key_hash,
    )
    yield raw_key
    await db.execute("DELETE FROM api_keys WHERE id=$1", kid)
    await db.execute("DELETE FROM app_users WHERE id=$1", uid)


@pytest_asyncio.fixture(loop_scope="session")
async def change_fixtures(db):
    """Seed one person and one org, return their IDs and a sentinel timestamp."""
    # Capture time before seeding so we can use it as `since` in tests.
    before = await db.fetchval("SELECT NOW()")

    person_id = generate_id()
    org_id = generate_id()

    await db.execute("INSERT INTO people (id) VALUES ($1)", person_id)
    await db.execute("INSERT INTO organizations (id) VALUES ($1)", org_id)

    # Seed a tombstone for a previously-deleted entity.
    deleted_person_id = generate_id()
    await db.execute(
        "INSERT INTO deleted_entities (entity_type, entity_id) VALUES ('person', $1)",
        deleted_person_id,
    )

    yield {
        "before": before,
        "person_id": person_id,
        "org_id": org_id,
        "deleted_person_id": deleted_person_id,
    }

    await db.execute("DELETE FROM people WHERE id=$1", person_id)
    await db.execute("DELETE FROM organizations WHERE id=$1", org_id)
    await db.execute("DELETE FROM deleted_entities WHERE entity_id=$1", deleted_person_id)


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_changes_empty_since_future(client, api_key, change_fixtures):
    """since= in the future → empty data list."""
    r = client.get(
        "/api/v1/changes",
        params={"since": "2099-01-01T00:00:00.000000Z"},
        headers={"X-API-Key": api_key},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["data"] == []
    assert body["meta"]["count"] == 0
    assert body["meta"]["has_more"] is False


def test_changes_returns_updated_entities(client, api_key, change_fixtures):
    """since= before fixture creation → both seeded entities appear."""
    since = change_fixtures["before"].isoformat().replace("+00:00", "Z")
    r = client.get(
        "/api/v1/changes",
        params={"since": since, "limit": 100},
        headers={"X-API-Key": api_key},
    )
    assert r.status_code == 200
    body = r.json()
    ids = {item["entity_id"] for item in body["data"]}
    assert change_fixtures["person_id"] in ids
    assert change_fixtures["org_id"] in ids


def test_changes_includes_deleted_entities(client, api_key, change_fixtures):
    """Deleted entities appear in the feed with change_kind='deleted'."""
    since = change_fixtures["before"].isoformat().replace("+00:00", "Z")
    r = client.get(
        "/api/v1/changes",
        params={"since": since, "limit": 100},
        headers={"X-API-Key": api_key},
    )
    assert r.status_code == 200
    body = r.json()
    deleted = [
        item for item in body["data"] if item["entity_id"] == change_fixtures["deleted_person_id"]
    ]
    assert len(deleted) == 1
    assert deleted[0]["change_kind"] == "deleted"
    assert deleted[0]["entity_type"] == "person"


def test_changes_updated_kind(client, api_key, change_fixtures):
    """Live entities appear with change_kind='updated'."""
    since = change_fixtures["before"].isoformat().replace("+00:00", "Z")
    r = client.get(
        "/api/v1/changes",
        params={"since": since, "limit": 100},
        headers={"X-API-Key": api_key},
    )
    assert r.status_code == 200
    body = r.json()
    live = [item for item in body["data"] if item["entity_id"] == change_fixtures["person_id"]]
    assert len(live) == 1
    assert live[0]["change_kind"] == "updated"


def test_changes_ordered_by_changed_at(client, api_key, change_fixtures):
    """Results are ordered by changed_at ASC."""
    since = change_fixtures["before"].isoformat().replace("+00:00", "Z")
    r = client.get(
        "/api/v1/changes",
        params={"since": since, "limit": 100},
        headers={"X-API-Key": api_key},
    )
    assert r.status_code == 200
    timestamps = [item["changed_at"] for item in r.json()["data"]]
    assert timestamps == sorted(timestamps)


def test_changes_meta_structure(client, api_key, change_fixtures):
    """meta contains limit, count, has_more, next_since."""
    since = change_fixtures["before"].isoformat().replace("+00:00", "Z")
    r = client.get(
        "/api/v1/changes",
        params={"since": since},
        headers={"X-API-Key": api_key},
    )
    assert r.status_code == 200
    meta = r.json()["meta"]
    assert "limit" in meta
    assert "count" in meta
    assert "has_more" in meta
    assert "next_since" in meta
    assert meta["next_since"].endswith("Z")


def test_changes_default_limit(client, api_key, change_fixtures):
    """Default limit is 50."""
    since = change_fixtures["before"].isoformat().replace("+00:00", "Z")
    r = client.get(
        "/api/v1/changes",
        params={"since": since},
        headers={"X-API-Key": api_key},
    )
    assert r.json()["meta"]["limit"] == 50


def test_changes_has_more_pagination(client, api_key, change_fixtures):
    """limit=1 with multiple results → has_more=True, next_since advances."""
    since = change_fixtures["before"].isoformat().replace("+00:00", "Z")
    r = client.get(
        "/api/v1/changes",
        params={"since": since, "limit": 1},
        headers={"X-API-Key": api_key},
    )
    assert r.status_code == 200
    body = r.json()
    # At least 3 items exist (person, org, deleted_person), so has_more must be True.
    assert body["meta"]["has_more"] is True
    assert body["meta"]["count"] == 1
    # next_since should equal changed_at of the last (only) returned item.
    assert body["meta"]["next_since"] == body["data"][0]["changed_at"]


def test_changes_since_boundary_inclusive(client, api_key, change_fixtures):
    """since= equal to an entity's changed_at → that entity is included (>= semantics)."""
    since = change_fixtures["before"].isoformat().replace("+00:00", "Z")
    r1 = client.get(
        "/api/v1/changes",
        params={"since": since, "limit": 1},
        headers={"X-API-Key": api_key},
    )
    first_item = r1.json()["data"][0]
    first_changed_at = first_item["changed_at"]

    # Re-query with since = changed_at of first item → must still see it.
    r2 = client.get(
        "/api/v1/changes",
        params={"since": first_changed_at, "limit": 100},
        headers={"X-API-Key": api_key},
    )
    ids = {item["entity_id"] for item in r2.json()["data"]}
    assert first_item["entity_id"] in ids


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def test_changes_invalid_since_format(client, api_key):
    r = client.get(
        "/api/v1/changes",
        params={"since": "not-a-date"},
        headers={"X-API-Key": api_key},
    )
    assert r.status_code == 422


def test_changes_limit_too_large(client, api_key):
    r = client.get(
        "/api/v1/changes",
        params={"since": "2020-01-01T00:00:00Z", "limit": 1001},
        headers={"X-API-Key": api_key},
    )
    assert r.status_code == 422


def test_changes_limit_zero(client, api_key):
    r = client.get(
        "/api/v1/changes",
        params={"since": "2020-01-01T00:00:00Z", "limit": 0},
        headers={"X-API-Key": api_key},
    )
    assert r.status_code == 422
