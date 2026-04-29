# tests/api/public/test_api_key_auth.py
"""Integration tests for public API X-API-Key authentication."""

import hashlib
import os

import asyncpg
import pytest
from fastapi.testclient import TestClient

from src.api.main import app
from src.core.db import apply_schema, generate_id

pytestmark = pytest.mark.integration


@pytest.fixture
async def db():
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        pytest.skip("DATABASE_URL not set")
    conn = await asyncpg.connect(dsn)
    try:
        await apply_schema(conn)
        yield conn
    finally:
        await conn.close()


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture
async def api_key_pair(db):
    """Insert a test app_user + api_key; yield raw_key; clean up."""
    uid = generate_id()
    kid = generate_id()
    raw_key = "pm_" + os.urandom(16).hex()
    key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
    await db.execute(
        "INSERT INTO app_users (id, email) VALUES ($1,$2)", uid, "apitest@test.com"
    )
    await db.execute(
        "INSERT INTO api_keys (id, user_id, label, key_prefix, key_hash)"
        " VALUES ($1,$2,$3,$4,$5)",
        kid, uid, "Test Key", raw_key[:8], key_hash,
    )
    yield raw_key
    await db.execute("DELETE FROM api_keys WHERE id=$1", kid)
    await db.execute("DELETE FROM app_users WHERE id=$1", uid)


def test_api_root_valid_key_returns_200(client, api_key_pair):
    response = client.get("/api/v1/", headers={"X-API-Key": api_key_pair})
    assert response.status_code == 200


def test_api_root_invalid_key_returns_401(client):
    response = client.get("/api/v1/", headers={"X-API-Key": "pm_notavalidkey"})
    assert response.status_code == 401


def test_api_root_missing_key_returns_403(client):
    """APIKeyHeader returns 403 when header is absent."""
    response = client.get("/api/v1/")
    assert response.status_code == 403


async def test_api_valid_key_updates_last_used_at(client, api_key_pair, db):
    client.get("/api/v1/", headers={"X-API-Key": api_key_pair})
    key_hash = hashlib.sha256(api_key_pair.encode()).hexdigest()
    row = await db.fetchrow(
        "SELECT last_used_at FROM api_keys WHERE key_hash=$1", key_hash
    )
    assert row["last_used_at"] is not None
