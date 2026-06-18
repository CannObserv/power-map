"""Integration tests for admin entities landing screen."""

import pytest
from fastapi.testclient import TestClient

from src.api.main import app

pytestmark = pytest.mark.integration

AUTH_HEADERS = {
    "X-ExeDev-UserID": "usr_test",
    "X-ExeDev-Email": "admin@test.com",
}


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


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


def test_entities_landing_has_org_dup_badge_slot(client):
    """Org dup badge loaded async; page must contain the HTMX slot, not an inline count."""
    response = client.get("/admin/entities/", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert 'hx-get="/admin/_dup-badge/orgs/?variant=card"' in response.text
    assert 'hx-swap="innerHTML"' in response.text
    assert "org_dup_count" not in response.text


def test_entities_landing_has_person_dup_badge_slot(client):
    """Person dup badge loaded async; page must contain the HTMX slot, not an inline count."""
    response = client.get("/admin/entities/", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert 'hx-get="/admin/_dup-badge/people/?variant=card"' in response.text
    assert 'hx-swap="innerHTML"' in response.text
    assert "person_dup_count" not in response.text


# --- Sidebar section-link ---


def test_entities_sidebar_link_renders(client):
    """Entities section-link is present in the sidebar with correct class."""
    response = client.get("/admin/entities/", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert 'class="admin-sidebar__section-link" href="/admin/entities/"' in response.text
