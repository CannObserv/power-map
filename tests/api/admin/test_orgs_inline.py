# tests/api/admin/test_orgs_inline.py
"""Integration tests for org inline editing (parent field)."""

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient

from src.api.main import app
from src.core.db import generate_id

pytestmark = [
    pytest.mark.integration,
]
AUTH_HEADERS = {"X-ExeDev-UserID": "usr_test", "X-ExeDev-Email": "admin@test.com"}
HTMX_HEADERS = {**AUTH_HEADERS, "HX-Request": "true"}


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


@pytest_asyncio.fixture(loop_scope="session")
async def org_id(db_pool):
    oid = generate_id()

    async with db_pool.acquire() as conn:
        await conn.execute("INSERT INTO organizations (id) VALUES ($1)", oid)
        await conn.execute(
            "INSERT INTO organization_names (id, organization_id, name, is_canonical)"
            " VALUES ($1, $2, 'Inline Test Org', TRUE)",
            generate_id(),
            oid,
        )

    yield oid

    async with db_pool.acquire() as conn:
        await conn.execute("DELETE FROM organization_acronyms WHERE organization_id=$1", oid)
        await conn.execute("DELETE FROM organization_names WHERE organization_id=$1", oid)
        await conn.execute("DELETE FROM organizations WHERE id=$1", oid)


async def test_parent_get_returns_partial(client, org_id):
    r = client.get(f"/admin/orgs/{org_id}/inline/parent/", headers=HTMX_HEADERS)
    assert r.status_code == 200


async def test_parent_post_sets_parent(client, org_id, db_pool):
    # Create a second org to be the parent
    parent_id = generate_id()

    async with db_pool.acquire() as conn:
        await conn.execute("INSERT INTO organizations (id) VALUES ($1)", parent_id)

    try:
        r = client.post(
            f"/admin/orgs/{org_id}/inline/parent/",
            headers=HTMX_HEADERS,
            data={"parent_id": parent_id},
            follow_redirects=False,
        )
        assert r.status_code == 200
    finally:
        # Clear parent before dropping
        client.post(
            f"/admin/orgs/{org_id}/inline/parent/",
            headers=HTMX_HEADERS,
            data={"parent_id": ""},
        )
        async with db_pool.acquire() as conn:
            await conn.execute("DELETE FROM organizations WHERE id=$1", parent_id)


async def test_parent_post_circular_returns_422(client, org_id):
    r = client.post(
        f"/admin/orgs/{org_id}/inline/parent/",
        headers=HTMX_HEADERS,
        data={"parent_id": org_id},  # self-reference
        follow_redirects=False,
    )
    assert r.status_code == 422
