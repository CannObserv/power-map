"""Integration tests for list view UI: pagination placement and per-page size."""

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


# --- Pagination placement ---


async def test_orgs_list_has_sticky_pagination(client):
    """pagination--sticky class must appear in orgs list HTML."""
    response = await client.get("/admin/orgs/", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert "pagination--sticky" in response.text


async def test_people_list_has_sticky_pagination(client):
    response = await client.get("/admin/people/", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert "pagination--sticky" in response.text


async def test_roles_list_has_sticky_pagination(client):
    response = await client.get("/admin/roles/", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert "pagination--sticky" in response.text


async def test_ra_list_has_sticky_pagination(client):
    response = await client.get("/admin/role-assignments/", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert "pagination--sticky" in response.text


# --- Per-page size selector ---


async def test_orgs_list_has_page_size_select(client):
    """orgs list filter bar must include a page_size select."""
    response = await client.get("/admin/orgs/", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert 'name="page_size"' in response.text


async def test_people_list_has_page_size_select(client):
    response = await client.get("/admin/people/", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert 'name="page_size"' in response.text


async def test_roles_list_has_page_size_select(client):
    response = await client.get("/admin/roles/", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert 'name="page_size"' in response.text


async def test_ra_list_has_page_size_select(client):
    response = await client.get("/admin/role-assignments/", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert 'name="page_size"' in response.text


# --- page_size URL param respected ---


async def test_orgs_list_accepts_page_size_param(client):
    """page_size=25 in URL must be reflected in the selected option."""
    response = await client.get("/admin/orgs/?page_size=25", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert 'name="page_size"' in response.text
    # Template renders: value="25"  selected (two spaces — Jinja2 template format)
    assert 'value="25"  selected' in response.text
