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
    """Footer must include the 🌱🏛️🔍 emoji wrapped in aria-hidden span."""
    response = client.get("/admin/", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert "🌱🏛️🔍" in response.text
    assert 'aria-hidden="true">🌱🏛️🔍' in response.text


def test_fouc_prevention_script_in_base_template(client):
    """FOUC script must appear in base.html to prevent flash on load."""
    response = client.get("/admin/", headers=AUTH_HEADERS)
    assert "pm-color-scheme" in response.text


def test_dark_mode_toggle_button_present(client):
    """Theme toggle button must be in the topbar with the three-state (#25) ARIA
    label. The static default matches the absent/system state; dark-mode.js
    re-syncs the label to the stored preference on load."""
    response = client.get("/admin/", headers=AUTH_HEADERS)
    assert "theme-toggle" in response.text
    assert "Color theme: System. Activate for Dark." in response.text


def test_dark_mode_js_loaded_with_defer(client):
    """dark-mode.js must be loaded with defer to avoid blocking render."""
    response = client.get("/admin/", headers=AUTH_HEADERS)
    assert "dark-mode.js" in response.text
    # Check defer appears on the same script tag line
    text = response.text
    idx = text.find("dark-mode.js")
    assert "defer" in text[max(0, idx - 100) : idx + 100]
