"""Integration tests for API key management admin routes."""

import os

import asyncpg
import pytest
from fastapi.testclient import TestClient

from src.api.main import app
from src.core.db import apply_schema, generate_id

pytestmark = pytest.mark.integration

AUTH_HEADERS = {
    "X-ExeDev-UserID": "usr_test",
    "X-ExeDev-Email": "admin@test.com",
}


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


# --- Schema ---

async def test_app_users_table_exists(db):
    row = await db.fetchrow(
        "SELECT 1 FROM information_schema.tables WHERE table_name='app_users'"
    )
    assert row is not None


async def test_api_keys_table_exists(db):
    row = await db.fetchrow(
        "SELECT 1 FROM information_schema.tables WHERE table_name='api_keys'"
    )
    assert row is not None


async def test_api_keys_key_hash_unique(db):
    uid = generate_id()
    await db.execute(
        "INSERT INTO app_users (id, email) VALUES ($1, $2)", uid, "a@test.com"
    )
    kid1 = generate_id()
    kid2 = generate_id()
    await db.execute(
        "INSERT INTO api_keys (id, user_id, label, key_prefix, key_hash)"
        " VALUES ($1,$2,$3,$4,$5)",
        kid1, uid, "key1", "pm_abc123", "deadbeef" * 8,
    )
    with pytest.raises(asyncpg.UniqueViolationError):
        await db.execute(
            "INSERT INTO api_keys (id, user_id, label, key_prefix, key_hash)"
            " VALUES ($1,$2,$3,$4,$5)",
            kid2, uid, "key2", "pm_abc124", "deadbeef" * 8,
        )
    await db.execute("DELETE FROM api_keys WHERE user_id=$1", uid)
    await db.execute("DELETE FROM app_users WHERE id=$1", uid)
