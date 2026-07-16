"""Integration tests for admin activity landing screen."""

import hashlib
import os

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

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


async def test_activity_landing_shows_busiest_key(client, db):
    """API Requests card surfaces the busiest key of the last 24h (#294)."""
    uid, kid = generate_id(), generate_id()
    raw = "pm_" + os.urandom(8).hex()
    await db.execute("INSERT INTO app_users (id, email) VALUES ($1,$2)", uid, "busy@test.com")
    await db.execute(
        "INSERT INTO api_keys (id, user_id, label, key_prefix, key_hash) VALUES ($1,$2,$3,$4,$5)",
        kid,
        uid,
        "Busiest Key",
        raw[:8],
        hashlib.sha256(raw.encode()).hexdigest(),
    )
    for _ in range(3):
        await db.execute(
            "INSERT INTO api_request_log (api_key_id, method, path, route_group,"
            " status_code, latency_ms) VALUES ($1,'GET','/api/v1/changes','changes',200,1)",
            kid,
        )
    response = await client.get("/admin/activity/", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert "Busiest Key" in response.text


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
