"""Integration tests for admin import history views."""

import pytest
from fastapi.testclient import TestClient

from src.api.main import app
from src.core.db import generate_id

pytestmark = pytest.mark.integration

AUTH_HEADERS = {
    "X-ExeDev-UserID": "usr_test",
    "X-ExeDev-Email": "admin@test.com",
}


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def test_imports_list_returns_200(client):
    response = client.get("/admin/imports/", headers=AUTH_HEADERS)
    assert response.status_code == 200


def test_imports_list_redirects_unauthenticated(client):
    response = client.get("/admin/imports/", follow_redirects=False)
    assert response.status_code in (302, 307)
    assert "/__exe.dev/login" in response.headers["location"]


def test_import_detail_404_for_unknown(client):
    response = client.get(f"/admin/imports/{generate_id()}/", headers=AUTH_HEADERS)
    assert response.status_code == 404
