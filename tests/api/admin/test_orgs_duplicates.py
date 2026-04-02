"""Integration tests for org duplicate detection and merge routes."""
import asyncio
import json
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
        and "Alberta Gaming, Liquor, and Cannabis Commission" not in response2.text


HTMX_HEADERS = {**AUTH_HEADERS, "HX-Request": "true"}


def test_merge_htmx_returns_200_with_region(client, org_pair):
    """HTMX merge returns 200 partial, not a redirect."""
    id_a, id_b = org_pair
    response = client.post(
        f"/admin/orgs/{id_a}/merge/{id_b}/",
        headers=HTMX_HEADERS,
        follow_redirects=False,
    )
    assert response.status_code == 200
    # Region content present
    assert "candidate" in response.text or "No duplicate" in response.text


def test_merge_htmx_sends_hx_trigger_flash(client, org_pair):
    """HTMX merge delivers flash via HX-Trigger header, not OOB in response body."""
    id_a, id_b = org_pair
    response = client.post(
        f"/admin/orgs/{id_a}/merge/{id_b}/",
        headers=HTMX_HEADERS,
        follow_redirects=False,
    )
    assert response.status_code == 200
    assert "HX-Trigger" in response.headers
    payload = json.loads(response.headers["HX-Trigger"])
    assert payload["showFlash"]["level"] == "success"
    assert "Alberta Gaming" in payload["showFlash"]["body"]  # winner name in flash body
    assert f"/admin/orgs/{id_a}/" in payload["showFlash"]["body"]  # link to winner
    assert "hx-swap-oob" not in response.text


def test_dismiss_htmx_returns_200_with_region(client, org_pair):
    """HTMX dismiss returns 200 partial, not a redirect."""
    id_a, id_b = org_pair
    response = client.post(
        f"/admin/orgs/{id_a}/dismiss-duplicate/{id_b}/",
        headers=HTMX_HEADERS,
        follow_redirects=False,
    )
    assert response.status_code == 200
    assert "candidate" in response.text or "No duplicate" in response.text


def test_merge_reassigns_loser_dismissals_to_winner(client, org_pair):
    """After merge, loser's dismissals with third orgs transfer to winner with correct ordering."""
    id_a, id_b = org_pair  # id_a < id_b; merge id_b (loser) into id_a (winner)
    dsn = _get_dsn()
    id_c = generate_id()

    async def setup_third():
        conn = await _aconnect(dsn)
        try:
            await conn.execute("INSERT INTO organizations (id) VALUES ($1)", id_c)
            await conn.execute(
                "INSERT INTO organization_names (id, organization_id, name, is_canonical)"
                " VALUES ($1, $2, 'Third Org For Dismissal Test', TRUE)",
                generate_id(), id_c,
            )
            a, b = (id_b, id_c) if id_b < id_c else (id_c, id_b)
            await conn.execute(
                "INSERT INTO duplicate_dismissals"
                " (id, entity_type, entity_a_id, entity_b_id, dismissed_by)"
                " VALUES ($1, 'organization', $2, $3, 'test@test.com')",
                generate_id(), a, b,
            )
        finally:
            await conn.close()

    async def teardown_third():
        conn = await asyncpg.connect(dsn)
        try:
            await conn.execute(
                "DELETE FROM duplicate_dismissals"
                " WHERE entity_a_id=$1 OR entity_b_id=$1",
                id_c,
            )
            await conn.execute(
                "DELETE FROM organization_names WHERE organization_id=$1", id_c
            )
            await conn.execute("DELETE FROM organizations WHERE id=$1", id_c)
        finally:
            await conn.close()

    asyncio.run(setup_third())
    try:
        client.post(
            f"/admin/orgs/{id_a}/merge/{id_b}/",
            headers=AUTH_HEADERS,
            follow_redirects=False,
        )

        async def check():
            conn = await asyncpg.connect(dsn)
            try:
                a, b = (id_a, id_c) if id_a < id_c else (id_c, id_a)
                return await conn.fetchrow(
                    "SELECT id FROM duplicate_dismissals"
                    " WHERE entity_type='organization'"
                    " AND entity_a_id=$1 AND entity_b_id=$2",
                    a, b,
                )
            finally:
                await conn.close()

        assert asyncio.run(check()) is not None
    finally:
        asyncio.run(teardown_third())


def test_dismiss_htmx_sends_hx_trigger_flash(client, org_pair):
    """HTMX dismiss delivers flash via HX-Trigger header, not OOB in response body."""
    id_a, id_b = org_pair
    response = client.post(
        f"/admin/orgs/{id_a}/dismiss-duplicate/{id_b}/",
        headers=HTMX_HEADERS,
        follow_redirects=False,
    )
    assert response.status_code == 200
    assert "HX-Trigger" in response.headers
    payload = json.loads(response.headers["HX-Trigger"])
    assert payload["showFlash"]["level"] == "info"
    assert "hx-swap-oob" not in response.text
