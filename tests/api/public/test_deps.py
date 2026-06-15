"""Tests for public API X-API-Key authentication dependency."""

import hashlib
import os

import pytest
import pytest_asyncio

from src.core.db import generate_id

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture(loop_scope="session")
async def api_key_pair(db):
    """Insert a test app_user + api_key; yield raw_key; clean up."""
    uid = generate_id()
    kid = generate_id()
    raw_key = "pm_" + os.urandom(16).hex()
    key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
    await db.execute("INSERT INTO app_users (id, email) VALUES ($1,$2)", uid, "apitest@test.com")
    await db.execute(
        "INSERT INTO api_keys (id, user_id, label, key_prefix, key_hash) VALUES ($1,$2,$3,$4,$5)",
        kid,
        uid,
        "Test Key",
        raw_key[:8],
        key_hash,
    )
    yield raw_key
    await db.execute("DELETE FROM api_keys WHERE id=$1", kid)
    await db.execute("DELETE FROM app_users WHERE id=$1", uid)


@pytest.mark.integration
async def test_api_root_valid_key_returns_200(client, api_key_pair):
    response = client.get("/api/v1/", headers={"X-API-Key": api_key_pair})
    assert response.status_code == 200


@pytest.mark.integration
async def test_api_valid_key_updates_last_used_at(client, api_key_pair, db):
    client.get("/api/v1/", headers={"X-API-Key": api_key_pair})
    key_hash = hashlib.sha256(api_key_pair.encode()).hexdigest()
    row = await db.fetchrow("SELECT last_used_at FROM api_keys WHERE key_hash=$1", key_hash)
    assert row["last_used_at"] is not None
