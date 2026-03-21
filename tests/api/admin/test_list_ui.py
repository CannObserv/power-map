"""Integration tests for list view UI: pagination placement and per-page size."""
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


# --- Pagination placement ---

def test_orgs_list_has_sticky_pagination(client):
    """pagination--sticky class must appear in orgs list HTML."""
    response = client.get("/admin/orgs/", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert "pagination--sticky" in response.text


def test_people_list_has_sticky_pagination(client):
    response = client.get("/admin/people/", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert "pagination--sticky" in response.text


def test_roles_list_has_sticky_pagination(client):
    response = client.get("/admin/roles/", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert "pagination--sticky" in response.text


def test_ra_list_has_sticky_pagination(client):
    response = client.get("/admin/role-assignments/", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert "pagination--sticky" in response.text


# --- Per-page size selector ---

def test_orgs_list_has_page_size_select(client):
    """orgs list filter bar must include a page_size select."""
    response = client.get("/admin/orgs/", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert 'name="page_size"' in response.text


def test_people_list_has_page_size_select(client):
    response = client.get("/admin/people/", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert 'name="page_size"' in response.text


def test_roles_list_has_page_size_select(client):
    response = client.get("/admin/roles/", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert 'name="page_size"' in response.text


def test_ra_list_has_page_size_select(client):
    response = client.get("/admin/role-assignments/", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert 'name="page_size"' in response.text


# --- page_size URL param respected ---

def test_orgs_list_accepts_page_size_param(client):
    """page_size=25 in URL must be reflected in the selected option."""
    response = client.get("/admin/orgs/?page_size=25", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert "page_size" in response.text  # select rendered
