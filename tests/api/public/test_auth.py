"""Auth tests for all public API endpoints.

Keyless (403) cases use the sync ``unit_client`` (mocked ``get_db``, never
touches the DB) and stay synchronous. Invalid-key (401) cases hit the DB via the
lifespan-less rollback ``client`` (#288) and are ``async`` — they ``await`` their
requests.
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
async def test_api_root_invalid_key_returns_401(client):
    response = await client.get("/api/v1/", headers={"X-API-Key": "pm_notavalidkey"})
    assert response.status_code == 401


# ---------------------------------------------------------------------------
# /api/v1/orgs/
# ---------------------------------------------------------------------------


def test_orgs_search_missing_key_returns_403(unit_client):
    r = unit_client.get("/api/v1/orgs/search?q=test")
    assert r.status_code == 403


@pytest.mark.integration
async def test_orgs_search_invalid_key_returns_401(client):
    r = await client.get("/api/v1/orgs/search?q=test", headers={"X-API-Key": "pm_bad"})
    assert r.status_code == 401


def test_get_org_missing_key_returns_403(unit_client):
    r = unit_client.get("/api/v1/orgs/someid")
    assert r.status_code == 403


@pytest.mark.integration
async def test_get_org_invalid_key_returns_401(client):
    r = await client.get("/api/v1/orgs/someid", headers={"X-API-Key": "pm_bad"})
    assert r.status_code == 401


# ---------------------------------------------------------------------------
# /api/v1/people/
# ---------------------------------------------------------------------------


def test_people_search_missing_key_returns_403(unit_client):
    r = unit_client.get("/api/v1/people/search?q=test")
    assert r.status_code == 403


@pytest.mark.integration
async def test_people_search_invalid_key_returns_401(client):
    r = await client.get("/api/v1/people/search?q=test", headers={"X-API-Key": "pm_bad"})
    assert r.status_code == 401


def test_get_person_missing_key_returns_403(unit_client):
    r = unit_client.get("/api/v1/people/someid")
    assert r.status_code == 403


@pytest.mark.integration
async def test_get_person_invalid_key_returns_401(client):
    r = await client.get("/api/v1/people/someid", headers={"X-API-Key": "pm_bad"})
    assert r.status_code == 401


# ---------------------------------------------------------------------------
# /api/v1/changes
# ---------------------------------------------------------------------------


def test_changes_missing_key_returns_403(unit_client):
    r = unit_client.get("/api/v1/changes")
    assert r.status_code == 403


@pytest.mark.integration
async def test_changes_invalid_key_returns_401(client):
    r = await client.get("/api/v1/changes", headers={"X-API-Key": "pm_bad"})
    assert r.status_code == 401


# ---------------------------------------------------------------------------
# /api/v1/jurisdictions
# ---------------------------------------------------------------------------


def test_list_jurisdictions_missing_key_returns_403(unit_client):
    r = unit_client.get("/api/v1/jurisdictions")
    assert r.status_code == 403


@pytest.mark.integration
async def test_list_jurisdictions_invalid_key_returns_401(client):
    r = await client.get("/api/v1/jurisdictions", headers={"X-API-Key": "pm_bad"})
    assert r.status_code == 401


def test_get_jurisdiction_missing_key_returns_403(unit_client):
    r = unit_client.get("/api/v1/jurisdictions/some-id")
    assert r.status_code == 403


@pytest.mark.integration
async def test_get_jurisdiction_invalid_key_returns_401(client):
    r = await client.get("/api/v1/jurisdictions/some-id", headers={"X-API-Key": "pm_bad"})
    assert r.status_code == 401


def test_resolve_jurisdiction_missing_key_returns_403(unit_client):
    r = unit_client.get("/api/v1/jurisdictions/resolve?slug=usa-wa")
    assert r.status_code == 403


def test_jurisdiction_relationships_missing_key_returns_403(unit_client):
    r = unit_client.get("/api/v1/jurisdictions/some-id/relationships")
    assert r.status_code == 403


def test_jurisdiction_lineage_missing_key_returns_403(unit_client):
    r = unit_client.get("/api/v1/jurisdictions/some-id/lineage")
    assert r.status_code == 403


# /api/v1/jurisdictions/observations


def test_jurisdiction_observations_missing_key_returns_403(unit_client):
    r = unit_client.post("/api/v1/jurisdictions/observations", json={})
    assert r.status_code == 403


@pytest.mark.integration
async def test_jurisdiction_observations_invalid_key_returns_401(client):
    r = await client.post(
        "/api/v1/jurisdictions/observations",
        json={},
        headers={"X-API-Key": "pm_bad"},
    )
    assert r.status_code == 401
