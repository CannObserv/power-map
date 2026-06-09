# tests/api/admin/test_orgs_search.py
"""Integration tests for org search typeahead endpoint."""

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient

from src.api.main import app
from src.core.db import generate_id

pytestmark = [
    pytest.mark.integration,
]

AUTH_HEADERS = {"X-ExeDev-UserID": "usr_test", "X-ExeDev-Email": "admin@test.com"}


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


@pytest_asyncio.fixture(loop_scope="session")
async def search_org_id(db_pool):
    oid = generate_id()

    async with db_pool.acquire() as conn:
        await conn.execute("INSERT INTO organizations (id) VALUES ($1)", oid)
        await conn.execute(
            "INSERT INTO organization_names (id, organization_id, name, is_canonical)"
            " VALUES ($1, $2, 'Searchable Org', TRUE)",
            generate_id(),
            oid,
        )

    yield oid

    async with db_pool.acquire() as conn:
        await conn.execute("DELETE FROM organization_names WHERE organization_id=$1", oid)
        await conn.execute("DELETE FROM organizations WHERE id=$1", oid)


async def test_search_returns_matching_org(client, search_org_id):
    response = client.get("/admin/orgs/search/?q=Searchable", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert "Searchable Org" in response.text


async def test_search_excludes_archived_org(client, db_pool):
    oid = generate_id()

    async with db_pool.acquire() as conn:
        await conn.execute("INSERT INTO organizations (id, archived_at) VALUES ($1, NOW())", oid)
        await conn.execute(
            "INSERT INTO organization_names (id, organization_id, name, is_canonical)"
            " VALUES ($1, $2, 'Archived Searchable', TRUE)",
            generate_id(),
            oid,
        )

    try:
        response = client.get("/admin/orgs/search/?q=Archived+Searchable", headers=AUTH_HEADERS)
        assert response.status_code == 200
        assert "Archived Searchable" not in response.text
    finally:
        async with db_pool.acquire() as conn:
            await conn.execute("DELETE FROM organization_names WHERE organization_id=$1", oid)
            await conn.execute("DELETE FROM organizations WHERE id=$1", oid)


async def test_search_empty_query_returns_empty(client):
    response = client.get("/admin/orgs/search/?q=", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert "<li" not in response.text
