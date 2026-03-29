"""Tests for admin auth dependency."""

import asyncio
import json
from unittest.mock import MagicMock

import pytest
from fastapi.responses import RedirectResponse
from fastapi.testclient import TestClient

from src.api.admin.deps import AdminUser, check_auth, flash_trigger, get_admin_user, is_htmx
from src.api.main import app
from tests.api.admin.conftest import AUTH_HEADERS

_client = TestClient(app, raise_server_exceptions=False)


def test_admin_root_redirects_when_unauthenticated():
    """GET /admin/ without exe.dev headers must redirect to login."""
    response = _client.get("/admin/", follow_redirects=False)
    assert response.status_code in (302, 307)
    assert "/__exe.dev/login" in response.headers["location"]


def test_admin_root_redirects_when_only_user_id_present():
    response = _client.get(
        "/admin/", headers={"X-ExeDev-UserID": "usr123"}, follow_redirects=False
    )
    assert response.status_code in (302, 307)
    assert "/__exe.dev/login" in response.headers["location"]


def test_admin_root_redirects_when_only_email_present():
    response = _client.get(
        "/admin/", headers={"X-ExeDev-Email": "a@b.com"}, follow_redirects=False
    )
    assert response.status_code in (302, 307)
    assert "/__exe.dev/login" in response.headers["location"]


def test_admin_user_dataclass():
    user = AdminUser(id="usr123", email="a@b.com")
    assert user.id == "usr123"
    assert user.email == "a@b.com"


def test_get_admin_user_returns_user_when_headers_present():
    """get_admin_user must extract headers into AdminUser."""
    request = MagicMock()
    request.headers = {
        "X-ExeDev-UserID": "usr_abc",
        "X-ExeDev-Email": "test@example.com",
    }
    request.url.path = "/admin/"

    result = asyncio.run(get_admin_user(request))
    assert isinstance(result, AdminUser)
    assert result.id == "usr_abc"
    assert result.email == "test@example.com"


def test_admin_root_redirect_preserves_query_string():
    response = _client.get("/admin/?page=2&q=foo", follow_redirects=False)
    assert response.status_code in (302, 307)
    location = response.headers["location"]
    assert "redirect=/admin/%3Fpage%3D2%26q%3Dfoo" in location


def test_check_auth_passes_through_user():
    user = AdminUser(id="u1", email="a@b.com")
    redirect, out = check_auth(user)
    assert redirect is None
    assert out is user


def test_check_auth_returns_redirect():
    r = RedirectResponse("/__exe.dev/login")
    redirect, out = check_auth(r)
    assert redirect is r
    assert out is None


def test_is_htmx_returns_true_for_htmx_non_boosted_request():
    request = MagicMock()
    request.headers = {"HX-Request": "true"}
    assert is_htmx(request) is True


def test_is_htmx_returns_false_for_non_htmx_request():
    request = MagicMock()
    request.headers = {}
    assert is_htmx(request) is False


def test_is_htmx_returns_false_for_boosted_request():
    """HX-Boosted is set on sidebar navigation; must return False so full page is rendered."""
    request = MagicMock()
    request.headers = {"HX-Request": "true", "HX-Boosted": "true"}
    assert is_htmx(request) is False


def test_flash_trigger_returns_hx_trigger_header():
    headers = flash_trigger("success", "Saved.")
    assert "HX-Trigger" in headers


def test_flash_trigger_value_is_valid_json():
    headers = flash_trigger("info", "Done.")
    json.loads(headers["HX-Trigger"])  # must not raise


def test_flash_trigger_payload_shape():
    headers = flash_trigger("error", "<strong>Oops</strong>")
    payload = json.loads(headers["HX-Trigger"])
    assert "showFlash" in payload
    assert payload["showFlash"]["level"] == "error"
    assert payload["showFlash"]["body"] == "<strong>Oops</strong>"


def test_flash_trigger_returns_only_hx_trigger_key():
    """Helper must return exactly one header key — callers spread it into TemplateResponse."""
    headers = flash_trigger("warning", "Watch out.")
    assert list(headers.keys()) == ["HX-Trigger"]


def test_flash_trigger_extra_merges_into_payload():
    """extra dict keys must be merged alongside showFlash in the HX-Trigger JSON."""
    headers = flash_trigger(
        "success", "Saved.", extra={"updateOrgHeader": {"display": "Acme Corp"}}
    )
    payload = json.loads(headers["HX-Trigger"])
    assert "showFlash" in payload
    assert "updateOrgHeader" in payload
    assert payload["updateOrgHeader"]["display"] == "Acme Corp"


def test_flash_trigger_extra_none_leaves_payload_unchanged():
    """Passing extra=None must produce the same output as omitting extra."""
    headers = flash_trigger("info", "Done.", extra=None)
    payload = json.loads(headers["HX-Trigger"])
    assert list(payload.keys()) == ["showFlash"]


def test_flash_trigger_extra_multiple_keys():
    """extra may contain multiple keys; all must appear in the payload."""
    headers = flash_trigger("info", "x", extra={"a": 1, "b": 2})
    payload = json.loads(headers["HX-Trigger"])
    assert payload["a"] == 1
    assert payload["b"] == 2


@pytest.mark.integration
def test_admin_dashboard_returns_200_when_authenticated():
    with TestClient(app) as client:
        response = client.get("/admin/", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert "Power Map" in response.text
