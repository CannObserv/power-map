"""Integration tests for admin entities landing screen."""

import pytest
from fastapi.testclient import TestClient

from src.api.main import app
from src.api.admin.org_dups import get_org_dup_count

pytestmark = pytest.mark.integration

AUTH_HEADERS = {
    "X-ExeDev-UserID": "usr_test",
    "X-ExeDev-Email": "admin@test.com",
}


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture
def client_no_dups():
    """Client with org_dup_count forced to 0 via dependency override."""
    app.dependency_overrides[get_org_dup_count] = lambda: 0
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
def client_with_dups():
    """Client with org_dup_count forced to 3 via dependency override."""
    app.dependency_overrides[get_org_dup_count] = lambda: 3
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


# --- Landing page ---


def test_entities_landing_returns_200(client):
    response = client.get("/admin/entities/", headers=AUTH_HEADERS)
    assert response.status_code == 200
    for label in ("People", "Organizations", "Roles", "Assignments"):
        assert label in response.text
    # Entities section-link specifically carries aria-current (not just any sidebar link)
    assert 'href="/admin/entities/" aria-current="page"' in response.text


def test_entities_landing_redirects_unauthenticated(client):
    response = client.get("/admin/entities/", follow_redirects=False)
    assert response.status_code in (302, 307)
    assert "/__exe.dev/login" in response.headers["location"]


def test_entities_landing_dup_link_hidden_when_zero(client_no_dups):
    """Duplicates link not rendered in card area when org_dup_count is 0."""
    response = client_no_dups.get("/admin/entities/", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert "duplicate →" not in response.text


def test_entities_landing_dup_link_shown_when_nonzero(client_with_dups):
    """Duplicates link rendered with count when org_dup_count > 0."""
    response = client_with_dups.get("/admin/entities/", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert "/admin/orgs/duplicates/" in response.text
    assert "3 duplicate" in response.text


# --- Sidebar section-link ---


def test_entities_sidebar_link_renders(client):
    """Entities section-link is present in the sidebar with correct class."""
    response = client.get("/admin/entities/", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert 'class="admin-sidebar__section-link" href="/admin/entities/"' in response.text
