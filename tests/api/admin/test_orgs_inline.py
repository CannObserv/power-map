# tests/api/admin/test_orgs_inline.py
"""Integration tests for org inline editing (parent field)."""

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
HTMX_HEADERS = {**AUTH_HEADERS, "HX-Request": "true"}


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
async def org_id(db):
    oid = generate_id()
    await db.execute("INSERT INTO organizations (id) VALUES ($1)", oid)
    await db.execute(
        "INSERT INTO organization_names (id, organization_id, name, is_canonical)"
        " VALUES ($1, $2, 'Inline Test Org', TRUE)",
        generate_id(),
        oid,
    )
    return oid


async def test_parent_get_returns_partial(client, org_id):
    r = await client.get(f"/admin/orgs/{org_id}/inline/parent/", headers=HTMX_HEADERS)
    assert r.status_code == 200


async def test_parent_post_sets_parent(client, org_id, db):
    # Create a second org to be the parent
    parent_id = generate_id()
    await db.execute("INSERT INTO organizations (id) VALUES ($1)", parent_id)

    r = await client.post(
        f"/admin/orgs/{org_id}/inline/parent/",
        headers=HTMX_HEADERS,
        data={"parent_id": parent_id},
        follow_redirects=False,
    )
    assert r.status_code == 200


async def test_parent_post_circular_returns_422(client, org_id):
    r = await client.post(
        f"/admin/orgs/{org_id}/inline/parent/",
        headers=HTMX_HEADERS,
        data={"parent_id": org_id},  # self-reference
        follow_redirects=False,
    )
    assert r.status_code == 422
