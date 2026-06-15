"""Tests for require_scope dependency factory."""

import hashlib
import os

import pytest
import pytest_asyncio
from fastapi import Depends

from src.api.main import app
from src.api.public.deps import require_scope
from src.core.db import generate_id

pytestmark = pytest.mark.integration


# At module level, after imports — registered once per process
@app.get("/api/v1/_test/require_scope", dependencies=[Depends(require_scope("observations:write"))])
async def _test_scope_endpoint():
    return {"ok": True}


@pytest_asyncio.fixture(loop_scope="session")
async def scope_fixture(db):
    """Ensure api_key_scope_types includes observations:write; yield scope_id."""
    scope_id = "observations:write"
    # Check if it exists, insert if not (idempotent for test robustness)
    existing = await db.fetchrow("SELECT id FROM api_key_scope_types WHERE id = $1", scope_id)
    if not existing:
        await db.execute(
            "INSERT INTO api_key_scope_types (id, display_name, description) VALUES ($1, $2, $3)",
            scope_id,
            "Observations Write",
            "Create and update observations",
        )
    yield scope_id


@pytest_asyncio.fixture(loop_scope="session")
async def api_key_with_scope(db, scope_fixture):
    """Create an API key WITH the observations:write scope; yield (raw_key, key_id)."""
    uid = generate_id()
    kid = generate_id()
    raw_key = "pm_" + os.urandom(16).hex()
    key_hash = hashlib.sha256(raw_key.encode()).hexdigest()

    await db.execute("INSERT INTO app_users (id, email) VALUES ($1,$2)", uid, "scope_test@test.com")
    await db.execute(
        "INSERT INTO api_keys (id, user_id, label, key_prefix, key_hash) VALUES ($1,$2,$3,$4,$5)",
        kid,
        uid,
        "Scope Test Key",
        raw_key[:8],
        key_hash,
    )
    await db.execute(
        "INSERT INTO api_key_scopes (api_key_id, scope_id) VALUES ($1, $2)",
        kid,
        scope_fixture,
    )
    yield raw_key, kid

    await db.execute("DELETE FROM api_key_scopes WHERE api_key_id=$1", kid)
    await db.execute("DELETE FROM api_keys WHERE id=$1", kid)
    await db.execute("DELETE FROM app_users WHERE id=$1", uid)


@pytest_asyncio.fixture(loop_scope="session")
async def api_key_without_scope(db, scope_fixture):
    """Create an API key WITHOUT the observations:write scope; yield (raw_key, key_id)."""
    uid = generate_id()
    kid = generate_id()
    raw_key = "pm_" + os.urandom(16).hex()
    key_hash = hashlib.sha256(raw_key.encode()).hexdigest()

    await db.execute(
        "INSERT INTO app_users (id, email) VALUES ($1,$2)", uid, "noscope_test@test.com"
    )
    await db.execute(
        "INSERT INTO api_keys (id, user_id, label, key_prefix, key_hash) VALUES ($1,$2,$3,$4,$5)",
        kid,
        uid,
        "No-Scope Test Key",
        raw_key[:8],
        key_hash,
    )
    # Note: NO api_key_scopes row inserted
    yield raw_key, kid

    await db.execute("DELETE FROM api_keys WHERE id=$1", kid)
    await db.execute("DELETE FROM app_users WHERE id=$1", uid)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_valid_key_with_correct_scope_returns_user_id(
    client, api_key_with_scope, scope_fixture
):
    """Test route using require_scope with valid key + matching scope → 200."""
    raw_key, _ = api_key_with_scope
    r = client.get(
        "/api/v1/_test/require_scope",
        headers={"X-API-Key": raw_key},
    )
    assert r.status_code == 200
    body = r.json()
    assert body == {"ok": True}


@pytest.mark.integration
async def test_valid_key_without_scope_returns_403(client, api_key_without_scope, scope_fixture):
    """Test route using require_scope with valid key but missing scope.

    Expects 403 'Insufficient scope'.
    """
    raw_key, _ = api_key_without_scope
    r = client.get(
        "/api/v1/_test/require_scope",
        headers={"X-API-Key": raw_key},
    )
    assert r.status_code == 403
    body = r.json()
    assert "detail" in body
    assert "Insufficient scope" in body["detail"]


@pytest.mark.integration
async def test_invalid_key_returns_401(client, scope_fixture):
    """Test route using require_scope with invalid key → 401."""
    r = client.get(
        "/api/v1/_test/require_scope",
        headers={"X-API-Key": "pm_invalid_key_123456"},
    )
    assert r.status_code == 401
    body = r.json()
    assert "detail" in body
    assert "Invalid API key" in body["detail"]


@pytest.mark.integration
async def test_missing_key_returns_403_not_authenticated(client, scope_fixture):
    """Test route using require_scope with no key → 403 'Not authenticated'."""
    r = client.get("/api/v1/_test/require_scope")
    assert r.status_code == 403
    body = r.json()
    assert "detail" in body
    assert "Not authenticated" in body["detail"]
