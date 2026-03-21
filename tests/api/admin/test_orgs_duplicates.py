"""Integration tests for org duplicate detection and merge routes."""
import asyncio
import os

import asyncpg
import pytest
from fastapi.testclient import TestClient

from src.api.main import app
from src.core.db import apply_schema, generate_id

pytestmark = pytest.mark.integration

AUTH_HEADERS = {
    "X-ExeDev-UserID": "usr_test",
    "X-ExeDev-Email": "admin@test.com",
}


def _get_dsn() -> str:
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        pytest.skip("DATABASE_URL not set")
    return dsn


async def _aconnect(dsn: str) -> asyncpg.Connection:
    conn = await asyncpg.connect(dsn)
    await apply_schema(conn)
    return conn


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture
def org_pair():
    """Insert two near-duplicate orgs (id_a < id_b), yield (id_a, id_b), teardown."""
    dsn = _get_dsn()
    id_a, id_b = generate_id(), generate_id()
    if id_a > id_b:
        id_a, id_b = id_b, id_a

    async def setup():
        conn = await _aconnect(dsn)
        try:
            for oid, name in [
                (id_a, "Alberta Gaming, Liquor and Cannabis Commission"),
                (id_b, "Alberta Gaming, Liquor, and Cannabis Commission"),
            ]:
                await conn.execute("INSERT INTO organizations (id) VALUES ($1)", oid)
                await conn.execute(
                    "INSERT INTO organization_names"
                    " (id, organization_id, name, is_canonical)"
                    " VALUES ($1, $2, $3, TRUE)",
                    generate_id(), oid, name,
                )
        finally:
            await conn.close()

    async def teardown():
        conn = await asyncpg.connect(dsn)
        try:
            await conn.execute(
                "DELETE FROM duplicate_dismissals"
                " WHERE entity_a_id=$1 OR entity_b_id=$1"
                " OR entity_a_id=$2 OR entity_b_id=$2",
                id_a, id_b,
            )
            for oid in [id_a, id_b]:
                await conn.execute(
                    "DELETE FROM organization_names WHERE organization_id=$1", oid
                )
                await conn.execute(
                    "DELETE FROM organizations WHERE id=$1", oid
                )
        finally:
            await conn.close()

    asyncio.run(setup())
    yield id_a, id_b
    asyncio.run(teardown())


def test_duplicates_list_returns_200(client, org_pair):
    response = client.get("/admin/orgs/duplicates/", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert "duplicate" in response.text.lower()


def test_duplicates_list_shows_pair(client, org_pair):
    response = client.get("/admin/orgs/duplicates/", headers=AUTH_HEADERS)
    assert "Alberta Gaming" in response.text


def test_orgs_list_shows_duplicate_banner(client, org_pair):
    response = client.get("/admin/orgs/", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert "possible duplicate" in response.text.lower()


def test_merge_hard_deletes_loser(client, org_pair):
    id_a, id_b = org_pair
    response = client.post(
        f"/admin/orgs/{id_a}/merge/{id_b}/",
        headers=AUTH_HEADERS,
        follow_redirects=False,
    )
    assert response.status_code in (302, 303)

    dsn = _get_dsn()

    async def check_deleted():
        conn = await asyncpg.connect(dsn)
        try:
            return await conn.fetchrow(
                "SELECT id FROM organizations WHERE id=$1", id_b
            )
        finally:
            await conn.close()

    assert asyncio.run(check_deleted()) is None


def test_dismiss_pair_removes_from_list(client, org_pair):
    id_a, id_b = org_pair
    response = client.post(
        f"/admin/orgs/{id_a}/dismiss-duplicate/{id_b}/",
        headers=AUTH_HEADERS,
        follow_redirects=False,
    )
    assert response.status_code in (302, 303)
    response2 = client.get("/admin/orgs/duplicates/", headers=AUTH_HEADERS)
    assert response2.status_code == 200
    # The dismissed pair should no longer appear as a candidate
    assert "Alberta Gaming, Liquor and Cannabis Commission" not in response2.text \
        or "Alberta Gaming, Liquor, and Cannabis Commission" not in response2.text
