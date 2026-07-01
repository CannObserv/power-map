"""Integration tests for role merge on org detail page."""

import json

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient

from src.api.main import app
from src.core.db import generate_id

pytestmark = [
    pytest.mark.integration,
]

AUTH_HEADERS = {
    "X-ExeDev-UserID": "usr_test",
    "X-ExeDev-Email": "admin@test.com",
}
HTMX_HEADERS = {**AUTH_HEADERS, "HX-Request": "true"}


# ── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def client():
    """TestClient with default raise_server_exceptions=True for integration tests."""
    with TestClient(app) as c:
        yield c


@pytest_asyncio.fixture(loop_scope="session")
async def role_pair(db_pool):
    """Two roles on the same org, yield (org_id, role_a, role_b), teardown."""
    org_id = generate_id()
    role_a = generate_id()
    role_b = generate_id()

    async with db_pool.acquire() as conn:
        await conn.execute("INSERT INTO organizations (id) VALUES ($1)", org_id)
        await conn.execute(
            "INSERT INTO organization_names (id, organization_id, name, is_canonical)"
            " VALUES ($1, $2, 'Test Org', TRUE)",
            generate_id(),
            org_id,
        )
        for rid, title in [(role_a, "Director"), (role_b, "Exec Director")]:
            await conn.execute(
                "INSERT INTO roles (id, organization_id, title) VALUES ($1, $2, $3)",
                rid,
                org_id,
                title,
            )

    yield org_id, role_a, role_b

    async with db_pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM role_assignments WHERE role_id = ANY($1::text[])",
            [role_a, role_b],
        )
        await conn.execute("DELETE FROM roles WHERE organization_id = $1", org_id)
        await conn.execute(
            "DELETE FROM organization_names WHERE organization_id = $1",
            org_id,
        )
        await conn.execute("DELETE FROM organizations WHERE id = $1", org_id)


@pytest_asyncio.fixture(loop_scope="session")
async def role_pair_with_assignments(db_pool):
    """Two roles with overlapping assignments (same person+start_date conflict)."""
    org_id = generate_id()
    role_a = generate_id()
    role_b = generate_id()
    person_id = generate_id()
    unique_person_id = generate_id()

    async with db_pool.acquire() as conn:
        await conn.execute("INSERT INTO organizations (id) VALUES ($1)", org_id)
        await conn.execute(
            "INSERT INTO organization_names (id, organization_id, name, is_canonical)"
            " VALUES ($1, $2, 'Test Org', TRUE)",
            generate_id(),
            org_id,
        )
        for rid, title in [(role_a, "Director"), (role_b, "Exec Director")]:
            await conn.execute(
                "INSERT INTO roles (id, organization_id, title) VALUES ($1, $2, $3)",
                rid,
                org_id,
                title,
            )
        for pid in [person_id, unique_person_id]:
            await conn.execute("INSERT INTO people (id) VALUES ($1)", pid)
            await conn.execute(
                "INSERT INTO person_names (id, person_id, name, is_canonical)"
                " VALUES ($1, $2, 'Test Person', TRUE)",
                generate_id(),
                pid,
            )
        # Shared person assigned to both roles with same start_date (conflict)
        await conn.execute(
            "INSERT INTO role_assignments (id, person_id, role_id, start_date)"
            " VALUES ($1, $2, $3, '2024-01-01')",
            generate_id(),
            person_id,
            role_a,
        )
        await conn.execute(
            "INSERT INTO role_assignments (id, person_id, role_id, start_date)"
            " VALUES ($1, $2, $3, '2024-01-01')",
            generate_id(),
            person_id,
            role_b,
        )
        # Unique person only on role_b (should be reassigned)
        await conn.execute(
            "INSERT INTO role_assignments (id, person_id, role_id, start_date)"
            " VALUES ($1, $2, $3, '2024-06-01')",
            generate_id(),
            unique_person_id,
            role_b,
        )

    yield org_id, role_a, role_b, person_id, unique_person_id

    async with db_pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM role_assignments WHERE role_id = ANY($1::text[])",
            [role_a, role_b],
        )
        await conn.execute("DELETE FROM roles WHERE organization_id = $1", org_id)
        for pid in [person_id, unique_person_id]:
            await conn.execute("DELETE FROM person_names WHERE person_id = $1", pid)
            await conn.execute("DELETE FROM people WHERE id = $1", pid)
        await conn.execute(
            "DELETE FROM organization_names WHERE organization_id = $1",
            org_id,
        )
        await conn.execute("DELETE FROM organizations WHERE id = $1", org_id)


@pytest_asyncio.fixture(loop_scope="session")
async def role_pair_with_notes(db_pool):
    """Two roles where both have notes."""
    org_id = generate_id()
    role_a = generate_id()
    role_b = generate_id()

    async with db_pool.acquire() as conn:
        await conn.execute("INSERT INTO organizations (id) VALUES ($1)", org_id)
        await conn.execute(
            "INSERT INTO organization_names (id, organization_id, name, is_canonical)"
            " VALUES ($1, $2, 'Test Org', TRUE)",
            generate_id(),
            org_id,
        )
        await conn.execute(
            "INSERT INTO roles (id, organization_id, title, notes)"
            " VALUES ($1, $2, 'Director', 'Winner notes')",
            role_a,
            org_id,
        )
        await conn.execute(
            "INSERT INTO roles (id, organization_id, title, notes)"
            " VALUES ($1, $2, 'Exec Director', 'Loser notes')",
            role_b,
            org_id,
        )

    yield org_id, role_a, role_b

    async with db_pool.acquire() as conn:
        await conn.execute("DELETE FROM roles WHERE organization_id = $1", org_id)
        await conn.execute(
            "DELETE FROM organization_names WHERE organization_id = $1",
            org_id,
        )
        await conn.execute("DELETE FROM organizations WHERE id = $1", org_id)


# ── Merge: hard delete ──────────────────────────────────────────────────────


async def test_merge_hard_deletes_loser(client, role_pair, db_pool):
    org_id, role_a, role_b = role_pair
    response = client.post(
        f"/admin/orgs/{org_id}/roles/{role_a}/merge/{role_b}/",
        headers=AUTH_HEADERS,
        follow_redirects=False,
    )
    assert response.status_code == 303

    async with db_pool.acquire() as conn:
        row = await conn.fetchrow("SELECT id FROM roles WHERE id=$1", role_b)
    assert row is None


# ── Merge preview modal (#255) ───────────────────────────────────────────────


async def test_merge_preview_returns_modal_with_titles_and_form(client, role_pair):
    """GET role merge-preview returns the modal: both titles and a form posting back
    to the existing /merge/ endpoint, targeting the roles list region (#255)."""
    org_id, role_a, role_b = role_pair
    response = client.get(
        f"/admin/orgs/{org_id}/roles/{role_a}/merge-preview/{role_b}/?ctx=list",
        headers=AUTH_HEADERS,
    )
    assert response.status_code == 200
    assert "Director" in response.text  # winner title
    assert "Exec Director" in response.text  # loser title
    assert f"/admin/orgs/{org_id}/roles/{role_a}/merge/{role_b}/" in response.text
    assert "roles-list-region" in response.text


async def test_merge_preview_counts_reassigned_and_conflicts(client, role_pair_with_assignments):
    """Preview surfaces how many assignments reassign vs. drop as conflicts (#255)."""
    org_id, role_a, role_b, _person, _unique = role_pair_with_assignments
    # role_a (winner) has the shared person; role_b (loser) has shared (conflict) + unique.
    response = client.get(
        f"/admin/orgs/{org_id}/roles/{role_a}/merge-preview/{role_b}/?ctx=list",
        headers=AUTH_HEADERS,
    )
    assert response.status_code == 200
    assert "1 role assignment" in response.text  # the unique person reassigns
    assert "1 duplicate" in response.text  # the shared person+date is dropped


async def test_merge_preview_409_for_cross_org(client, role_pair, db_pool):
    """Preview enforces the same-org rule like the merge route itself."""
    org_id, role_a, role_b = role_pair
    other_org = generate_id()
    other_role = generate_id()
    async with db_pool.acquire() as conn:
        await conn.execute("INSERT INTO organizations (id) VALUES ($1)", other_org)
        await conn.execute(
            "INSERT INTO roles (id, organization_id, title) VALUES ($1, $2, 'Other')",
            other_role,
            other_org,
        )
    try:
        response = client.get(
            f"/admin/orgs/{org_id}/roles/{role_a}/merge-preview/{other_role}/?ctx=list",
            headers=AUTH_HEADERS,
        )
        assert response.status_code == 409
    finally:
        async with db_pool.acquire() as conn:
            await conn.execute("DELETE FROM roles WHERE id=$1", other_role)
            await conn.execute("DELETE FROM organizations WHERE id=$1", other_org)


# ── Merge: role_assignments ─────────────────────────────────────────────────


async def test_merge_deletes_conflicting_assignments(client, role_pair_with_assignments, db_pool):
    org_id, role_a, role_b, person_id, _ = role_pair_with_assignments
    client.post(
        f"/admin/orgs/{org_id}/roles/{role_a}/merge/{role_b}/",
        headers=AUTH_HEADERS,
        follow_redirects=False,
    )

    async with db_pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id FROM role_assignments WHERE person_id=$1 AND role_id=$2",
            person_id,
            role_a,
        )
    assert len(rows) == 1  # only winner's original assignment remains


async def test_merge_reassigns_unique_assignments(client, role_pair_with_assignments, db_pool):
    org_id, role_a, role_b, _, unique_person_id = role_pair_with_assignments
    client.post(
        f"/admin/orgs/{org_id}/roles/{role_a}/merge/{role_b}/",
        headers=AUTH_HEADERS,
        follow_redirects=False,
    )

    async with db_pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id FROM role_assignments WHERE person_id=$1 AND role_id=$2",
            unique_person_id,
            role_a,
        )
    assert row is not None


# ── Merge: notes ────────────────────────────────────────────────────────────


async def test_merge_appends_loser_notes_to_winner(client, role_pair_with_notes, db_pool):
    org_id, role_a, role_b = role_pair_with_notes
    client.post(
        f"/admin/orgs/{org_id}/roles/{role_a}/merge/{role_b}/",
        headers=AUTH_HEADERS,
        follow_redirects=False,
    )

    async with db_pool.acquire() as conn:
        notes = await conn.fetchval("SELECT notes FROM roles WHERE id=$1", role_a)
    assert "Winner notes" in notes
    assert "Loser notes" in notes
    assert "Merged from" in notes


async def test_merge_skips_notes_when_loser_has_none(client, role_pair, db_pool):
    org_id, role_a, role_b = role_pair
    client.post(
        f"/admin/orgs/{org_id}/roles/{role_a}/merge/{role_b}/",
        headers=AUTH_HEADERS,
        follow_redirects=False,
    )

    async with db_pool.acquire() as conn:
        notes = await conn.fetchval("SELECT notes FROM roles WHERE id=$1", role_a)
    assert notes is None


# ── Merge: provenance survives ──────────────────────────────────────────────


async def test_merge_preserves_assignment_provenance(client, role_pair_with_assignments, db_pool):
    """Provenance tracks assignment IDs, not role IDs — merge must not break them."""
    org_id, role_a, role_b, _, unique_person_id = role_pair_with_assignments

    # Get the assignment ID that will be reassigned (unique_person on role_b)
    async with db_pool.acquire() as conn:
        assignment_id = await conn.fetchval(
            "SELECT id FROM role_assignments WHERE person_id=$1 AND role_id=$2",
            unique_person_id,
            role_b,
        )

    client.post(
        f"/admin/orgs/{org_id}/roles/{role_a}/merge/{role_b}/",
        headers=AUTH_HEADERS,
        follow_redirects=False,
    )

    # Assignment still exists with same ID, now pointing to winner role
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id, role_id FROM role_assignments WHERE id=$1",
            assignment_id,
        )
    assert row is not None
    assert row["role_id"] == role_a


# ── Merge: guards ───────────────────────────────────────────────────────────


async def test_merge_returns_404_for_unknown_role(client, role_pair):
    org_id, role_a, _ = role_pair
    response = client.post(
        f"/admin/orgs/{org_id}/roles/{role_a}/merge/nonexistent-id/",
        headers=AUTH_HEADERS,
        follow_redirects=False,
    )
    assert response.status_code == 404


async def test_merge_returns_409_for_archived_role(client, role_pair, db_pool):
    org_id, role_a, role_b = role_pair

    async with db_pool.acquire() as conn:
        await conn.execute(
            "UPDATE roles SET archived_at = NOW() WHERE id = $1",
            role_b,
        )

    response = client.post(
        f"/admin/orgs/{org_id}/roles/{role_a}/merge/{role_b}/",
        headers=AUTH_HEADERS,
        follow_redirects=False,
    )
    assert response.status_code == 409


async def test_merge_returns_409_for_cross_org_roles(client, role_pair, db_pool):
    org_id, role_a, role_b = role_pair

    other_org_id = generate_id()
    other_role_id = generate_id()

    async with db_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO organizations (id) VALUES ($1)",
            other_org_id,
        )
        await conn.execute(
            "INSERT INTO organization_names (id, organization_id, name, is_canonical)"
            " VALUES ($1, $2, 'Other Org', TRUE)",
            generate_id(),
            other_org_id,
        )
        await conn.execute(
            "INSERT INTO roles (id, organization_id, title) VALUES ($1, $2, 'Other Role')",
            other_role_id,
            other_org_id,
        )

    response = client.post(
        f"/admin/orgs/{org_id}/roles/{role_a}/merge/{other_role_id}/",
        headers=AUTH_HEADERS,
        follow_redirects=False,
    )
    assert response.status_code == 409

    # cleanup
    async with db_pool.acquire() as conn:
        await conn.execute("DELETE FROM roles WHERE id=$1", other_role_id)
        await conn.execute(
            "DELETE FROM organization_names WHERE organization_id=$1",
            other_org_id,
        )
        await conn.execute("DELETE FROM organizations WHERE id=$1", other_org_id)


# ── Merge: HTMX response ───────────────────────────────────────────────────


async def test_merge_htmx_returns_200_with_tbody(client, role_pair):
    org_id, role_a, role_b = role_pair
    response = client.post(
        f"/admin/orgs/{org_id}/roles/{role_a}/merge/{role_b}/",
        headers=HTMX_HEADERS,
        follow_redirects=False,
    )
    assert response.status_code == 200
    # Should contain the remaining role in tbody
    assert "Director" in response.text


async def test_merge_htmx_sends_flash_trigger(client, role_pair):
    org_id, role_a, role_b = role_pair
    response = client.post(
        f"/admin/orgs/{org_id}/roles/{role_a}/merge/{role_b}/",
        headers=HTMX_HEADERS,
        follow_redirects=False,
    )
    assert response.status_code == 200
    assert "HX-Trigger" in response.headers
    payload = json.loads(response.headers["HX-Trigger"])
    assert payload["showFlash"]["level"] == "success"
    assert "Exec Director" in payload["showFlash"]["body"]


async def test_merge_non_htmx_redirects_to_org_detail(client, role_pair):
    org_id, role_a, role_b = role_pair
    response = client.post(
        f"/admin/orgs/{org_id}/roles/{role_a}/merge/{role_b}/",
        headers=AUTH_HEADERS,
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == f"/admin/orgs/{org_id}/"


# ── List-flow merge (#251): merge initiated from /admin/roles/ ───────────────

LIST_HEADERS = {**HTMX_HEADERS, "HX-Target": "roles-list-region"}


async def test_list_merge_returns_roles_list_region(client, role_pair):
    """HX-Target=roles-list-region → render the roles LIST region, not the
    org-detail roles table partial."""
    org_id, role_a, role_b = role_pair
    response = client.post(
        f"/admin/orgs/{org_id}/roles/{role_a}/merge/{role_b}/",
        headers=LIST_HEADERS,
        follow_redirects=False,
    )
    assert response.status_code == 200
    # List-region markers (caption + namespaced table id + merge bar), absent
    # from the org-detail `_role_rows.html` partial.
    assert 'id="roles-list-table"' in response.text
    assert 'id="roles-list-merge-bar"' in response.text
    assert "Roles —" in response.text
    # Surviving winner linked into the roles list (not the org-detail view).
    assert f'href="/admin/roles/{role_a}/"' in response.text


async def test_list_merge_actually_merges(client, role_pair, db_pool):
    """Loser is hard-deleted regardless of which response branch renders."""
    org_id, role_a, role_b = role_pair
    client.post(
        f"/admin/orgs/{org_id}/roles/{role_a}/merge/{role_b}/",
        headers=LIST_HEADERS,
        follow_redirects=False,
    )
    async with db_pool.acquire() as conn:
        survivor = await conn.fetchval("SELECT count(*) FROM roles WHERE id = $1", role_a)
        gone = await conn.fetchval("SELECT count(*) FROM roles WHERE id = $1", role_b)
    assert survivor == 1
    assert gone == 0


async def test_list_merge_sends_flash_trigger(client, role_pair):
    org_id, role_a, role_b = role_pair
    response = client.post(
        f"/admin/orgs/{org_id}/roles/{role_a}/merge/{role_b}/",
        headers=LIST_HEADERS,
        follow_redirects=False,
    )
    assert response.status_code == 200
    payload = json.loads(response.headers["HX-Trigger"])
    assert payload["showFlash"]["level"] == "success"
    assert "Exec Director" in payload["showFlash"]["body"]


async def test_list_merge_preserves_org_q_filter(client, role_pair):
    """A non-matching org_q (re-derived from HX-Current-URL) filters the survivor
    out of the re-rendered region — proving the filter flowed through."""
    org_id, role_a, role_b = role_pair
    response = client.post(
        f"/admin/orgs/{org_id}/roles/{role_a}/merge/{role_b}/",
        headers={**LIST_HEADERS, "HX-Current-URL": "/admin/roles/?org_q=Zzzznomatch"},
        follow_redirects=False,
    )
    assert response.status_code == 200
    assert "Roles — 0 records" in response.text
    assert "No results" in response.text


async def test_list_merge_honors_matching_org_q_filter(client, role_pair):
    """A matching org_q keeps the survivor in the region."""
    org_id, role_a, role_b = role_pair
    response = client.post(
        f"/admin/orgs/{org_id}/roles/{role_a}/merge/{role_b}/",
        headers={**LIST_HEADERS, "HX-Current-URL": "/admin/roles/?org_q=Test"},
        follow_redirects=False,
    )
    assert response.status_code == 200
    assert f'href="/admin/roles/{role_a}/"' in response.text


async def test_org_detail_merge_unaffected_without_hx_target(client, role_pair):
    """No HX-Target → the org-detail roles-table partial still renders (regression
    guard: the list branch must not steal the detail flow)."""
    org_id, role_a, role_b = role_pair
    response = client.post(
        f"/admin/orgs/{org_id}/roles/{role_a}/merge/{role_b}/",
        headers=HTMX_HEADERS,
        follow_redirects=False,
    )
    assert response.status_code == 200
    assert 'id="roles-list-table"' not in response.text
    assert "Roles —" not in response.text
