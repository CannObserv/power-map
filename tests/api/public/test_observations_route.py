"""Smoke tests for POST /api/v1/observations."""

import hashlib
import os

import pytest
import pytest_asyncio

from src.core.db import generate_id

pytestmark = [pytest.mark.asyncio(loop_scope="session")]


@pytest_asyncio.fixture(loop_scope="session")
async def obs_scope(db):
    """Ensure api_key_scope_types includes observations:write."""
    scope_id = "observations:write"
    existing = await db.fetchrow("SELECT id FROM api_key_scope_types WHERE id = $1", scope_id)
    if not existing:
        await db.execute(
            "INSERT INTO api_key_scope_types (id, display_name, description) VALUES ($1,$2,$3)",
            scope_id,
            "Observations Write",
            "Create and update observations",
        )
    yield scope_id


@pytest_asyncio.fixture(loop_scope="session")
async def obs_key_with_scope(db, obs_scope):
    """API key with observations:write scope; yields raw_key."""
    uid = generate_id()
    kid = generate_id()
    raw_key = "pm_" + os.urandom(16).hex()
    key_hash = hashlib.sha256(raw_key.encode()).hexdigest()

    await db.execute("INSERT INTO app_users (id, email) VALUES ($1,$2)", uid, "obsroute@test.com")
    await db.execute(
        "INSERT INTO api_keys (id, user_id, label, key_prefix, key_hash) VALUES ($1,$2,$3,$4,$5)",
        kid,
        uid,
        "Obs Route Key",
        raw_key[:8],
        key_hash,
    )
    await db.execute(
        "INSERT INTO api_key_scopes (api_key_id, scope_id) VALUES ($1,$2)",
        kid,
        obs_scope,
    )
    yield raw_key

    await db.execute("DELETE FROM api_key_scopes WHERE api_key_id=$1", kid)
    await db.execute("DELETE FROM api_keys WHERE id=$1", kid)
    await db.execute("DELETE FROM app_users WHERE id=$1", uid)


@pytest_asyncio.fixture(loop_scope="session")
async def obs_key_no_scope(db, obs_scope):
    """API key WITHOUT observations:write scope; yields raw_key."""
    uid = generate_id()
    kid = generate_id()
    raw_key = "pm_" + os.urandom(16).hex()
    key_hash = hashlib.sha256(raw_key.encode()).hexdigest()

    await db.execute(
        "INSERT INTO app_users (id, email) VALUES ($1,$2)", uid, "obsroute_noscope@test.com"
    )
    await db.execute(
        "INSERT INTO api_keys (id, user_id, label, key_prefix, key_hash) VALUES ($1,$2,$3,$4,$5)",
        kid,
        uid,
        "Obs No-Scope Key",
        raw_key[:8],
        key_hash,
    )
    yield raw_key

    await db.execute("DELETE FROM api_keys WHERE id=$1", kid)
    await db.execute("DELETE FROM app_users WHERE id=$1", uid)


@pytest_asyncio.fixture(loop_scope="session")
async def known_identifier_type(db):
    """Ensure at least one known identifier type exists; yield its slug."""
    slug = "obs_test_type"
    existing = await db.fetchrow("SELECT id FROM entity_identifier_types WHERE slug=$1", slug)
    if not existing:
        eit_id = generate_id()
        await db.execute(
            "INSERT INTO entity_identifier_types"
            " (id, entity_type, slug, display_name, full_name)"
            " VALUES ($1, 'organization', $2, 'Obs Test', 'Observation Test Type')",
            eit_id,
            slug,
        )
    yield slug


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_valid_key_and_scope_returns_disposition(
    client, obs_key_with_scope, known_identifier_type
):
    """Valid key + observations:write + minimal body → 200 with known disposition."""
    r = client.post(
        "/api/v1/observations",
        json={
            "identifier_type": known_identifier_type,
            "identifier_value": "obs_route_smoke_" + os.urandom(4).hex(),
        },
        headers={"X-API-Key": obs_key_with_scope},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["disposition"] in ("auto-attached", "new", "rejected")


@pytest.mark.integration
async def test_valid_key_no_scope_returns_403(client, obs_key_no_scope):
    """Valid key but missing observations:write scope → 403."""
    r = client.post(
        "/api/v1/observations",
        json={"identifier_type": "any_type", "identifier_value": "v"},
        headers={"X-API-Key": obs_key_no_scope},
    )
    assert r.status_code == 403


@pytest.mark.integration
async def test_missing_key_returns_403(client):
    """No X-API-Key header → 403."""
    r = client.post(
        "/api/v1/observations",
        json={"identifier_type": "any_type", "identifier_value": "v"},
    )
    assert r.status_code == 403


@pytest.mark.integration
async def test_invalid_key_returns_401(client):
    """Bad X-API-Key value → 401."""
    r = client.post(
        "/api/v1/observations",
        json={"identifier_type": "any_type", "identifier_value": "v"},
        headers={"X-API-Key": "pm_totally_invalid_key"},
    )
    assert r.status_code == 401


@pytest.mark.integration
async def test_unknown_identifier_type_returns_rejected(client, obs_key_with_scope):
    """Unknown identifier_type → 200 with disposition='rejected'."""
    r = client.post(
        "/api/v1/observations",
        json={
            "identifier_type": "zzz_does_not_exist_type",
            "identifier_value": "somevalue",
        },
        headers={"X-API-Key": obs_key_with_scope},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["disposition"] == "rejected"
    assert body["entity_id"] is None
    assert body["entity_type"] is None
