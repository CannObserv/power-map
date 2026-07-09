"""Integration tests for admin activity landing screen."""

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from src.api.admin.deps import get_db
from src.api.main import app

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
