"""Tests for the pure-ASGI request-capture middleware (#260, step 3)."""

import hashlib
import json
import os

import pytest
import pytest_asyncio

from src.api.public.middleware import route_group_for_path
from src.core.db import generate_id

# ---------------------------------------------------------------------------
# Unit — route classification (no DB)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/people/observations",
        "/api/v1/orgs/observations",
        "/api/v1/jurisdictions/observations",
        "/api/v1/observations",
        "/api/v1/people/observations/",
    ],
)
def test_route_group_observations(path):
    assert route_group_for_path(path) == "observations"


def test_route_group_changes():
    assert route_group_for_path("/api/v1/changes") == "changes"
    assert route_group_for_path("/api/v1/changes/") == "changes"


@pytest.mark.parametrize(
    "path",
    ["/api/v1/", "/api/v1/people/01ABC", "/api/v1/orgs", "/api/v1/link-types"],
)
def test_route_group_other(path):
    assert route_group_for_path(path) == "other"


# ---------------------------------------------------------------------------
# Integration — capture end-to-end
# ---------------------------------------------------------------------------

pytestmark_integration = pytest.mark.integration


@pytest_asyncio.fixture(loop_scope="session")
async def plain_key(db):
    """A valid API key with no scopes (enough for require_api_key routes)."""
    uid = generate_id()
    kid = generate_id()
    raw = "pm_" + os.urandom(16).hex()
    key_hash = hashlib.sha256(raw.encode()).hexdigest()
    await db.execute("INSERT INTO app_users (id, email) VALUES ($1,$2)", uid, "arl_plain@test.com")
    await db.execute(
        "INSERT INTO api_keys (id, user_id, label, key_prefix, key_hash) VALUES ($1,$2,$3,$4,$5)",
        kid,
        uid,
        "ARL Plain Key",
        raw[:8],
        key_hash,
    )
    yield raw, kid
    await db.execute("DELETE FROM api_keys WHERE id=$1", kid)
    await db.execute("DELETE FROM app_users WHERE id=$1", uid)


@pytest_asyncio.fixture(loop_scope="session")
async def obs_key(db):
    """API key with observations:write scope (for the body-tee POST)."""
    scope_id = "observations:write"
    existing = await db.fetchrow("SELECT id FROM api_key_scope_types WHERE id=$1", scope_id)
    if not existing:
        await db.execute(
            "INSERT INTO api_key_scope_types (id, display_name, description) VALUES ($1,$2,$3)",
            scope_id,
            "Observations Write",
            "Create and update observations",
        )
    uid = generate_id()
    kid = generate_id()
    raw = "pm_" + os.urandom(16).hex()
    key_hash = hashlib.sha256(raw.encode()).hexdigest()
    await db.execute("INSERT INTO app_users (id, email) VALUES ($1,$2)", uid, "arl_obs@test.com")
    await db.execute(
        "INSERT INTO api_keys (id, user_id, label, key_prefix, key_hash) VALUES ($1,$2,$3,$4,$5)",
        kid,
        uid,
        "ARL Obs Key",
        raw[:8],
        key_hash,
    )
    await db.execute(
        "INSERT INTO api_key_scopes (api_key_id, scope_id) VALUES ($1,$2)", kid, scope_id
    )
    yield raw, kid
    await db.execute("DELETE FROM api_key_scopes WHERE api_key_id=$1", kid)
    await db.execute("DELETE FROM api_keys WHERE id=$1", kid)
    await db.execute("DELETE FROM app_users WHERE id=$1", uid)


@pytest.mark.integration
async def test_valid_v1_get_logs_row(client, db, plain_key):
    raw, kid = plain_key
    resp = client.get("/api/v1/", headers={"X-API-Key": raw})
    assert resp.status_code == 200
    row = await db.fetchrow(
        "SELECT * FROM api_request_log WHERE api_key_id=$1 ORDER BY id DESC LIMIT 1", kid
    )
    assert row is not None
    assert row["method"] == "GET"
    assert row["path"] == "/api/v1/"
    assert row["route_group"] == "other"
    assert row["status_code"] == 200
    assert row["latency_ms"] >= 0
    assert row["api_key_id"] == kid


@pytest.mark.integration
async def test_non_v1_path_not_logged(client, db):
    before = await db.fetchval("SELECT COUNT(*) FROM api_request_log WHERE path LIKE '/admin%'")
    client.get("/admin/")  # 307 redirect (no exe.dev headers) — must not be logged
    after = await db.fetchval("SELECT COUNT(*) FROM api_request_log WHERE path LIKE '/admin%'")
    assert after == before


@pytest.mark.integration
async def test_invalid_key_logs_null_key_row(client, db):
    resp = client.get("/api/v1/", headers={"X-API-Key": "pm_definitely_invalid"})
    assert resp.status_code == 401
    row = await db.fetchrow(
        "SELECT * FROM api_request_log WHERE path='/api/v1/' AND status_code=401"
        " ORDER BY id DESC LIMIT 1"
    )
    assert row is not None
    assert row["api_key_id"] is None
    assert row["route_group"] == "other"


@pytest.mark.integration
async def test_body_tee_preserves_downstream_and_captures_body(client, db, obs_key):
    """POSTing a body: downstream must still parse it (normal response) and we capture it."""
    raw, kid = obs_key
    payload = {"identifier_type": "definitely_unknown_type", "identifier_value": "x123"}
    resp = client.post("/api/v1/people/observations", json=payload, headers={"X-API-Key": raw})
    # Downstream read the JSON body and produced a normal ObservationResponse.
    assert resp.status_code == 200
    assert "disposition" in resp.json()
    row = await db.fetchrow(
        "SELECT * FROM api_request_log WHERE api_key_id=$1 AND route_group='observations'"
        " ORDER BY id DESC LIMIT 1",
        kid,
    )
    assert row is not None
    assert row["method"] == "POST"
    assert json.loads(row["request_body"]) == payload
    assert row["response_body"] is not None
