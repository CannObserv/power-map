"""Tests for admin auth dependency."""

from fastapi.testclient import TestClient

from src.api.main import app

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
