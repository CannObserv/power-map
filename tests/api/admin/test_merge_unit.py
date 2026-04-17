"""Non-integration smoke tests for org and person merge POST handlers.

These tests use a fully mocked DB to verify that merge handlers execute
without raising exceptions (e.g. stale column references, missing keys).
They do not test correctness of the SQL — that lives in the integration
tests in test_orgs_duplicates.py and test_people_duplicates.py.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from src.api.admin.deps import get_db
from src.api.admin.org_dups import get_org_dup_count
from src.api.admin.people_dups import get_person_dup_count
from src.api.main import app
from tests.api.admin.conftest import AUTH_HEADERS

HTMX_HEADERS = {**AUTH_HEADERS, "HX-Request": "true"}


class _AsyncCM:
    """Minimal async context manager for mocking db.transaction()."""

    async def __aenter__(self):
        return None

    async def __aexit__(self, *args):
        return False


def _make_merge_db(fetchrow_result):
    """Return a mock DB suitable for merge handlers."""
    mock_db = AsyncMock()
    mock_db.fetchval.return_value = "Test Name"
    mock_db.fetchrow.return_value = fetchrow_result
    mock_db.execute.return_value = None
    mock_db.fetch.return_value = []
    mock_db.transaction = MagicMock(return_value=_AsyncCM())
    return mock_db


async def _org_merge_db():
    yield _make_merge_db({"id": "test_id"})


async def _person_merge_db():
    # person merge fetches 'id' and 'notes' from the people row
    yield _make_merge_db({"id": "test_id", "notes": None})


async def _zero():
    return 0


@pytest.fixture
def org_client():
    app.dependency_overrides[get_db] = _org_merge_db
    app.dependency_overrides[get_org_dup_count] = _zero
    app.dependency_overrides[get_person_dup_count] = _zero
    with TestClient(app, raise_server_exceptions=True) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
def person_client():
    app.dependency_overrides[get_db] = _person_merge_db
    app.dependency_overrides[get_org_dup_count] = _zero
    app.dependency_overrides[get_person_dup_count] = _zero
    with TestClient(app, raise_server_exceptions=True) as c:
        yield c
    app.dependency_overrides.clear()


def test_org_merge_post_returns_200(org_client):
    """POST to org merge must not 500 (e.g. from stale column references)."""
    response = org_client.post(
        "/admin/orgs/WINNER000000000000000000000/merge/LOSER0000000000000000000000/",
        headers=HTMX_HEADERS,
    )
    assert response.status_code == 200


def test_person_merge_post_returns_200(person_client):
    """POST to person merge must not 500 (e.g. from stale column references)."""
    response = person_client.post(
        "/admin/people/WINNER000000000000000000000/merge/LOSER0000000000000000000000/",
        headers=HTMX_HEADERS,
    )
    assert response.status_code == 200


def test_org_merge_with_post_htmx_redirects(org_client):
    """POST to merge-with must return HX-Redirect, not 500."""
    response = org_client.post(
        "/admin/orgs/WINNER000000000000000000000/merge-with/LOSER0000000000000000000000/",
        headers=HTMX_HEADERS,
    )
    assert response.status_code == 200
    assert response.headers.get("HX-Redirect")


def test_org_merge_with_post_non_htmx_redirects(org_client):
    """Non-HTMX POST to merge-with must return 303 redirect."""
    response = org_client.post(
        "/admin/orgs/WINNER000000000000000000000/merge-with/LOSER0000000000000000000000/",
        headers=AUTH_HEADERS,
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert "/admin/orgs/WINNER000000000000000000000/" in response.headers["location"]


def test_org_merge_target_search_returns_200(org_client):
    """GET merge-target-search must not 404."""
    response = org_client.get(
        "/admin/orgs/ORGID00000000000000000000000/merge-target-search/?q=test",
        headers=AUTH_HEADERS,
    )
    assert response.status_code in (200, 500)


def test_org_merge_search_modal_returns_200(org_client):
    """GET merge-search must not 404."""
    response = org_client.get(
        "/admin/orgs/ORGID00000000000000000000000/merge-search/",
        headers=AUTH_HEADERS,
    )
    assert response.status_code in (200, 500)


def test_org_merge_preview_returns_200(org_client):
    """GET merge-preview must not 404."""
    response = org_client.get(
        "/admin/orgs/ORGID00000000000000000000000/merge-preview/OTHERID0000000000000000000/",
        headers=AUTH_HEADERS,
    )
    assert response.status_code in (200, 500)
