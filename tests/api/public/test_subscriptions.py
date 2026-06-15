"""Tests for /api/v1/subscriptions — entity subscription management."""

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
async def sub_api_key(db):
    """API key with subscriptions:write scope; yields {'raw_key', 'key_id'}."""
    uid = generate_id()
    kid = generate_id()
    raw_key = "pm_" + os.urandom(16).hex()
    key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
    await db.execute("INSERT INTO app_users (id, email) VALUES ($1,$2)", uid, "sub@test.com")
    await db.execute(
        "INSERT INTO api_keys (id, user_id, label, key_prefix, key_hash) VALUES ($1,$2,$3,$4,$5)",
        kid,
        uid,
        "Sub Test Key",
        raw_key[:8],
        key_hash,
    )
    await db.execute(
        "INSERT INTO api_key_scopes (api_key_id, scope_id) VALUES ($1,'subscriptions:write')",
        kid,
    )
    yield {"raw_key": raw_key, "key_id": kid}
    await db.execute("DELETE FROM api_keys WHERE id=$1", kid)
    await db.execute("DELETE FROM app_users WHERE id=$1", uid)


@pytest_asyncio.fixture(loop_scope="session")
async def readonly_api_key(db):
    """API key with no scopes; yields {'raw_key', 'key_id'}."""
    uid = generate_id()
    kid = generate_id()
    raw_key = "pm_" + os.urandom(16).hex()
    key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
    await db.execute("INSERT INTO app_users (id, email) VALUES ($1,$2)", uid, "ro@test.com")
    await db.execute(
        "INSERT INTO api_keys (id, user_id, label, key_prefix, key_hash) VALUES ($1,$2,$3,$4,$5)",
        kid,
        uid,
        "RO Test Key",
        raw_key[:8],
        key_hash,
    )
    yield {"raw_key": raw_key, "key_id": kid}
    await db.execute("DELETE FROM api_keys WHERE id=$1", kid)
    await db.execute("DELETE FROM app_users WHERE id=$1", uid)


@pytest_asyncio.fixture(loop_scope="session")
async def sub_entities(db):
    """Seed a person and an org for subscription tests; yield their IDs."""
    person_id = generate_id()
    org_id = generate_id()
    await db.execute("INSERT INTO people (id) VALUES ($1)", person_id)
    await db.execute("INSERT INTO organizations (id) VALUES ($1)", org_id)
    yield {"person_id": person_id, "org_id": org_id}
    await db.execute("DELETE FROM people WHERE id=$1", person_id)
    await db.execute("DELETE FROM organizations WHERE id=$1", org_id)


# ---------------------------------------------------------------------------
# GET /api/v1/subscriptions
# ---------------------------------------------------------------------------


def test_subscriptions_list_empty_for_new_key(client, sub_api_key):
    """A key with no subscriptions returns an empty list."""
    r = client.get(
        "/api/v1/subscriptions",
        headers={"X-API-Key": sub_api_key["raw_key"]},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["data"] == []
    assert body["meta"]["count"] == 0
    assert body["meta"]["has_more"] is False


def test_subscriptions_list_after_register(client, sub_api_key, sub_entities):
    """Registered entity appears in GET /subscriptions."""
    person_id = sub_entities["person_id"]
    # Register
    client.post(
        "/api/v1/subscriptions",
        json={"entity_ids": [person_id]},
        headers={"X-API-Key": sub_api_key["raw_key"]},
    )
    r = client.get(
        "/api/v1/subscriptions",
        headers={"X-API-Key": sub_api_key["raw_key"]},
    )
    assert r.status_code == 200
    ids = {item["entity_id"] for item in r.json()["data"]}
    assert person_id in ids
    # Cleanup
    client.delete(
        f"/api/v1/subscriptions/{person_id}",
        headers={"X-API-Key": sub_api_key["raw_key"]},
    )


def test_subscriptions_list_entity_type_filter(client, sub_api_key, sub_entities):
    """GET ?entity_type= filters results to that type only."""
    person_id = sub_entities["person_id"]
    org_id = sub_entities["org_id"]
    # Register both
    client.post(
        "/api/v1/subscriptions",
        json={"entity_ids": [person_id, org_id]},
        headers={"X-API-Key": sub_api_key["raw_key"]},
    )
    r = client.get(
        "/api/v1/subscriptions",
        params={"entity_type": "person"},
        headers={"X-API-Key": sub_api_key["raw_key"]},
    )
    assert r.status_code == 200
    types = {item["entity_type"] for item in r.json()["data"]}
    assert types == {"person"}
    # Cleanup
    client.request(
        "DELETE",
        "/api/v1/subscriptions",
        json={"entity_ids": [person_id, org_id]},
        headers={"X-API-Key": sub_api_key["raw_key"]},
    )


def test_subscriptions_list_pagination(client, sub_api_key, sub_entities):
    """limit/offset pagination works on GET /subscriptions."""
    person_id = sub_entities["person_id"]
    org_id = sub_entities["org_id"]
    client.post(
        "/api/v1/subscriptions",
        json={"entity_ids": [person_id, org_id]},
        headers={"X-API-Key": sub_api_key["raw_key"]},
    )
    r = client.get(
        "/api/v1/subscriptions",
        params={"limit": 1, "offset": 0},
        headers={"X-API-Key": sub_api_key["raw_key"]},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["meta"]["count"] == 1
    assert body["meta"]["has_more"] is True
    # Cleanup
    client.request(
        "DELETE",
        "/api/v1/subscriptions",
        json={"entity_ids": [person_id, org_id]},
        headers={"X-API-Key": sub_api_key["raw_key"]},
    )


# ---------------------------------------------------------------------------
# POST /api/v1/subscriptions
# ---------------------------------------------------------------------------


def test_subscriptions_post_registers_entity(client, sub_api_key, sub_entities):
    """POST registers an entity; response reports registered count."""
    person_id = sub_entities["person_id"]
    r = client.post(
        "/api/v1/subscriptions",
        json={"entity_ids": [person_id]},
        headers={"X-API-Key": sub_api_key["raw_key"]},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["registered"] == 1
    assert body["already_subscribed"] == 0
    assert body["not_found"] == []
    # Cleanup
    client.delete(
        f"/api/v1/subscriptions/{person_id}",
        headers={"X-API-Key": sub_api_key["raw_key"]},
    )


def test_subscriptions_post_idempotent(client, sub_api_key, sub_entities):
    """Posting the same entity_id twice counts as already_subscribed on second call."""
    person_id = sub_entities["person_id"]
    client.post(
        "/api/v1/subscriptions",
        json={"entity_ids": [person_id]},
        headers={"X-API-Key": sub_api_key["raw_key"]},
    )
    r2 = client.post(
        "/api/v1/subscriptions",
        json={"entity_ids": [person_id]},
        headers={"X-API-Key": sub_api_key["raw_key"]},
    )
    assert r2.status_code == 200
    body = r2.json()
    assert body["registered"] == 0
    assert body["already_subscribed"] == 1
    # Cleanup
    client.delete(
        f"/api/v1/subscriptions/{person_id}",
        headers={"X-API-Key": sub_api_key["raw_key"]},
    )


def test_subscriptions_post_not_found_entity(client, sub_api_key):
    """Unknown entity_id goes to not_found; other valid IDs still registered."""
    fake_id = generate_id()
    r = client.post(
        "/api/v1/subscriptions",
        json={"entity_ids": [fake_id]},
        headers={"X-API-Key": sub_api_key["raw_key"]},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["registered"] == 0
    assert fake_id in body["not_found"]


def test_subscriptions_post_bulk_mixed(client, sub_api_key, sub_entities):
    """POST with valid + unknown IDs in one call: partial registration."""
    person_id = sub_entities["person_id"]
    fake_id = generate_id()
    r = client.post(
        "/api/v1/subscriptions",
        json={"entity_ids": [person_id, fake_id]},
        headers={"X-API-Key": sub_api_key["raw_key"]},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["registered"] == 1
    assert fake_id in body["not_found"]
    # Cleanup
    client.delete(
        f"/api/v1/subscriptions/{person_id}",
        headers={"X-API-Key": sub_api_key["raw_key"]},
    )


def test_subscriptions_post_requires_scope(client, readonly_api_key, sub_entities):
    """POST /subscriptions requires subscriptions:write scope → 403 without it."""
    r = client.post(
        "/api/v1/subscriptions",
        json={"entity_ids": [sub_entities["person_id"]]},
        headers={"X-API-Key": readonly_api_key["raw_key"]},
    )
    assert r.status_code == 403


def test_subscriptions_post_entity_ids_max_length(client, sub_api_key):
    """POST with >500 entity_ids → 422 (Pydantic max_length list constraint)."""
    r = client.post(
        "/api/v1/subscriptions",
        json={"entity_ids": [generate_id() for _ in range(501)]},
        headers={"X-API-Key": sub_api_key["raw_key"]},
    )
    assert r.status_code == 422


def test_subscriptions_delete_bulk_entity_ids_max_length(client, sub_api_key):
    """Bulk DELETE with >500 entity_ids → 422 (Pydantic max_length list constraint)."""
    r = client.request(
        "DELETE",
        "/api/v1/subscriptions",
        json={"entity_ids": [generate_id() for _ in range(501)]},
        headers={"X-API-Key": sub_api_key["raw_key"]},
    )
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# DELETE /api/v1/subscriptions/{entity_id}
# ---------------------------------------------------------------------------


def test_subscriptions_delete_single(client, sub_api_key, sub_entities):
    """DELETE removes the subscription; entity no longer on GET."""
    person_id = sub_entities["person_id"]
    client.post(
        "/api/v1/subscriptions",
        json={"entity_ids": [person_id]},
        headers={"X-API-Key": sub_api_key["raw_key"]},
    )
    r = client.delete(
        f"/api/v1/subscriptions/{person_id}",
        headers={"X-API-Key": sub_api_key["raw_key"]},
    )
    assert r.status_code == 204

    get_r = client.get(
        "/api/v1/subscriptions",
        headers={"X-API-Key": sub_api_key["raw_key"]},
    )
    ids = {item["entity_id"] for item in get_r.json()["data"]}
    assert person_id not in ids


def test_subscriptions_delete_single_not_subscribed(client, sub_api_key):
    """DELETE on an entity the key is not subscribed to → 404."""
    fake_id = generate_id()
    r = client.delete(
        f"/api/v1/subscriptions/{fake_id}",
        headers={"X-API-Key": sub_api_key["raw_key"]},
    )
    assert r.status_code == 404


def test_subscriptions_delete_single_requires_scope(client, readonly_api_key, sub_entities):
    """DELETE /subscriptions/{id} requires subscriptions:write → 403."""
    r = client.delete(
        f"/api/v1/subscriptions/{sub_entities['person_id']}",
        headers={"X-API-Key": readonly_api_key["raw_key"]},
    )
    assert r.status_code == 403


# ---------------------------------------------------------------------------
# DELETE /api/v1/subscriptions (bulk)
# ---------------------------------------------------------------------------


def test_subscriptions_delete_bulk(client, sub_api_key, sub_entities):
    """Bulk DELETE removes multiple subscriptions at once."""
    person_id = sub_entities["person_id"]
    org_id = sub_entities["org_id"]
    client.post(
        "/api/v1/subscriptions",
        json={"entity_ids": [person_id, org_id]},
        headers={"X-API-Key": sub_api_key["raw_key"]},
    )
    r = client.request(
        "DELETE",
        "/api/v1/subscriptions",
        json={"entity_ids": [person_id, org_id]},
        headers={"X-API-Key": sub_api_key["raw_key"]},
    )
    assert r.status_code == 204

    get_r = client.get(
        "/api/v1/subscriptions",
        headers={"X-API-Key": sub_api_key["raw_key"]},
    )
    ids = {item["entity_id"] for item in get_r.json()["data"]}
    assert person_id not in ids
    assert org_id not in ids


def test_subscriptions_delete_bulk_requires_scope(client, readonly_api_key, sub_entities):
    """Bulk DELETE requires subscriptions:write → 403."""
    r = client.request(
        "DELETE",
        "/api/v1/subscriptions",
        json={"entity_ids": [sub_entities["person_id"]]},
        headers={"X-API-Key": readonly_api_key["raw_key"]},
    )
    assert r.status_code == 403
