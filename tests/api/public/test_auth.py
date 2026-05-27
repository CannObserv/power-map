"""Sync auth tests for all public API endpoints.

No asyncio pytestmark — these tests are all synchronous and use only sync
fixtures (client, unit_client). Kept separate from async test modules so the
module-level loop_scope="session" mark doesn't attach to non-async functions.
"""

import pytest

# ---------------------------------------------------------------------------
# /api/v1/ (root)
# ---------------------------------------------------------------------------


def test_api_root_missing_key_returns_403(unit_client):
    """403 is raised before the DB is touched — no integration fixture needed."""
    response = unit_client.get("/api/v1/")
    assert response.status_code == 403


@pytest.mark.integration
def test_api_root_invalid_key_returns_401(client):
    response = client.get("/api/v1/", headers={"X-API-Key": "pm_notavalidkey"})
    assert response.status_code == 401


# ---------------------------------------------------------------------------
# /api/v1/orgs/
# ---------------------------------------------------------------------------


def test_orgs_search_missing_key_returns_403(unit_client):
    r = unit_client.get("/api/v1/orgs/search?q=test")
    assert r.status_code == 403


@pytest.mark.integration
def test_orgs_search_invalid_key_returns_401(client):
    r = client.get("/api/v1/orgs/search?q=test", headers={"X-API-Key": "pm_bad"})
    assert r.status_code == 401


def test_get_org_missing_key_returns_403(unit_client):
    r = unit_client.get("/api/v1/orgs/someid")
    assert r.status_code == 403


@pytest.mark.integration
def test_get_org_invalid_key_returns_401(client):
    r = client.get("/api/v1/orgs/someid", headers={"X-API-Key": "pm_bad"})
    assert r.status_code == 401


# ---------------------------------------------------------------------------
# /api/v1/people/
# ---------------------------------------------------------------------------


def test_people_search_missing_key_returns_403(unit_client):
    r = unit_client.get("/api/v1/people/search?q=test")
    assert r.status_code == 403


@pytest.mark.integration
def test_people_search_invalid_key_returns_401(client):
    r = client.get("/api/v1/people/search?q=test", headers={"X-API-Key": "pm_bad"})
    assert r.status_code == 401


def test_get_person_missing_key_returns_403(unit_client):
    r = unit_client.get("/api/v1/people/someid")
    assert r.status_code == 403


@pytest.mark.integration
def test_get_person_invalid_key_returns_401(client):
    r = client.get("/api/v1/people/someid", headers={"X-API-Key": "pm_bad"})
    assert r.status_code == 401
