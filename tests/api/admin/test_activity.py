"""Integration tests for admin activity landing screen."""

import hashlib
import os
import time

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from ulid import ULID

from src.api.admin.deps import get_db
from src.api.main import app
from src.core.db import generate_id

pytestmark = pytest.mark.integration

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


async def test_activity_landing_returns_200(client):
    response = await client.get("/admin/activity/", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert "<h1>Activity</h1>" in response.text
    assert "Import History" in response.text
    assert 'href="/admin/activity/" aria-current="page"' in response.text


async def test_activity_has_api_requests_card(client):
    """Activity landing surfaces the API Requests card linking to the log (#260)."""
    response = await client.get("/admin/activity/", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert "API Requests" in response.text
    assert 'href="/admin/activity/requests/"' in response.text


async def _insert_key(db, label: str, key_id: str) -> str:
    """Insert an app user and an API key with ``key_id``; return the key id."""
    uid = generate_id()
    raw = "pm_" + os.urandom(8).hex()
    await db.execute(
        "INSERT INTO app_users (id, email) VALUES ($1,$2)", uid, f"{uid.lower()}@test.com"
    )
    await db.execute(
        "INSERT INTO api_keys (id, user_id, label, key_prefix, key_hash) VALUES ($1,$2,$3,$4,$5)",
        key_id,
        uid,
        label,
        raw[:8],
        hashlib.sha256(raw.encode()).hexdigest(),
    )
    return key_id


async def _insert_requests(db, key_id: str | None, n: int) -> None:
    """Insert ``n`` request-log rows for ``key_id`` (NULL = no key) at NOW()."""
    for _ in range(n):
        await db.execute(
            "INSERT INTO api_request_log (api_key_id, method, path, route_group,"
            " status_code, latency_ms) VALUES ($1,'GET','/api/v1/changes','changes',200,1)",
            key_id,
        )


async def _clear_request_window(db) -> None:
    """Delete the card's 24 h window of ``api_request_log`` in ``db``'s transaction."""
    await db.execute("DELETE FROM api_request_log WHERE occurred_at >= NOW() - INTERVAL '24 hours'")


async def _assert_busiest_key_shown(client, db) -> None:
    """Give a fresh key 3 requests and assert the card names it busiest.

    Clears the card's 24 h window first, inside the test's rolled-back
    transaction (the ``client`` override shares ``db``), so rows other tests
    committed can't outrank the test's key and nothing is deleted for real.
    """
    await _clear_request_window(db)
    kid = await _insert_key(db, "Busiest Key", generate_id())
    await _insert_requests(db, kid, 3)
    response = await client.get("/admin/activity/", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert "Busiest Key" in response.text


async def test_activity_landing_shows_busiest_key(client, db):
    """API Requests card surfaces the busiest key of the last 24h (#294)."""
    await _assert_busiest_key_shown(client, db)


@pytest.mark.parametrize("background", ["older_key_tie", "keyless_majority"])
async def test_busiest_key_ignores_background_rows(client, db, background):
    """Rows another test committed in the window don't decide the card (#813).

    The request-log middleware writes on its own committed connection, so the
    shared test DB can already hold rows in the 24 h window. At a tie the
    query's ``ORDER BY request_count DESC, r.api_key_id`` picks the older
    ULID; keyless (NULL) rows from unauthenticated requests win on count.
    """
    if background == "older_key_tie":
        older = str(ULID.from_timestamp(time.time() - 3600))
        await _insert_requests(db, await _insert_key(db, "Background Key", older), 3)
    else:
        await _insert_requests(db, None, 4)
    await _assert_busiest_key_shown(client, db)


async def test_activity_landing_redirects_unauthenticated(client):
    response = await client.get("/admin/activity/", follow_redirects=False)
    assert response.status_code in (302, 307)
    assert "/__exe.dev/login" in response.headers["location"]


async def test_activity_sidebar_link_renders(client):
    """Activity section-link present in sidebar with correct class and aria-current."""
    response = await client.get("/admin/activity/", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert 'class="admin-sidebar__section-link" href="/admin/activity/"' in response.text
    assert 'aria-current="page"' in response.text


async def test_activity_sidebar_link_below_settings(client):
    """Activity section-link appears after Settings in the sidebar."""
    response = await client.get("/admin/activity/", headers=AUTH_HEADERS)
    assert response.status_code == 200
    settings_pos = response.text.index('href="/admin/settings/"')
    activity_pos = response.text.index('href="/admin/activity/"')
    assert activity_pos > settings_pos
