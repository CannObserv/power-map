"""Integration tests: rate limiter enforcement in the auth deps (#292).

Every authenticated ``/api/v1/*`` request funnels through ``_resolve_api_key``,
which consults the token-bucket limiter after key resolution. Exhausted bucket
-> 429 with ``Retry-After`` + ``X-RateLimit-*`` headers. Limits are patched
tiny and buckets reset per test so tests are deterministic and fast.
"""

import hashlib
import os
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from fastapi import HTTPException

from src.api.public import ratelimit as rl
from src.api.public.deps import _resolve_api_key
from src.core.db import generate_id

pytestmark = [
    pytest.mark.integration,
]


@pytest.fixture
def tiny_read_limit(monkeypatch):
    """Read bucket: burst 2, negligible refill — third GET must 429."""
    monkeypatch.setattr(rl, "_READ_REFILL_PER_S", 0.001)
    monkeypatch.setattr(rl, "_READ_BURST", 2)


@pytest_asyncio.fixture(loop_scope="session")
async def api_key(db):
    uid = generate_id()
    kid = generate_id()
    raw_key = "pm_" + os.urandom(16).hex()
    key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
    await db.execute("INSERT INTO app_users (id, email) VALUES ($1,$2)", uid, "rl@test.com")
    await db.execute(
        "INSERT INTO api_keys (id, user_id, label, key_prefix, key_hash) VALUES ($1,$2,$3,$4,$5)",
        kid,
        uid,
        "Rate Limit Key",
        raw_key[:8],
        key_hash,
    )
    return raw_key


@pytest_asyncio.fixture(loop_scope="session")
async def other_api_key(db):
    uid = generate_id()
    kid = generate_id()
    raw_key = "pm_" + os.urandom(16).hex()
    key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
    await db.execute("INSERT INTO app_users (id, email) VALUES ($1,$2)", uid, "rl2@test.com")
    await db.execute(
        "INSERT INTO api_keys (id, user_id, label, key_prefix, key_hash) VALUES ($1,$2,$3,$4,$5)",
        kid,
        uid,
        "Rate Limit Key 2",
        raw_key[:8],
        key_hash,
    )
    return raw_key


async def test_exhausted_read_bucket_returns_429(client, api_key, tiny_read_limit):
    for _ in range(2):
        r = await client.get("/api/v1/", headers={"X-API-Key": api_key})
        assert r.status_code == 200
    r = await client.get("/api/v1/", headers={"X-API-Key": api_key})
    assert r.status_code == 429


async def test_429_carries_retry_after_and_ratelimit_headers(client, api_key, tiny_read_limit):
    for _ in range(2):
        await client.get("/api/v1/", headers={"X-API-Key": api_key})
    r = await client.get("/api/v1/", headers={"X-API-Key": api_key})
    assert r.status_code == 429
    assert int(r.headers["retry-after"]) >= 1
    assert r.headers["x-ratelimit-limit"] == "2"
    assert r.headers["x-ratelimit-remaining"] == "0"
    assert r.json()["detail"] == "Rate limit exceeded"


async def test_write_bucket_unaffected_by_read_exhaustion(client, api_key, tiny_read_limit):
    for _ in range(3):
        await client.get("/api/v1/", headers={"X-API-Key": api_key})
    # Write bucket untouched: an unscoped POST fails on scope (403), not 429.
    r = await client.post(
        "/api/v1/assignments/observations", headers={"X-API-Key": api_key}, json={}
    )
    assert r.status_code == 403


async def test_other_key_unaffected(client, api_key, other_api_key, tiny_read_limit):
    for _ in range(3):
        await client.get("/api/v1/", headers={"X-API-Key": api_key})
    r = await client.get("/api/v1/", headers={"X-API-Key": other_api_key})
    assert r.status_code == 200


async def test_invalid_key_is_401_not_429(client, tiny_read_limit):
    """Unauthenticated traffic never reaches the limiter."""
    for _ in range(4):
        r = await client.get("/api/v1/", headers={"X-API-Key": "pm_bogus"})
        assert r.status_code == 401


async def test_throttled_request_skips_last_used_at_update(tiny_read_limit):
    """A 429 must not run the last_used_at UPDATE (unit — mocked db).

    The rollback-client fixtures freeze ``NOW()`` per transaction, so this is
    asserted at the dep level: when the limiter denies, ``db.execute`` (the
    UPDATE) is never called.
    """
    mock_db = AsyncMock()
    mock_db.fetchrow.return_value = {"id": "key_throttle_unit", "user_id": "u1"}
    # Exhaust the read bucket for this key id.
    rl.check("key_throttle_unit", "GET")
    rl.check("key_throttle_unit", "GET")
    with pytest.raises(HTTPException) as exc_info:
        await _resolve_api_key("pm_whatever", mock_db, request=None, method="GET")
    assert exc_info.value.status_code == 429
    mock_db.execute.assert_not_called()
