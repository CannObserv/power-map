# tests/api/admin/test_orgs_search.py
"""Integration tests for org search typeahead endpoint."""

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from src.api.admin.deps import get_db
from src.api.main import app
from src.core.db import generate_id

pytestmark = [
    pytest.mark.integration,
]

AUTH_HEADERS = {"X-ExeDev-UserID": "usr_test", "X-ExeDev-Email": "admin@test.com"}


@pytest_asyncio.fixture(loop_scope="session")
async def db(db_pool):
    """Pool-acquired connection wrapped in a rolled-back transaction."""
    async with db_pool.acquire() as conn:
        tr = conn.transaction()
        await tr.start()
        try:
            yield conn
        finally:
            await tr.rollback()


@pytest_asyncio.fixture(loop_scope="session")
async def client(db):
    """AsyncClient with app, overriding get_db to use the test connection."""

    async def _get_db_override():
        yield db

    app.dependency_overrides[get_db] = _get_db_override
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test", follow_redirects=True
    ) as c:
        yield c
    app.dependency_overrides.pop(get_db, None)


@pytest_asyncio.fixture(loop_scope="session")
async def search_org_id(db):
    oid = generate_id()
    await db.execute("INSERT INTO organizations (id) VALUES ($1)", oid)
    await db.execute(
        "INSERT INTO organization_names (id, organization_id, name, is_canonical)"
        " VALUES ($1, $2, 'Searchable Org', TRUE)",
        generate_id(),
        oid,
    )
    return oid


async def test_search_returns_matching_org(client, search_org_id):
    response = await client.get("/admin/orgs/search/?q=Searchable", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert "Searchable Org" in response.text


async def test_search_excludes_archived_org(client, db):
    oid = generate_id()
    await db.execute("INSERT INTO organizations (id, archived_at) VALUES ($1, NOW())", oid)
    await db.execute(
        "INSERT INTO organization_names (id, organization_id, name, is_canonical)"
        " VALUES ($1, $2, 'Archived Searchable', TRUE)",
        generate_id(),
        oid,
    )

    response = await client.get("/admin/orgs/search/?q=Archived+Searchable", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert "Archived Searchable" not in response.text


async def test_search_empty_query_returns_empty(client):
    response = await client.get("/admin/orgs/search/?q=", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert "<li" not in response.text
