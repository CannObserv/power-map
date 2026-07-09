"""Integration tests for admin entities landing screen."""

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from src.api.admin.deps import get_db
from src.api.main import app
from tests.api.admin.conftest import ENTITY_ORDER_HREFS, assert_render_order

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


# --- Landing page ---


async def test_entities_landing_returns_200(client):
    response = await client.get("/admin/entities/", headers=AUTH_HEADERS)
    assert response.status_code == 200
    for label in ("People", "Organizations", "Roles", "Assignments"):
        assert label in response.text
    # Entities section-link specifically carries aria-current (not just any sidebar link)
    assert 'href="/admin/entities/" aria-current="page"' in response.text


async def test_entities_landing_redirects_unauthenticated(client):
    response = await client.get("/admin/entities/", follow_redirects=False)
    assert response.status_code in (302, 307)
    assert "/__exe.dev/login" in response.headers["location"]


async def test_entities_landing_has_org_dup_badge_slot(client):
    """Org dup badge loaded async; page must contain the HTMX slot, not an inline count."""
    response = await client.get("/admin/entities/", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert 'hx-get="/admin/_dup-badge/orgs/?variant=card"' in response.text
    assert 'hx-swap="innerHTML"' in response.text
    assert "org_dup_count" not in response.text


async def test_entities_landing_has_person_dup_badge_slot(client):
    """Person dup badge loaded async; page must contain the HTMX slot, not an inline count."""
    response = await client.get("/admin/entities/", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert 'hx-get="/admin/_dup-badge/people/?variant=card"' in response.text
    assert 'hx-swap="innerHTML"' in response.text
    assert "person_dup_count" not in response.text


# --- Sidebar section-link ---


async def test_entities_sidebar_link_renders(client):
    """Entities section-link is present in the sidebar with correct class."""
    response = await client.get("/admin/entities/", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert 'class="admin-sidebar__section-link" href="/admin/entities/"' in response.text


# --- Jurisdictions card ---


async def test_entities_landing_has_jurisdictions_card(client):
    """Entities landing shows a Jurisdictions card linking to the list."""
    response = await client.get("/admin/entities/", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert "Jurisdictions" in response.text
    assert 'href="/admin/jurisdictions/"' in response.text


async def test_entities_landing_cards_jurisdiction_first(client):
    """Entities landing cards are ordered Jurisdiction, Org, Person, Role,
    Assignment (#275) — the focused mirror of the dashboard's Entities section
    must not drift from it."""
    response = await client.get("/admin/entities/", headers=AUTH_HEADERS)
    assert response.status_code == 200
    content = response.text.split('id="main-content"')[1]
    assert_render_order(content, ENTITY_ORDER_HREFS)
