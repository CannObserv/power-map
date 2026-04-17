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


def test_duplicates_region_has_no_hx_confirm_on_merge(client, org_pair):
    """Keep A / Keep B buttons must not use hx-confirm (replaced by preview modal)."""
    response = client.get("/admin/orgs/duplicates/", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert "merge-preview" in response.text
    assert 'hx-confirm="Merge' not in response.text


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
    assert response.status_code == 303

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
    assert response.status_code == 303
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


def test_merge_with_hard_deletes_loser(client, org_pair):
    """POST merge-with hard-deletes loser (same transaction as merge)."""
    id_a, id_b = org_pair
    response = client.post(
        f"/admin/orgs/{id_a}/merge-with/{id_b}/",
        headers=AUTH_HEADERS,
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert f"/admin/orgs/{id_a}/" in response.headers["location"]

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


def test_merge_with_htmx_returns_hx_redirect(client, org_pair):
    """HTMX merge-with returns HX-Redirect to winner detail, not an inline region."""
    id_a, id_b = org_pair
    response = client.post(
        f"/admin/orgs/{id_a}/merge-with/{id_b}/",
        headers=HTMX_HEADERS,
        follow_redirects=False,
    )
    assert response.status_code == 200
    assert response.headers.get("HX-Redirect") == f"/admin/orgs/{id_a}/"


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


def test_merge_search_modal_returns_fragment(client, org_pair):
    """GET merge-search returns modal fragment with typeahead input."""
    id_a, _ = org_pair
    response = client.get(
        f"/admin/orgs/{id_a}/merge-search/",
        headers=AUTH_HEADERS,
    )
    assert response.status_code == 200
    assert "merge-target-display" in response.text
    assert "merge-target-results" in response.text


def test_merge_preview_shows_winner_and_loser(client, org_pair):
    """GET merge-preview shows winner and loser org names."""
    id_a, id_b = org_pair
    response = client.get(
        f"/admin/orgs/{id_a}/merge-preview/{id_b}/",
        headers=AUTH_HEADERS,
    )
    assert response.status_code == 200
    assert "Alberta Gaming" in response.text
    assert "Execute merge" in response.text


def test_merge_preview_winner_param_flips_direction(client, org_pair):
    """?winner=id_b makes id_b the winner in the preview."""
    id_a, id_b = org_pair
    response = client.get(
        f"/admin/orgs/{id_a}/merge-preview/{id_b}/?winner={id_b}",
        headers=AUTH_HEADERS,
    )
    assert response.status_code == 200
    assert "Execute merge" in response.text
    assert f"winner={id_b}" in response.text


def test_merge_preview_shows_conflict_warning(client):
    """GET merge-preview shows role conflict warning when title clash exists."""
    dsn = _get_dsn()
    id_a, id_b = generate_id(), generate_id()
    role_a, role_b = generate_id(), generate_id()

    async def setup():
        conn = await _aconnect(dsn)
        try:
            for oid, name in [(id_a, "Conflict Org A"), (id_b, "Conflict Org B")]:
                await conn.execute("INSERT INTO organizations (id) VALUES ($1)", oid)
                await conn.execute(
                    "INSERT INTO organization_names (id, organization_id, name, is_canonical)"
                    " VALUES ($1, $2, $3, TRUE)",
                    generate_id(), oid, name,
                )
            for rid, oid in [(role_a, id_a), (role_b, id_b)]:
                await conn.execute(
                    "INSERT INTO roles (id, organization_id, title) VALUES ($1, $2, 'Director')",
                    rid, oid,
                )
        finally:
            await conn.close()

    async def teardown():
        conn = await asyncpg.connect(dsn)
        try:
            for rid in [role_a, role_b]:
                await conn.execute("DELETE FROM roles WHERE id=$1", rid)
            for oid in [id_a, id_b]:
                await conn.execute("DELETE FROM organization_names WHERE organization_id=$1", oid)
                await conn.execute("DELETE FROM organizations WHERE id=$1", oid)
        finally:
            await conn.close()

    asyncio.run(setup())
    try:
        with TestClient(app) as c:
            response = c.get(
                f"/admin/orgs/{id_a}/merge-preview/{id_b}/",
                headers=AUTH_HEADERS,
            )
        assert response.status_code == 200
        assert "Director" in response.text
        assert "conflict" in response.text.lower()
    finally:
        asyncio.run(teardown())


def test_merge_with_keep_name_ids_transfers_only_checked(client, org_pair):
    """POSTing keep_name_ids transfers only the specified names; others are deleted."""
    dsn = _get_dsn()
    id_a, id_b = org_pair
    former_name_id = generate_id()

    async def setup():
        conn = await asyncpg.connect(dsn)
        try:
            await conn.execute(
                "INSERT INTO organization_names (id, organization_id, name, is_canonical)"
                " VALUES ($1, $2, 'Former Name B', FALSE)",
                former_name_id, id_b,
            )
        finally:
            await conn.close()

    async def teardown():
        conn = await asyncpg.connect(dsn)
        try:
            await conn.execute(
                "DELETE FROM organization_names WHERE id=$1", former_name_id
            )
        finally:
            await conn.close()

    asyncio.run(setup())
    try:
        with TestClient(app) as c:
            response = c.post(
                f"/admin/orgs/{id_a}/merge-with/{id_b}/",
                data={"keep_name_ids": former_name_id},
                headers=AUTH_HEADERS,
                follow_redirects=False,
            )
        assert response.status_code == 303

        async def check():
            conn = await asyncpg.connect(dsn)
            try:
                kept = await conn.fetchval(
                    "SELECT name FROM organization_names WHERE organization_id=$1"
                    " AND name='Former Name B'",
                    id_a,
                )
                return kept
            finally:
                await conn.close()

        assert asyncio.run(check()) == "Former Name B"
    finally:
        asyncio.run(teardown())


def test_merge_with_role_pairs_merges_assignments_and_deduplicates(client):
    """merge_role_pairs reassigns loser role assignments; drops duplicates."""
    dsn = _get_dsn()
    id_a, id_b = generate_id(), generate_id()
    role_a, role_b = generate_id(), generate_id()
    person_id = generate_id()
    assign_a, assign_b = generate_id(), generate_id()

    async def setup():
        conn = await _aconnect(dsn)
        try:
            for oid, name in [(id_a, "Role Merge Org A"), (id_b, "Role Merge Org B")]:
                await conn.execute("INSERT INTO organizations (id) VALUES ($1)", oid)
                await conn.execute(
                    "INSERT INTO organization_names (id, organization_id, name, is_canonical)"
                    " VALUES ($1, $2, $3, TRUE)",
                    generate_id(), oid, name,
                )
            for rid, oid in [(role_a, id_a), (role_b, id_b)]:
                await conn.execute(
                    "INSERT INTO roles (id, organization_id, title) VALUES ($1, $2, 'Director')",
                    rid, oid,
                )
            await conn.execute(
                "INSERT INTO people (id) VALUES ($1)", person_id
            )
            await conn.execute(
                "INSERT INTO person_names (id, person_id, name, is_canonical)"
                " VALUES ($1, $2, 'Test Person', TRUE)",
                generate_id(), person_id,
            )
            # Same person assigned to both roles (same start_date = duplicate)
            for aid, rid in [(assign_a, role_a), (assign_b, role_b)]:
                await conn.execute(
                    "INSERT INTO role_assignments (id, person_id, role_id, start_date)"
                    " VALUES ($1, $2, $3, '2020-01-01')",
                    aid, person_id, rid,
                )
        finally:
            await conn.close()

    async def teardown():
        conn = await asyncpg.connect(dsn)
        try:
            await conn.execute(
                "DELETE FROM role_assignments WHERE person_id=$1", person_id
            )
            for rid in [role_a, role_b]:
                await conn.execute("DELETE FROM roles WHERE id=$1", rid)
            await conn.execute("DELETE FROM person_names WHERE person_id=$1", person_id)
            await conn.execute("DELETE FROM people WHERE id=$1", person_id)
            for oid in [id_a, id_b]:
                await conn.execute("DELETE FROM organization_names WHERE organization_id=$1", oid)
                await conn.execute("DELETE FROM organizations WHERE id=$1", oid)
        finally:
            await conn.close()

    asyncio.run(setup())
    try:
        with TestClient(app) as c:
            response = c.post(
                f"/admin/orgs/{id_a}/merge-with/{id_b}/",
                data={"merge_role_pairs": f"{role_a}:{role_b}"},
                headers=AUTH_HEADERS,
                follow_redirects=False,
            )
        assert response.status_code == 303

        async def check():
            conn = await asyncpg.connect(dsn)
            try:
                # loser org deleted
                loser_exists = await conn.fetchval(
                    "SELECT id FROM organizations WHERE id=$1", id_b
                )
                # loser role deleted
                loser_role_exists = await conn.fetchval(
                    "SELECT id FROM roles WHERE id=$1", role_b
                )
                # exactly one assignment on winner role for the person (deduplicated)
                assignment_count = await conn.fetchval(
                    "SELECT count(*) FROM role_assignments"
                    " WHERE role_id=$1 AND person_id=$2 AND archived_at IS NULL",
                    role_a, person_id,
                )
                return loser_exists, loser_role_exists, assignment_count
            finally:
                await conn.close()

        loser_exists, loser_role_exists, count = asyncio.run(check())
        assert loser_exists is None
        assert loser_role_exists is None
        assert count == 1
    finally:
        asyncio.run(teardown())
