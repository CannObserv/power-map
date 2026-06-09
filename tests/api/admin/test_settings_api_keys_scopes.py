"""Integration tests: API key scope grant/revoke admin routes."""

import hashlib
import os

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient

from src.api.main import app
from src.core.db import generate_id

pytestmark = [
    pytest.mark.integration,
]

AUTH_HEADERS = {"X-ExeDev-UserID": "usr_test", "X-ExeDev-Email": "admin@test.com"}
HTMX_HEADERS = {**AUTH_HEADERS, "HX-Request": "true"}


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


@pytest_asyncio.fixture(loop_scope="session")
async def user_and_key(db_pool):
    """Insert app_user + api_key owned by usr_test; yield (uid, kid)."""
    uid = "usr_test"
    kid = generate_id()
    raw_key = "pm_" + os.urandom(16).hex()
    key_hash = hashlib.sha256(raw_key.encode()).hexdigest()

    async with db_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO app_users (id, email) VALUES ($1,$2)"
            " ON CONFLICT (id) DO UPDATE SET email = EXCLUDED.email",
            uid,
            "admin@test.com",
        )
        await conn.execute(
            "INSERT INTO api_keys (id, user_id, label, key_prefix, key_hash)"
            " VALUES ($1,$2,$3,$4,$5)",
            kid,
            uid,
            "Scope Test Key",
            raw_key[:8],
            key_hash,
        )

    yield uid, kid

    async with db_pool.acquire() as conn:
        await conn.execute("DELETE FROM api_key_scopes WHERE api_key_id=$1", kid)
        await conn.execute("DELETE FROM api_keys WHERE id=$1", kid)
        await conn.execute("DELETE FROM app_users WHERE id=$1", uid)


async def _has_scope(db_pool, key_id: str, scope_id: str) -> bool:
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT 1 FROM api_key_scopes WHERE api_key_id=$1 AND scope_id=$2",
            key_id,
            scope_id,
        )
    return row is not None


# ---------------------------------------------------------------------------
# GET detail
# ---------------------------------------------------------------------------


async def test_detail_returns_200(client, user_and_key):
    _, kid = user_and_key
    r = client.get(f"/admin/settings/api-keys/{kid}/detail/", headers=HTMX_HEADERS)
    assert r.status_code == 200


async def test_detail_shows_scope_panel(client, user_and_key):
    _, kid = user_and_key
    r = client.get(f"/admin/settings/api-keys/{kid}/detail/", headers=HTMX_HEADERS)
    assert r.status_code == 200
    assert f"api-key-scopes-{kid}" in r.text


async def test_detail_shows_available_scope_grant_button(client, user_and_key):
    _, kid = user_and_key
    r = client.get(f"/admin/settings/api-keys/{kid}/detail/", headers=HTMX_HEADERS)
    assert r.status_code == 200
    assert "observations:write" in r.text


async def test_detail_404_for_other_users_key(client, db_pool):
    other_uid = generate_id()
    kid = generate_id()
    raw_key = "pm_" + os.urandom(16).hex()
    key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
    async with db_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO app_users (id, email) VALUES ($1,$2)", other_uid, "other@test.com"
        )
        await conn.execute(
            "INSERT INTO api_keys (id, user_id, label, key_prefix, key_hash)"
            " VALUES ($1,$2,$3,$4,$5)",
            kid,
            other_uid,
            "Other Key",
            raw_key[:8],
            key_hash,
        )
    try:
        r = client.get(
            f"/admin/settings/api-keys/{kid}/detail/",
            headers=HTMX_HEADERS,
        )
        assert r.status_code == 404
    finally:
        async with db_pool.acquire() as conn:
            await conn.execute("DELETE FROM api_keys WHERE id=$1", kid)
            await conn.execute("DELETE FROM app_users WHERE id=$1", other_uid)


# ---------------------------------------------------------------------------
# Grant scope
# ---------------------------------------------------------------------------


async def test_grant_scope_returns_200(client, user_and_key, db_pool):
    _, kid = user_and_key
    try:
        r = client.post(
            f"/admin/settings/api-keys/{kid}/scopes/observations:write/grant/",
            headers=HTMX_HEADERS,
        )
        assert r.status_code == 200
    finally:
        async with db_pool.acquire() as conn:
            await conn.execute("DELETE FROM api_key_scopes WHERE api_key_id=$1", kid)


async def test_grant_scope_persists_to_db(client, user_and_key, db_pool):
    _, kid = user_and_key
    try:
        client.post(
            f"/admin/settings/api-keys/{kid}/scopes/observations:write/grant/",
            headers=HTMX_HEADERS,
        )
        assert await _has_scope(db_pool, kid, "observations:write")
    finally:
        async with db_pool.acquire() as conn:
            await conn.execute("DELETE FROM api_key_scopes WHERE api_key_id=$1", kid)


async def test_grant_scope_idempotent(client, user_and_key, db_pool):
    """Second grant must not fail."""
    _, kid = user_and_key
    try:
        r1 = client.post(
            f"/admin/settings/api-keys/{kid}/scopes/observations:write/grant/",
            headers=HTMX_HEADERS,
        )
        r2 = client.post(
            f"/admin/settings/api-keys/{kid}/scopes/observations:write/grant/",
            headers=HTMX_HEADERS,
        )
        assert r1.status_code == 200
        assert r2.status_code == 200
    finally:
        async with db_pool.acquire() as conn:
            await conn.execute("DELETE FROM api_key_scopes WHERE api_key_id=$1", kid)


async def test_grant_scope_returns_scope_panel_html(client, user_and_key, db_pool):
    _, kid = user_and_key
    try:
        r = client.post(
            f"/admin/settings/api-keys/{kid}/scopes/observations:write/grant/",
            headers=HTMX_HEADERS,
        )
        assert r.status_code == 200
        assert f"api-key-scopes-{kid}" in r.text
    finally:
        async with db_pool.acquire() as conn:
            await conn.execute("DELETE FROM api_key_scopes WHERE api_key_id=$1", kid)


async def test_grant_scope_404_for_other_users_key(client, db_pool):
    other_uid = generate_id()
    kid = generate_id()
    raw_key = "pm_" + os.urandom(16).hex()
    key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
    async with db_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO app_users (id, email) VALUES ($1,$2)", other_uid, "other@test.com"
        )
        await conn.execute(
            "INSERT INTO api_keys (id, user_id, label, key_prefix, key_hash)"
            " VALUES ($1,$2,$3,$4,$5)",
            kid,
            other_uid,
            "Other Key",
            raw_key[:8],
            key_hash,
        )
    try:
        r = client.post(
            f"/admin/settings/api-keys/{kid}/scopes/observations:write/grant/",
            headers=HTMX_HEADERS,
        )
        assert r.status_code == 404
    finally:
        async with db_pool.acquire() as conn:
            await conn.execute("DELETE FROM api_key_scopes WHERE api_key_id=$1", kid)
            await conn.execute("DELETE FROM api_keys WHERE id=$1", kid)
            await conn.execute("DELETE FROM app_users WHERE id=$1", other_uid)


# ---------------------------------------------------------------------------
# Revoke scope
# ---------------------------------------------------------------------------


async def test_revoke_scope_returns_200(client, user_and_key, db_pool):
    _, kid = user_and_key
    async with db_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO api_key_scopes (api_key_id, scope_id) VALUES ($1,$2)"
            " ON CONFLICT DO NOTHING",
            kid,
            "observations:write",
        )
    r = client.post(
        f"/admin/settings/api-keys/{kid}/scopes/observations:write/revoke/",
        headers=HTMX_HEADERS,
    )
    assert r.status_code == 200


async def test_revoke_scope_removes_from_db(client, user_and_key, db_pool):
    _, kid = user_and_key
    async with db_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO api_key_scopes (api_key_id, scope_id) VALUES ($1,$2)"
            " ON CONFLICT DO NOTHING",
            kid,
            "observations:write",
        )
    client.post(
        f"/admin/settings/api-keys/{kid}/scopes/observations:write/revoke/",
        headers=HTMX_HEADERS,
    )
    assert not await _has_scope(db_pool, kid, "observations:write")


async def test_revoke_scope_noop_when_not_granted(client, user_and_key, db_pool):
    """Revoke when not granted must not fail."""
    _, kid = user_and_key
    # Ensure scope is not present
    async with db_pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM api_key_scopes WHERE api_key_id=$1 AND scope_id=$2",
            kid,
            "observations:write",
        )
    r = client.post(
        f"/admin/settings/api-keys/{kid}/scopes/observations:write/revoke/",
        headers=HTMX_HEADERS,
    )
    assert r.status_code == 200


async def test_revoke_scope_returns_scope_panel_html(client, user_and_key, db_pool):
    _, kid = user_and_key
    async with db_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO api_key_scopes (api_key_id, scope_id) VALUES ($1,$2)"
            " ON CONFLICT DO NOTHING",
            kid,
            "observations:write",
        )
    r = client.post(
        f"/admin/settings/api-keys/{kid}/scopes/observations:write/revoke/",
        headers=HTMX_HEADERS,
    )
    assert r.status_code == 200
    assert f"api-key-scopes-{kid}" in r.text


async def test_revoke_scope_404_for_other_users_key(client, db_pool):
    other_uid = generate_id()
    kid = generate_id()
    raw_key = "pm_" + os.urandom(16).hex()
    key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
    async with db_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO app_users (id, email) VALUES ($1,$2)", other_uid, "other@test.com"
        )
        await conn.execute(
            "INSERT INTO api_keys (id, user_id, label, key_prefix, key_hash)"
            " VALUES ($1,$2,$3,$4,$5)",
            kid,
            other_uid,
            "Other Key",
            raw_key[:8],
            key_hash,
        )
    try:
        r = client.post(
            f"/admin/settings/api-keys/{kid}/scopes/observations:write/revoke/",
            headers=HTMX_HEADERS,
        )
        assert r.status_code == 404
    finally:
        async with db_pool.acquire() as conn:
            await conn.execute("DELETE FROM api_key_scopes WHERE api_key_id=$1", kid)
            await conn.execute("DELETE FROM api_keys WHERE id=$1", kid)
            await conn.execute("DELETE FROM app_users WHERE id=$1", other_uid)
