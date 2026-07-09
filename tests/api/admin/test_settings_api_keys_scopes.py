"""Integration tests: API key scope grant/revoke admin routes."""

import hashlib
import os

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from src.api.admin.deps import get_db
from src.api.main import app
from src.core.db import generate_id

pytestmark = [
    pytest.mark.integration,
]

AUTH_HEADERS = {"X-ExeDev-UserID": "usr_test", "X-ExeDev-Email": "admin@test.com"}
HTMX_HEADERS = {**AUTH_HEADERS, "HX-Request": "true"}


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


@pytest_asyncio.fixture(loop_scope="session")
async def user_and_key(db):
    """Insert app_user + api_key owned by usr_test; yield (uid, kid)."""
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
        "Scope Test Key",
        raw_key[:8],
        key_hash,
    )

    yield uid, kid


async def _has_scope(db, key_id: str, scope_id: str) -> bool:
    row = await db.fetchrow(
        "SELECT 1 FROM api_key_scopes WHERE api_key_id=$1 AND scope_id=$2",
        key_id,
        scope_id,
    )
    return row is not None


# ---------------------------------------------------------------------------
# GET detail — HTML structure (issue #192)
# ---------------------------------------------------------------------------


async def test_detail_panel_renders_as_table_row(client, user_and_key):
    """Scopes panel must be a <tr>, not a <div>, to remain valid inside <tbody>."""
    _, kid = user_and_key
    r = await client.get(f"/admin/settings/api-keys/{kid}/detail/", headers=HTMX_HEADERS)
    assert r.status_code == 200
    assert r.text.strip().startswith("<tr")


async def test_detail_panel_has_close_button(client, user_and_key):
    _, kid = user_and_key
    r = await client.get(f"/admin/settings/api-keys/{kid}/detail/", headers=HTMX_HEADERS)
    assert r.status_code == 200
    assert ">Close<" in r.text


async def test_read_row_scopes_placeholder_is_tr(client, user_and_key):
    """Empty scopes placeholder in the key row must be a <tr>, not a <div>."""
    _, kid = user_and_key
    r = await client.get(f"/admin/settings/api-keys/{kid}/read-row/", headers=AUTH_HEADERS)
    assert r.status_code == 200
    assert f'<tr id="api-key-scopes-{kid}"' in r.text
    assert f'<div id="api-key-scopes-{kid}"' not in r.text


async def test_scopes_button_closes_other_panels(client, user_and_key):
    """Scopes button must include hx-on::before-request to collapse sibling panels."""
    _, kid = user_and_key
    r = await client.get(f"/admin/settings/api-keys/{kid}/read-row/", headers=AUTH_HEADERS)
    assert r.status_code == 200
    assert "hx-on::before-request" in r.text


async def test_delete_button_clears_scopes_row(client, user_and_key):
    """Delete button must clean up its sibling scopes <tr> via hx-on::after-request."""
    _, kid = user_and_key
    r = await client.get(f"/admin/settings/api-keys/{kid}/read-row/", headers=AUTH_HEADERS)
    assert r.status_code == 200
    assert "hx-on::after-request" in r.text
    assert f"getElementById('api-key-scopes-{kid}')" in r.text


# ---------------------------------------------------------------------------
# a11y — disambiguating aria-labels on looped scope buttons (#247)
#
# The static lint (test_aria_labels.py) only guards aria-label *presence*; these
# render-level tests lock in the disambiguating *content* (scope id / key label)
# that makes the labels WCAG SC 2.4.6-compliant.
# ---------------------------------------------------------------------------


async def test_revoke_button_has_scope_scoped_aria_label(client, user_and_key, db):
    """Each Revoke button carries aria-label="Revoke <scope_id>" — N otherwise
    identical "Revoke" buttons must be distinguishable to assistive tech."""
    _, kid = user_and_key
    await db.execute(
        "INSERT INTO api_key_scopes (api_key_id, scope_id) VALUES ($1,$2) ON CONFLICT DO NOTHING",
        kid,
        "observations:write",
    )
    r = await client.get(f"/admin/settings/api-keys/{kid}/detail/", headers=HTMX_HEADERS)
    assert r.status_code == 200
    assert 'aria-label="Revoke observations:write"' in r.text


async def test_grant_button_has_scope_scoped_aria_label(client, user_and_key):
    """Each Grant button carries aria-label="Grant <scope_id>, <description>"; the
    scope id disambiguates and the description is folded into the label (not a
    separate .visually-hidden span). The trailing ", " proves the fold-in fired —
    observations:write has a seeded description — without coupling to its wording."""
    _, kid = user_and_key
    r = await client.get(f"/admin/settings/api-keys/{kid}/detail/", headers=HTMX_HEADERS)
    assert r.status_code == 200
    assert 'aria-label="Grant observations:write, ' in r.text


async def test_close_button_has_key_scoped_aria_label(client, user_and_key):
    """The Close button is key-scoped (by label) so multiple open scope panels do
    not present identical "Close" accessible names."""
    _, kid = user_and_key
    r = await client.get(f"/admin/settings/api-keys/{kid}/detail/", headers=HTMX_HEADERS)
    assert r.status_code == 200
    assert 'aria-label="Close scopes for Scope Test Key"' in r.text


# ---------------------------------------------------------------------------
# GET detail
# ---------------------------------------------------------------------------


async def test_detail_returns_200(client, user_and_key):
    _, kid = user_and_key
    r = await client.get(f"/admin/settings/api-keys/{kid}/detail/", headers=HTMX_HEADERS)
    assert r.status_code == 200


async def test_detail_shows_scope_panel(client, user_and_key):
    _, kid = user_and_key
    r = await client.get(f"/admin/settings/api-keys/{kid}/detail/", headers=HTMX_HEADERS)
    assert r.status_code == 200
    assert f"api-key-scopes-{kid}" in r.text


async def test_detail_shows_available_scope_grant_button(client, user_and_key):
    _, kid = user_and_key
    r = await client.get(f"/admin/settings/api-keys/{kid}/detail/", headers=HTMX_HEADERS)
    assert r.status_code == 200
    assert "observations:write" in r.text


async def test_detail_404_for_other_users_key(client, db):
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
    r = await client.get(
        f"/admin/settings/api-keys/{kid}/detail/",
        headers=HTMX_HEADERS,
    )
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# Grant scope
# ---------------------------------------------------------------------------


async def test_grant_scope_returns_200(client, user_and_key, db):
    _, kid = user_and_key
    r = await client.post(
        f"/admin/settings/api-keys/{kid}/scopes/observations:write/grant/",
        headers=HTMX_HEADERS,
    )
    assert r.status_code == 200


async def test_grant_scope_persists_to_db(client, user_and_key, db):
    _, kid = user_and_key
    await client.post(
        f"/admin/settings/api-keys/{kid}/scopes/observations:write/grant/",
        headers=HTMX_HEADERS,
    )
    assert await _has_scope(db, kid, "observations:write")


async def test_grant_scope_idempotent(client, user_and_key, db):
    """Second grant must not fail."""
    _, kid = user_and_key
    r1 = await client.post(
        f"/admin/settings/api-keys/{kid}/scopes/observations:write/grant/",
        headers=HTMX_HEADERS,
    )
    r2 = await client.post(
        f"/admin/settings/api-keys/{kid}/scopes/observations:write/grant/",
        headers=HTMX_HEADERS,
    )
    assert r1.status_code == 200
    assert r2.status_code == 200


async def test_grant_scope_returns_scope_panel_html(client, user_and_key, db):
    _, kid = user_and_key
    r = await client.post(
        f"/admin/settings/api-keys/{kid}/scopes/observations:write/grant/",
        headers=HTMX_HEADERS,
    )
    assert r.status_code == 200
    assert f"api-key-scopes-{kid}" in r.text


async def test_grant_scope_404_for_other_users_key(client, db):
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
    r = await client.post(
        f"/admin/settings/api-keys/{kid}/scopes/observations:write/grant/",
        headers=HTMX_HEADERS,
    )
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# Revoke scope
# ---------------------------------------------------------------------------


async def test_revoke_scope_returns_200(client, user_and_key, db):
    _, kid = user_and_key
    await db.execute(
        "INSERT INTO api_key_scopes (api_key_id, scope_id) VALUES ($1,$2) ON CONFLICT DO NOTHING",
        kid,
        "observations:write",
    )
    r = await client.post(
        f"/admin/settings/api-keys/{kid}/scopes/observations:write/revoke/",
        headers=HTMX_HEADERS,
    )
    assert r.status_code == 200


async def test_revoke_scope_removes_from_db(client, user_and_key, db):
    _, kid = user_and_key
    await db.execute(
        "INSERT INTO api_key_scopes (api_key_id, scope_id) VALUES ($1,$2) ON CONFLICT DO NOTHING",
        kid,
        "observations:write",
    )
    await client.post(
        f"/admin/settings/api-keys/{kid}/scopes/observations:write/revoke/",
        headers=HTMX_HEADERS,
    )
    assert not await _has_scope(db, kid, "observations:write")


async def test_revoke_scope_noop_when_not_granted(client, user_and_key, db):
    """Revoke when not granted must not fail."""
    _, kid = user_and_key
    # Ensure scope is not present
    await db.execute(
        "DELETE FROM api_key_scopes WHERE api_key_id=$1 AND scope_id=$2",
        kid,
        "observations:write",
    )
    r = await client.post(
        f"/admin/settings/api-keys/{kid}/scopes/observations:write/revoke/",
        headers=HTMX_HEADERS,
    )
    assert r.status_code == 200


async def test_revoke_scope_returns_scope_panel_html(client, user_and_key, db):
    _, kid = user_and_key
    await db.execute(
        "INSERT INTO api_key_scopes (api_key_id, scope_id) VALUES ($1,$2) ON CONFLICT DO NOTHING",
        kid,
        "observations:write",
    )
    r = await client.post(
        f"/admin/settings/api-keys/{kid}/scopes/observations:write/revoke/",
        headers=HTMX_HEADERS,
    )
    assert r.status_code == 200
    assert f"api-key-scopes-{kid}" in r.text


async def test_revoke_scope_404_for_other_users_key(client, db):
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
    r = await client.post(
        f"/admin/settings/api-keys/{kid}/scopes/observations:write/revoke/",
        headers=HTMX_HEADERS,
    )
    assert r.status_code == 404
