"""Integration tests for API key management admin routes."""

import hashlib
import os

import asyncpg
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from src.api.admin.deps import get_db
from src.api.admin.settings_api_keys import generate_api_key
from src.api.main import app
from src.core.db import generate_id

pytestmark = [
    pytest.mark.integration,
]

AUTH_HEADERS = {
    "X-ExeDev-UserID": "usr_test",
    "X-ExeDev-Email": "admin@test.com",
}


@pytest_asyncio.fixture(loop_scope="session")
async def db(db_pool):
    """Pool-acquired connection wrapped in a rolled-back transaction."""
    async with db_pool.acquire() as conn:
        tr = conn.transaction()
        await tr.start()
        try:
            yield conn
        finally:
            await tr.rollback()


@pytest_asyncio.fixture(loop_scope="session")
async def client(db):
    """AsyncClient with app, overriding get_db to use the test connection."""

    async def _get_db_override():
        yield db

    app.dependency_overrides[get_db] = _get_db_override
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test", follow_redirects=True
    ) as c:
        yield c
    app.dependency_overrides.pop(get_db, None)


# --- Schema ---


async def test_app_users_table_exists(db):
    row = await db.fetchrow("SELECT 1 FROM information_schema.tables WHERE table_name='app_users'")
    assert row is not None


async def test_api_keys_table_exists(db):
    row = await db.fetchrow("SELECT 1 FROM information_schema.tables WHERE table_name='api_keys'")
    assert row is not None


async def test_api_keys_key_hash_unique(db):
    uid = generate_id()
    await db.execute("INSERT INTO app_users (id, email) VALUES ($1, $2)", uid, "a@test.com")
    kid1 = generate_id()
    kid2 = generate_id()
    await db.execute(
        "INSERT INTO api_keys (id, user_id, label, key_prefix, key_hash) VALUES ($1,$2,$3,$4,$5)",
        kid1,
        uid,
        "key1",
        "pm_abc123",
        "deadbeef" * 8,
    )
    # Savepoint so the deliberate duplicate aborts only this INSERT, not the
    # surrounding rollback transaction (#288). Cleanup is handled by rollback.
    with pytest.raises(asyncpg.UniqueViolationError):
        async with db.transaction():
            await db.execute(
                "INSERT INTO api_keys (id, user_id, label, key_prefix, key_hash)"
                " VALUES ($1,$2,$3,$4,$5)",
                kid2,
                uid,
                "key2",
                "pm_abc124",
                "deadbeef" * 8,
            )


# --- provision_app_user ---


async def test_provision_app_user_creates_row(db):
    """provision_app_user upserts an app_users row for the current user."""
    from src.api.admin.deps import AdminUser, provision_app_user

    user = AdminUser(id="usr_provision_test", email="provision@test.com")

    # Call dep directly with real db
    result = await provision_app_user(user=user, db=db)
    assert result.id == "usr_provision_test"

    row = await db.fetchrow("SELECT id, email FROM app_users WHERE id=$1", "usr_provision_test")
    assert row is not None
    assert row["email"] == "provision@test.com"
    await db.execute("DELETE FROM app_users WHERE id='usr_provision_test'")


async def test_provision_app_user_updates_email_on_conflict(db):
    """provision_app_user updates email when user already exists."""
    from src.api.admin.deps import AdminUser, provision_app_user

    uid = "usr_upsert_test"
    await db.execute("INSERT INTO app_users (id, email) VALUES ($1,$2)", uid, "old@test.com")
    try:
        user = AdminUser(id=uid, email="new@test.com")
        await provision_app_user(user=user, db=db)
        row = await db.fetchrow("SELECT email FROM app_users WHERE id=$1", uid)
        assert row["email"] == "new@test.com"
    finally:
        await db.execute("DELETE FROM app_users WHERE id=$1", uid)


# --- generate_api_key (unit) ---


async def test_generate_api_key_format():
    raw_key, key_hash, key_prefix = generate_api_key()
    assert raw_key.startswith("pm_")
    assert len(raw_key) == 35  # "pm_" + 32 hex chars
    assert len(key_hash) == 64  # SHA-256 hex
    assert key_prefix == raw_key[:8]


async def test_generate_api_key_is_random():
    raw1, _, _ = generate_api_key()
    raw2, _, _ = generate_api_key()
    assert raw1 != raw2


async def test_generate_api_key_hash_matches():
    raw_key, key_hash, _ = generate_api_key()
    expected = hashlib.sha256(raw_key.encode()).hexdigest()
    assert key_hash == expected


# --- API keys routes ---


async def _make_user_and_key(db, label="My Key"):
    """Helper: insert app_user + api_key owned by usr_test, return (uid, kid, raw_key).

    Uses usr_test as the user ID so AUTH_HEADERS routes can find the key.
    """
    uid = "usr_test"
    kid = generate_id()
    raw_key = "pm_" + os.urandom(16).hex()
    key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
    await db.execute(
        "INSERT INTO app_users (id, email) VALUES ($1,$2)"
        " ON CONFLICT (id) DO UPDATE SET email = EXCLUDED.email",
        uid,
        "admin@test.com",
    )
    await db.execute(
        "INSERT INTO api_keys (id, user_id, label, key_prefix, key_hash) VALUES ($1,$2,$3,$4,$5)",
        kid,
        uid,
        label,
        raw_key[:8],
        key_hash,
    )
    return uid, kid, raw_key


async def test_api_keys_list_requires_auth(client):
    r = await client.get("/admin/settings/api-keys/", follow_redirects=False)
    assert r.status_code in (302, 307)


async def test_api_keys_list_returns_200(client):
    r = await client.get("/admin/settings/api-keys/", headers=AUTH_HEADERS)
    assert r.status_code == 200


async def test_api_keys_new_row_returns_form(client):
    r = await client.get("/admin/settings/api-keys/new-row/", headers=AUTH_HEADERS)
    assert r.status_code == 200
    assert "label" in r.text


async def test_api_keys_create_returns_modal(client, db):
    r = await client.post(
        "/admin/settings/api-keys/",
        headers={**AUTH_HEADERS, "HX-Request": "true"},
        data={"label": "Test Key"},
    )
    assert r.status_code == 200
    assert "pm_" in r.text  # raw key in modal
    assert "not be shown again" in r.text
    # Clean up: find the inserted key
    await db.execute("DELETE FROM api_keys WHERE user_id='usr_test'")
    await db.execute("DELETE FROM app_users WHERE id='usr_test'")


async def test_api_keys_create_non_htmx_redirects(client, db):
    r = await client.post(
        "/admin/settings/api-keys/",
        headers=AUTH_HEADERS,
        data={"label": "Non-HTMX Key"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"] == "/admin/settings/api-keys/?flash=saved"
    await db.execute("DELETE FROM api_keys WHERE user_id='usr_test'")
    await db.execute("DELETE FROM app_users WHERE id='usr_test'")


async def test_api_keys_edit_row_get(client, db):
    uid, kid, _ = await _make_user_and_key(db)
    try:
        r = await client.get(f"/admin/settings/api-keys/{kid}/edit-row/", headers=AUTH_HEADERS)
        assert r.status_code == 200
        assert "My Key" in r.text
    finally:
        await db.execute("DELETE FROM api_keys WHERE id=$1", kid)
        await db.execute("DELETE FROM app_users WHERE id=$1", uid)


async def test_api_keys_edit_row_post(client, db):
    uid, kid, _ = await _make_user_and_key(db)
    try:
        r = await client.post(
            f"/admin/settings/api-keys/{kid}/edit-row/",
            headers={**AUTH_HEADERS, "HX-Request": "true"},
            data={"label": "Renamed Key"},
        )
        assert r.status_code == 200
        assert "Renamed Key" in r.text
    finally:
        await db.execute("DELETE FROM api_keys WHERE id=$1", kid)
        await db.execute("DELETE FROM app_users WHERE id=$1", uid)


async def test_api_keys_read_row(client, db):
    uid, kid, _ = await _make_user_and_key(db)
    try:
        r = await client.get(f"/admin/settings/api-keys/{kid}/read-row/", headers=AUTH_HEADERS)
        assert r.status_code == 200
        assert "My Key" in r.text
    finally:
        await db.execute("DELETE FROM api_keys WHERE id=$1", kid)
        await db.execute("DELETE FROM app_users WHERE id=$1", uid)


async def test_api_keys_delete(client, db):
    uid, kid, _ = await _make_user_and_key(db)
    try:
        r = await client.delete(
            f"/admin/settings/api-keys/{kid}/",
            headers={**AUTH_HEADERS, "HX-Request": "true"},
        )
        assert r.status_code == 200
        row = await db.fetchrow("SELECT id FROM api_keys WHERE id=$1", kid)
        assert row is None
    finally:
        await db.execute("DELETE FROM api_keys WHERE id=$1", kid)
        await db.execute("DELETE FROM app_users WHERE id=$1", uid)


async def test_api_keys_delete_404_when_not_found(client, db):
    r = await client.delete(
        "/admin/settings/api-keys/nonexistent/",
        headers={**AUTH_HEADERS, "HX-Request": "true"},
    )
    assert r.status_code == 404


async def test_api_keys_create_empty_label_rejected(client, db):
    r = await client.post(
        "/admin/settings/api-keys/",
        headers={**AUTH_HEADERS, "HX-Request": "true"},
        data={"label": "   "},
    )
    assert r.status_code == 422


async def test_api_keys_edit_row_empty_label_rejected(client, db):
    uid, kid, _ = await _make_user_and_key(db)
    try:
        r = await client.post(
            f"/admin/settings/api-keys/{kid}/edit-row/",
            headers={**AUTH_HEADERS, "HX-Request": "true"},
            data={"label": "   "},
        )
        assert r.status_code == 422
    finally:
        await db.execute("DELETE FROM api_keys WHERE id=$1", kid)
        await db.execute("DELETE FROM app_users WHERE id=$1", uid)


async def test_api_keys_edit_other_users_key_returns_404(client, db):
    """User cannot edit a key belonging to a different user."""
    other_uid = generate_id()
    kid = generate_id()
    raw_key = "pm_" + os.urandom(16).hex()
    key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
    await db.execute(
        "INSERT INTO app_users (id, email) VALUES ($1,$2)", other_uid, "other@test.com"
    )
    await db.execute(
        "INSERT INTO api_keys (id, user_id, label, key_prefix, key_hash) VALUES ($1,$2,$3,$4,$5)",
        kid,
        other_uid,
        "Other Key",
        raw_key[:8],
        key_hash,
    )
    try:
        # AUTH_HEADERS authenticates as usr_test, not other_uid
        r = await client.post(
            f"/admin/settings/api-keys/{kid}/edit-row/",
            headers={**AUTH_HEADERS, "HX-Request": "true"},
            data={"label": "Hijacked"},
        )
        assert r.status_code == 404
    finally:
        await db.execute("DELETE FROM api_keys WHERE id=$1", kid)
        await db.execute("DELETE FROM app_users WHERE id=$1", other_uid)


async def test_api_keys_delete_other_users_key_returns_404(client, db):
    """User cannot delete a key belonging to a different user."""
    other_uid = generate_id()
    kid = generate_id()
    raw_key = "pm_" + os.urandom(16).hex()
    key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
    await db.execute(
        "INSERT INTO app_users (id, email) VALUES ($1,$2)", other_uid, "other@test.com"
    )
    await db.execute(
        "INSERT INTO api_keys (id, user_id, label, key_prefix, key_hash) VALUES ($1,$2,$3,$4,$5)",
        kid,
        other_uid,
        "Other Key",
        raw_key[:8],
        key_hash,
    )
    try:
        r = await client.delete(
            f"/admin/settings/api-keys/{kid}/",
            headers={**AUTH_HEADERS, "HX-Request": "true"},
        )
        assert r.status_code == 404
    finally:
        await db.execute("DELETE FROM api_keys WHERE id=$1", kid)
        await db.execute("DELETE FROM app_users WHERE id=$1", other_uid)
