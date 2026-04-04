"""Integration tests for admin activity landing screen."""

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


def test_activity_landing_returns_200(client):
    response = client.get("/admin/activity/", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert "<h1>Activity</h1>" in response.text
    assert "Import History" in response.text
    assert 'href="/admin/activity/" aria-current="page"' in response.text


def test_activity_landing_redirects_unauthenticated(client):
    response = client.get("/admin/activity/", follow_redirects=False)
    assert response.status_code in (302, 307)
    assert "/__exe.dev/login" in response.headers["location"]


def test_activity_sidebar_link_renders(client):
    """Activity section-link present in sidebar with correct class and aria-current."""
    response = client.get("/admin/activity/", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert 'class="admin-sidebar__section-link" href="/admin/activity/"' in response.text
    assert 'aria-current="page"' in response.text


def test_activity_sidebar_link_below_settings(client):
    """Activity section-link appears after Settings in the sidebar."""
    response = client.get("/admin/activity/", headers=AUTH_HEADERS)
    assert response.status_code == 200
    settings_pos = response.text.index('href="/admin/settings/"')
    activity_pos = response.text.index('href="/admin/activity/"')
    assert activity_pos > settings_pos
