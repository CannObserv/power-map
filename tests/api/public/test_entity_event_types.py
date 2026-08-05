"""Tests for GET /api/v1/entity-event-types."""

import hashlib
import os

import pytest
import pytest_asyncio

from src.core.db import generate_id

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture(loop_scope="session")
async def api_key(db):
    """Insert a test app_user + api_key; return raw_key (rolled back per test)."""
    uid = generate_id()
    kid = generate_id()
    raw_key = "pm_" + os.urandom(16).hex()
    key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
    await db.execute(
        "INSERT INTO app_users (id, email) VALUES ($1,$2)", uid, "entityeventtest@test.com"
    )
    await db.execute(
        "INSERT INTO api_keys (id, user_id, label, key_prefix, key_hash) VALUES ($1,$2,$3,$4,$5)",
        kid,
        uid,
        "Entity Event Type Test Key",
        raw_key[:8],
        key_hash,
    )
    return raw_key


async def test_entity_event_types_with_valid_key_returns_200(client, api_key):
    """GET /api/v1/entity-event-types with valid key returns 200."""
    response = await client.get("/api/v1/entity-event-types", headers={"X-API-Key": api_key})
    assert response.status_code == 200


async def test_entity_event_types_response_has_data_list(client, api_key):
    """Response has `data` key with list of items."""
    response = await client.get("/api/v1/entity-event-types", headers={"X-API-Key": api_key})
    assert response.status_code == 200
    body = response.json()
    assert "data" in body
    assert isinstance(body["data"], list)


async def test_entity_event_types_items_have_required_fields(client, api_key):
    """Each item in data list has all required fields."""
    response = await client.get("/api/v1/entity-event-types", headers={"X-API-Key": api_key})
    assert response.status_code == 200
    body = response.json()
    if body["data"]:
        item = body["data"][0]
        assert "id" in item
        assert "slug" in item
        assert "display_name" in item
        assert "applies_to" in item
        assert "requires_year" in item
        assert "requires_linked_entity" in item
        assert isinstance(item["requires_year"], bool)
        assert isinstance(item["requires_linked_entity"], bool)
        assert item["applies_to"] in ("person", "organization", "both")


async def test_entity_event_types_seed_data_present(client, api_key):
    """Seed data includes 'birth' and 'founded' slugs."""
    response = await client.get("/api/v1/entity-event-types", headers={"X-API-Key": api_key})
    assert response.status_code == 200
    body = response.json()
    slugs = {item["slug"] for item in body["data"]}
    assert "birth" in slugs
    assert "founded" in slugs


async def test_entity_event_types_succeeded_by_present(client, api_key):
    """#321: succeeded_by — org continuation link on the predecessor → successor."""
    response = await client.get("/api/v1/entity-event-types", headers={"X-API-Key": api_key})
    assert response.status_code == 200
    by_slug = {item["slug"]: item for item in response.json()["data"]}
    assert "succeeded_by" in by_slug
    row = by_slug["succeeded_by"]
    assert row["applies_to"] == "organization"
    assert row["requires_linked_entity"] is True
    assert row["requires_year"] is False


def test_entity_event_types_without_key_returns_403(unit_client):
    """GET /api/v1/entity-event-types without X-API-Key returns 403."""
    response = unit_client.get("/api/v1/entity-event-types")
    assert response.status_code == 403


async def test_entity_event_types_with_invalid_key_returns_401(client):
    """GET /api/v1/entity-event-types with invalid key returns 401."""
    response = await client.get("/api/v1/entity-event-types", headers={"X-API-Key": "pm_invalid"})
    assert response.status_code == 401


# ── conditional GET (#392) ────────────────────────────────────────────────────


async def test_entity_event_types_etag_round_trips_to_304(client, api_key):
    first = await client.get("/api/v1/entity-event-types", headers={"X-API-Key": api_key})
    assert first.status_code == 200
    etag = first.headers["etag"]

    r = await client.get(
        "/api/v1/entity-event-types", headers={"X-API-Key": api_key, "If-None-Match": etag}
    )
    assert r.status_code == 304
    assert r.content == b""
    assert r.headers["vary"] == "X-API-Key"
    assert "last-modified" not in r.headers


async def test_entity_event_types_etag_changes_on_in_place_edit(client, db, api_key):
    before = (
        await client.get("/api/v1/entity-event-types", headers={"X-API-Key": api_key})
    ).headers["etag"]
    await db.execute(
        "UPDATE entity_event_types SET display_name = display_name || ' (renamed)'"
        " WHERE id = (SELECT id FROM entity_event_types ORDER BY slug LIMIT 1)"
    )
    after = await client.get(
        "/api/v1/entity-event-types", headers={"X-API-Key": api_key, "If-None-Match": before}
    )
    assert after.status_code == 200, "in-place rename still revalidated as unchanged"
