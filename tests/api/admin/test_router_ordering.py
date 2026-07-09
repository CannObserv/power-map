"""Non-integration tests that guard against router inclusion-order regressions.

FastAPI matches routes in registration order. Routers that share a prefix and
contain wildcard paths (e.g. /{org_id}/) must be registered *after* routers
with literal paths (e.g. /duplicates/). These tests verify the literal routes
win, without requiring a real database.
"""

from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from src.api.admin.deps import get_db
from src.api.admin.org_dups import get_org_dup_count
from src.api.admin.people_dups import get_person_dup_count
from src.api.main import app
from tests.api.admin.conftest import AUTH_HEADERS


async def _mock_get_db():
    """Yield a minimal async mock DB that returns empty results for fetch()."""
    mock_db = AsyncMock()
    mock_db.fetch.return_value = []
    yield mock_db


async def _zero():
    return 0


@pytest.fixture
def client():
    # No `with` → no lifespan → no app pool created. get_db is fully mocked, so
    # the app never touches a real connection. Avoids the ~170 ms pool
    # create/introspect and the real-DB dependency this file's docstring already
    # disclaims (#288).
    app.dependency_overrides[get_db] = _mock_get_db
    app.dependency_overrides[get_org_dup_count] = _zero
    app.dependency_overrides[get_person_dup_count] = _zero
    yield TestClient(app, raise_server_exceptions=True)
    for dep in (get_db, get_org_dup_count, get_person_dup_count):
        app.dependency_overrides.pop(dep, None)


def test_orgs_duplicates_route_resolves(client):
    """/admin/orgs/duplicates/ must match the merge router, not /{org_id}/."""
    response = client.get("/admin/orgs/duplicates/", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert "Duplicate Organizations" in response.text


def test_people_duplicates_route_resolves(client):
    """/admin/people/duplicates/ must match the merge router, not /{person_id}/."""
    response = client.get("/admin/people/duplicates/", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert "Duplicate People" in response.text
