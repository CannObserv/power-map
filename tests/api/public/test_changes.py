"""Tests for GET /api/v1/changes — outbox-based, subscription-filtered change feed."""

import hashlib
import os

import pytest
import pytest_asyncio

from src.core.db import generate_id

pytestmark = [
    pytest.mark.integration,
]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(loop_scope="session")
async def api_key(db):
    """Create an API key; yield {'raw_key': str, 'key_id': str}."""
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
    yield {"raw_key": raw_key, "key_id": kid}
    await db.execute("DELETE FROM api_keys WHERE id=$1", kid)
    await db.execute("DELETE FROM app_users WHERE id=$1", uid)


@pytest_asyncio.fixture(loop_scope="session")
async def change_fixtures(db, api_key):
    """Seed entities, subscribe api_key to them, return IDs and a seq cursor anchor."""
    before_seq = await db.fetchval("SELECT COALESCE(MAX(id), 0) FROM entity_changes")

    person_id = generate_id()
    org_id = generate_id()
    kid = api_key["key_id"]

    await db.execute("INSERT INTO people (id) VALUES ($1)", person_id)
    await db.execute("INSERT INTO organizations (id) VALUES ($1)", org_id)

    await db.execute(
        "INSERT INTO api_key_entity_subscriptions (api_key_id, entity_id, entity_type)"
        " VALUES ($1,$2,'person')",
        kid,
        person_id,
    )
    await db.execute(
        "INSERT INTO api_key_entity_subscriptions (api_key_id, entity_id, entity_type)"
        " VALUES ($1,$2,'organization')",
        kid,
        org_id,
    )

    # Tombstone: seed a deleted entity and subscribe to it.
    deleted_person_id = generate_id()
    await db.execute(
        "INSERT INTO deleted_entities (entity_type, entity_id) VALUES ('person', $1)",
        deleted_person_id,
    )
    await db.execute(
        "INSERT INTO api_key_entity_subscriptions (api_key_id, entity_id, entity_type)"
        " VALUES ($1,$2,'person')",
        kid,
        deleted_person_id,
    )

    # Unsubscribed entity — must never appear in the feed for this key.
    unsubscribed_id = generate_id()
    await db.execute("INSERT INTO people (id) VALUES ($1)", unsubscribed_id)

    yield {
        "before_seq": before_seq,
        "person_id": person_id,
        "org_id": org_id,
        "deleted_person_id": deleted_person_id,
        "unsubscribed_id": unsubscribed_id,
    }

    await db.execute("DELETE FROM api_key_entity_subscriptions WHERE api_key_id=$1", kid)
    await db.execute("DELETE FROM people WHERE id=$1", person_id)
    await db.execute("DELETE FROM people WHERE id=$1", unsubscribed_id)
    await db.execute("DELETE FROM organizations WHERE id=$1", org_id)
    await db.execute("DELETE FROM deleted_entities WHERE entity_id=$1", deleted_person_id)


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_changes_returns_subscribed_entities(client, api_key, change_fixtures):
    """Subscribed entities appear in the feed after their creation seq_id."""
    r = client.get(
        "/api/v1/changes",
        params={"after": change_fixtures["before_seq"], "limit": 100},
        headers={"X-API-Key": api_key["raw_key"]},
    )
    assert r.status_code == 200
    ids = {item["entity_id"] for item in r.json()["data"]}
    assert change_fixtures["person_id"] in ids
    assert change_fixtures["org_id"] in ids


def test_changes_excludes_unsubscribed_entities(client, api_key, change_fixtures):
    """Entities not in the subscription set are excluded from the feed."""
    r = client.get(
        "/api/v1/changes",
        params={"after": change_fixtures["before_seq"], "limit": 100},
        headers={"X-API-Key": api_key["raw_key"]},
    )
    assert r.status_code == 200
    ids = {item["entity_id"] for item in r.json()["data"]}
    assert change_fixtures["unsubscribed_id"] not in ids


def test_changes_includes_subscribed_deleted_entities(client, api_key, change_fixtures):
    """Subscribed deleted entities appear with change_kind='deleted'."""
    r = client.get(
        "/api/v1/changes",
        params={"after": change_fixtures["before_seq"], "limit": 100},
        headers={"X-API-Key": api_key["raw_key"]},
    )
    assert r.status_code == 200
    deleted = [
        item
        for item in r.json()["data"]
        if item["entity_id"] == change_fixtures["deleted_person_id"]
    ]
    assert len(deleted) == 1
    assert deleted[0]["change_kind"] == "deleted"
    assert deleted[0]["entity_type"] == "person"


def test_changes_updated_kind(client, api_key, change_fixtures):
    """Live subscribed entities appear with change_kind='updated'."""
    r = client.get(
        "/api/v1/changes",
        params={"after": change_fixtures["before_seq"], "limit": 100},
        headers={"X-API-Key": api_key["raw_key"]},
    )
    assert r.status_code == 200
    live = [item for item in r.json()["data"] if item["entity_id"] == change_fixtures["person_id"]]
    assert len(live) >= 1
    assert live[0]["change_kind"] == "updated"


def test_changes_ordered_by_seq_id(client, api_key, change_fixtures):
    """Results are ordered by outbox seq_id ASC."""
    r = client.get(
        "/api/v1/changes",
        params={"after": change_fixtures["before_seq"], "limit": 100},
        headers={"X-API-Key": api_key["raw_key"]},
    )
    assert r.status_code == 200
    seq_ids = [item["seq_id"] for item in r.json()["data"]]
    assert seq_ids == sorted(seq_ids)


def test_changes_meta_structure(client, api_key, change_fixtures):
    """meta contains limit, count, has_more, next_after (integer)."""
    r = client.get(
        "/api/v1/changes",
        params={"after": change_fixtures["before_seq"]},
        headers={"X-API-Key": api_key["raw_key"]},
    )
    assert r.status_code == 200
    meta = r.json()["meta"]
    assert "limit" in meta
    assert "count" in meta
    assert "has_more" in meta
    assert "next_after" in meta
    assert isinstance(meta["next_after"], int)


def test_changes_next_after_advances(client, api_key, change_fixtures):
    """next_after equals the seq_id of the last returned item."""
    r = client.get(
        "/api/v1/changes",
        params={"after": change_fixtures["before_seq"], "limit": 1},
        headers={"X-API-Key": api_key["raw_key"]},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["meta"]["next_after"] == body["data"][0]["seq_id"]


def test_changes_default_limit(client, api_key, change_fixtures):
    """Default limit is 50."""
    r = client.get(
        "/api/v1/changes",
        params={"after": change_fixtures["before_seq"]},
        headers={"X-API-Key": api_key["raw_key"]},
    )
    assert r.json()["meta"]["limit"] == 50


def test_changes_has_more_pagination(client, api_key, change_fixtures):
    """limit=1 with multiple subscribed results → has_more=True, count=1."""
    r = client.get(
        "/api/v1/changes",
        params={"after": change_fixtures["before_seq"], "limit": 1},
        headers={"X-API-Key": api_key["raw_key"]},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["meta"]["has_more"] is True
    assert body["meta"]["count"] == 1


def test_changes_after_cursor_exclusive(client, api_key, change_fixtures):
    """after= is exclusive (>); items at or before next_after are excluded on next page."""
    r1 = client.get(
        "/api/v1/changes",
        params={"after": change_fixtures["before_seq"], "limit": 1},
        headers={"X-API-Key": api_key["raw_key"]},
    )
    first_item = r1.json()["data"][0]
    next_after = r1.json()["meta"]["next_after"]

    r2 = client.get(
        "/api/v1/changes",
        params={"after": next_after, "limit": 100},
        headers={"X-API-Key": api_key["raw_key"]},
    )
    ids = {item["entity_id"] for item in r2.json()["data"]}
    assert first_item["entity_id"] not in ids


def test_changes_after_zero_returns_all(client, api_key, change_fixtures):
    """after=0 returns all subscribed events regardless of seq_id."""
    r = client.get(
        "/api/v1/changes",
        params={"after": 0, "limit": 1000},
        headers={"X-API-Key": api_key["raw_key"]},
    )
    assert r.status_code == 200
    ids = {item["entity_id"] for item in r.json()["data"]}
    assert change_fixtures["person_id"] in ids


def test_changes_empty_when_after_beyond_max(client, api_key, change_fixtures):
    """after= beyond the current max seq_id → empty data, next_after echoes the param."""
    r = client.get(
        "/api/v1/changes",
        params={"after": 999_999_999},
        headers={"X-API-Key": api_key["raw_key"]},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["data"] == []
    assert body["meta"]["has_more"] is False
    assert body["meta"]["next_after"] == 999_999_999


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def test_changes_missing_after_param(client, api_key):
    r = client.get("/api/v1/changes", headers={"X-API-Key": api_key["raw_key"]})
    assert r.status_code == 422


def test_changes_negative_after(client, api_key):
    r = client.get(
        "/api/v1/changes",
        params={"after": -1},
        headers={"X-API-Key": api_key["raw_key"]},
    )
    assert r.status_code == 422


def test_changes_limit_too_large(client, api_key):
    r = client.get(
        "/api/v1/changes",
        params={"after": 0, "limit": 1001},
        headers={"X-API-Key": api_key["raw_key"]},
    )
    assert r.status_code == 422


def test_changes_limit_zero(client, api_key):
    r = client.get(
        "/api/v1/changes",
        params={"after": 0, "limit": 0},
        headers={"X-API-Key": api_key["raw_key"]},
    )
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# Roles in the change feed
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(loop_scope="session")
async def role_change_fixtures(db, api_key):
    """Seed an org + role, subscribe the test key, return IDs and cursor anchor."""
    before_seq = await db.fetchval("SELECT COALESCE(MAX(id), 0) FROM entity_changes")
    org_id = generate_id()
    role_id = generate_id()
    kid = api_key["key_id"]
    await db.execute("INSERT INTO organizations (id) VALUES ($1)", org_id)
    await db.execute(
        "INSERT INTO roles (id, organization_id, title) VALUES ($1,$2,'Test Feed Role')",
        role_id,
        org_id,
    )
    await db.execute(
        "INSERT INTO api_key_entity_subscriptions (api_key_id, entity_id, entity_type)"
        " VALUES ($1,$2,'role')",
        kid,
        role_id,
    )
    yield {"before_seq": before_seq, "role_id": role_id}
    await db.execute(
        "DELETE FROM api_key_entity_subscriptions WHERE api_key_id=$1 AND entity_id=$2",
        kid,
        role_id,
    )
    await db.execute("DELETE FROM roles WHERE id=$1", role_id)
    await db.execute("DELETE FROM organizations WHERE id=$1", org_id)


def test_changes_includes_roles(client, api_key, role_change_fixtures):
    r = client.get(
        "/api/v1/changes",
        params={"after": role_change_fixtures["before_seq"], "limit": 100},
        headers={"X-API-Key": api_key["raw_key"]},
    )
    assert r.status_code == 200
    items = r.json()["data"]
    role_items = [i for i in items if i["entity_id"] == role_change_fixtures["role_id"]]
    assert len(role_items) >= 1
    assert role_items[0]["entity_type"] == "role"
    assert role_items[0]["change_kind"] == "updated"


# ---------------------------------------------------------------------------
# Jurisdictions in the change feed
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(loop_scope="session")
async def jurisdiction_change_fixtures(db, api_key):
    """Seed a jurisdiction + type, subscribe, return IDs and cursor anchor."""
    before_seq = await db.fetchval("SELECT COALESCE(MAX(id), 0) FROM entity_changes")
    jtype_id = generate_id()
    jid = generate_id()
    kid = api_key["key_id"]
    await db.execute(
        "INSERT INTO jurisdiction_types (id, slug, display_name)"
        " VALUES ($1,'test-jtype-cf','Test CF')",
        jtype_id,
    )
    await db.execute(
        "INSERT INTO jurisdictions (id, slug, name, type_id)"
        " VALUES ($1,'test-jfeed-cf','Feed Jurisdiction CF',$2)",
        jid,
        jtype_id,
    )
    await db.execute(
        "INSERT INTO api_key_entity_subscriptions (api_key_id, entity_id, entity_type)"
        " VALUES ($1,$2,'jurisdiction')",
        kid,
        jid,
    )
    yield {"before_seq": before_seq, "jurisdiction_id": jid}
    await db.execute(
        "DELETE FROM api_key_entity_subscriptions WHERE api_key_id=$1 AND entity_id=$2",
        kid,
        jid,
    )
    await db.execute("DELETE FROM jurisdictions WHERE id=$1", jid)
    await db.execute("DELETE FROM jurisdiction_types WHERE id=$1", jtype_id)


def test_changes_includes_jurisdictions(client, api_key, jurisdiction_change_fixtures):
    r = client.get(
        "/api/v1/changes",
        params={"after": jurisdiction_change_fixtures["before_seq"], "limit": 100},
        headers={"X-API-Key": api_key["raw_key"]},
    )
    assert r.status_code == 200
    items = r.json()["data"]
    jitems = [i for i in items if i["entity_id"] == jurisdiction_change_fixtures["jurisdiction_id"]]
    assert len(jitems) >= 1
    assert jitems[0]["entity_type"] == "jurisdiction"
    assert jitems[0]["change_kind"] == "updated"
