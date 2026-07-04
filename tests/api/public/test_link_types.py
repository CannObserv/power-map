"""Tests for GET /api/v1/link-types.

Per-test markers: DB-backed cases carry ``@pytest.mark.integration``; the keyless
auth-reject case is a pure unit test (``unit_client``) so it runs in the fast
suite. (No module-level ``pytestmark`` — see test_role_types.py / test_auth.py.)
"""

import hashlib
import os

import pytest
import pytest_asyncio

from src.core.db import generate_id


@pytest_asyncio.fixture(loop_scope="session")
async def api_key(db):
    """Insert a test app_user + api_key; yield raw_key; clean up."""
    uid = generate_id()
    kid = generate_id()
    raw_key = "pm_" + os.urandom(16).hex()
    key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
    await db.execute(
        "INSERT INTO app_users (id, email) VALUES ($1,$2)", uid, "linktypetest@test.com"
    )
    await db.execute(
        "INSERT INTO api_keys (id, user_id, label, key_prefix, key_hash) VALUES ($1,$2,$3,$4,$5)",
        kid,
        uid,
        "Link Type Test Key",
        raw_key[:8],
        key_hash,
    )
    yield raw_key
    await db.execute("DELETE FROM api_keys WHERE id=$1", kid)
    await db.execute("DELETE FROM app_users WHERE id=$1", uid)


@pytest.mark.integration
async def test_link_types_with_valid_key_returns_200(client, api_key):
    """GET /api/v1/link-types with valid key returns 200."""
    response = client.get("/api/v1/link-types", headers={"X-API-Key": api_key})
    assert response.status_code == 200


@pytest.mark.integration
async def test_link_types_response_has_data_list(client, api_key):
    """Response has `data` key with list of items."""
    response = client.get("/api/v1/link-types", headers={"X-API-Key": api_key})
    assert response.status_code == 200
    body = response.json()
    assert "data" in body
    assert isinstance(body["data"], list)


@pytest.mark.integration
async def test_link_types_items_have_required_fields(client, api_key):
    """Each item in data list has id, slug, display_name, is_social."""
    response = client.get("/api/v1/link-types", headers={"X-API-Key": api_key})
    assert response.status_code == 200
    body = response.json()
    if body["data"]:  # At least check that fields are present if any exist
        item = body["data"][0]
        assert "id" in item
        assert "slug" in item
        assert "display_name" in item
        assert "is_social" in item
        assert isinstance(item["is_social"], bool)


def test_link_types_without_key_returns_403(unit_client):
    """GET /api/v1/link-types without X-API-Key returns 403."""
    response = unit_client.get("/api/v1/link-types")
    assert response.status_code == 403


@pytest.mark.integration
async def test_link_types_with_invalid_key_returns_401(client):
    """GET /api/v1/link-types with invalid key returns 401."""
    response = client.get("/api/v1/link-types", headers={"X-API-Key": "pm_invalid"})
    assert response.status_code == 401
