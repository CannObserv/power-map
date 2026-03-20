"""Tests for admin base template: header branding and footer."""

import pytest
from fastapi.testclient import TestClient

from src.api.main import app
from tests.api.admin.conftest import AUTH_HEADERS

pytestmark = pytest.mark.integration


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def test_header_brand_text_is_power_map(client):
    """Brand in topbar must read 'Power Map'."""
    response = client.get("/admin/", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert "Power Map" in response.text


def test_header_brand_icon_present(client):
    """Brand icon (cannabis_observer-icon-square.svg) must appear in topbar."""
    response = client.get("/admin/", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert "admin-topbar__brand-icon" in response.text
    assert "cannabis_observer-icon-square.svg" in response.text


def test_footer_credits_cannabis_observer(client):
    """Footer must contain the 'A project of … Cannabis Observer' credit text."""
    response = client.get("/admin/", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert "admin-footer" in response.text
    assert "A project of" in response.text
    assert "Cannabis Observer" in response.text


def test_footer_links_to_cannabis_observer_site(client):
    """Footer 'Cannabis Observer' link must point to https://cannabis.observer/."""
    response = client.get("/admin/", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert "https://cannabis.observer/" in response.text


def test_footer_icon_present(client):
    """Footer must include a cannabis observer icon image."""
    response = client.get("/admin/", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert "admin-footer__icon" in response.text


def test_footer_emoji_present(client):
    """Footer must include the 🌱🏛️🔍 emoji string."""
    response = client.get("/admin/", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert "🌱🏛️🔍" in response.text
