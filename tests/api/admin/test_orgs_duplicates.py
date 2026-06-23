"""Integration tests for org duplicate detection and merge routes."""

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


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


@pytest_asyncio.fixture(loop_scope="session")
async def org_pair(db_pool):
    """Insert two near-duplicate orgs (id_a < id_b), yield (id_a, id_b), teardown."""
    id_a, id_b = generate_id(), generate_id()
    if id_a > id_b:
        id_a, id_b = id_b, id_a

    async with db_pool.acquire() as conn:
        for oid, name in [
            (id_a, "Alberta Gaming, Liquor and Cannabis Commission"),
            (id_b, "Alberta Gaming, Liquor, and Cannabis Commission"),
        ]:
            await conn.execute("INSERT INTO organizations (id) VALUES ($1)", oid)
            await conn.execute(
                "INSERT INTO organization_names"
                " (id, organization_id, name, is_canonical)"
                " VALUES ($1, $2, $3, TRUE)",
                generate_id(),
                oid,
                name,
            )

    yield id_a, id_b

    async with db_pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM duplicate_dismissals"
            " WHERE entity_a_id=$1 OR entity_b_id=$1"
            " OR entity_a_id=$2 OR entity_b_id=$2",
            id_a,
            id_b,
        )
        for oid in [id_a, id_b]:
            await conn.execute("DELETE FROM organization_names WHERE organization_id=$1", oid)
            await conn.execute("DELETE FROM organizations WHERE id=$1", oid)


async def test_duplicates_region_has_no_hx_confirm_on_merge(client, org_pair):
    """Keep A / Keep B buttons must not use hx-confirm (replaced by preview modal)."""
    response = client.get("/admin/orgs/duplicates/", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert "merge-preview" in response.text
    assert 'hx-confirm="Merge' not in response.text


async def test_duplicates_list_returns_200(client, org_pair):
    response = client.get("/admin/orgs/duplicates/", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert "duplicate" in response.text.lower()


async def test_duplicates_list_shows_pair(client, org_pair):
    response = client.get("/admin/orgs/duplicates/", headers=AUTH_HEADERS)
    assert "Alberta Gaming" in response.text


async def test_orgs_list_has_dup_banner_slot(client, org_pair):
    """Orgs list page has the async HTMX slot for the dup banner, not an inline count."""
    response = client.get("/admin/orgs/", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert 'hx-get="/admin/_dup-badge/orgs/?variant=banner"' in response.text
    assert 'hx-swap="innerHTML"' in response.text


async def test_org_merge_htmx_emits_refresh_dup_badge(client, org_pair):
    """HTMX merge response must include refreshDupBadge in HX-Trigger."""
    id_a, id_b = org_pair
    response = client.post(
        f"/admin/orgs/{id_a}/merge/{id_b}/",
        headers=HTMX_HEADERS,
        follow_redirects=False,
    )
    assert response.status_code == 200
    trigger = json.loads(response.headers.get("HX-Trigger", "{}"))
    assert "refreshDupBadge" in trigger


async def test_org_dismiss_htmx_emits_refresh_dup_badge(client, org_pair):
    """HTMX dismiss response must include refreshDupBadge in HX-Trigger."""
    id_a, id_b = org_pair
    response = client.post(
        f"/admin/orgs/{id_a}/dismiss-duplicate/{id_b}/",
        headers=HTMX_HEADERS,
        follow_redirects=False,
    )
    assert response.status_code == 200
    trigger = json.loads(response.headers.get("HX-Trigger", "{}"))
    assert "refreshDupBadge" in trigger


async def test_merge_hard_deletes_loser(client, org_pair, db_pool):
    id_a, id_b = org_pair
    response = client.post(
        f"/admin/orgs/{id_a}/merge/{id_b}/",
        headers=AUTH_HEADERS,
        follow_redirects=False,
    )
    assert response.status_code == 303

    async with db_pool.acquire() as conn:
        row = await conn.fetchrow("SELECT id FROM organizations WHERE id=$1", id_b)
    assert row is None


async def test_dismiss_pair_removes_from_list(client, org_pair):
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
    assert (
        "Alberta Gaming, Liquor and Cannabis Commission" not in response2.text
        and "Alberta Gaming, Liquor, and Cannabis Commission" not in response2.text
    )


async def test_merge_htmx_returns_200_with_region(client, org_pair):
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


async def test_merge_htmx_sends_hx_trigger_flash(client, org_pair):
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


async def test_dismiss_htmx_returns_200_with_region(client, org_pair):
    """HTMX dismiss returns 200 partial, not a redirect."""
    id_a, id_b = org_pair
    response = client.post(
        f"/admin/orgs/{id_a}/dismiss-duplicate/{id_b}/",
        headers=HTMX_HEADERS,
        follow_redirects=False,
    )
    assert response.status_code == 200
    assert "candidate" in response.text or "No duplicate" in response.text


async def test_merge_reassigns_loser_dismissals_to_winner(client, org_pair, db_pool):
    """After merge, loser's dismissals with third orgs transfer to winner with correct ordering."""
    id_a, id_b = org_pair  # id_a < id_b; merge id_b (loser) into id_a (winner)
    id_c = generate_id()

    async with db_pool.acquire() as conn:
        await conn.execute("INSERT INTO organizations (id) VALUES ($1)", id_c)
        await conn.execute(
            "INSERT INTO organization_names (id, organization_id, name, is_canonical)"
            " VALUES ($1, $2, 'Third Org For Dismissal Test', TRUE)",
            generate_id(),
            id_c,
        )
        a, b = (id_b, id_c) if id_b < id_c else (id_c, id_b)
        await conn.execute(
            "INSERT INTO duplicate_dismissals"
            " (id, entity_type, entity_a_id, entity_b_id, dismissed_by)"
            " VALUES ($1, 'organization', $2, $3, 'test@test.com')",
            generate_id(),
            a,
            b,
        )

    try:
        client.post(
            f"/admin/orgs/{id_a}/merge/{id_b}/",
            headers=AUTH_HEADERS,
            follow_redirects=False,
        )

        async with db_pool.acquire() as conn:
            a, b = (id_a, id_c) if id_a < id_c else (id_c, id_a)
            row = await conn.fetchrow(
                "SELECT id FROM duplicate_dismissals"
                " WHERE entity_type='organization'"
                " AND entity_a_id=$1 AND entity_b_id=$2",
                a,
                b,
            )
        assert row is not None
    finally:
        async with db_pool.acquire() as conn:
            await conn.execute(
                "DELETE FROM duplicate_dismissals WHERE entity_a_id=$1 OR entity_b_id=$1",
                id_c,
            )
            await conn.execute("DELETE FROM organization_names WHERE organization_id=$1", id_c)
            await conn.execute("DELETE FROM organizations WHERE id=$1", id_c)


async def test_merge_with_hard_deletes_loser(client, org_pair, db_pool):
    """POST merge-with hard-deletes loser (same transaction as merge)."""
    id_a, id_b = org_pair
    response = client.post(
        f"/admin/orgs/{id_a}/merge-with/{id_b}/",
        headers=AUTH_HEADERS,
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert f"/admin/orgs/{id_a}/" in response.headers["location"]

    async with db_pool.acquire() as conn:
        row = await conn.fetchrow("SELECT id FROM organizations WHERE id=$1", id_b)
    assert row is None


async def test_merge_with_htmx_returns_hx_redirect(client, org_pair):
    """HTMX merge-with returns HX-Redirect to winner detail, not an inline region."""
    id_a, id_b = org_pair
    response = client.post(
        f"/admin/orgs/{id_a}/merge-with/{id_b}/",
        headers=HTMX_HEADERS,
        follow_redirects=False,
    )
    assert response.status_code == 200
    assert response.headers.get("HX-Redirect") == f"/admin/orgs/{id_a}/"


async def test_dismiss_htmx_sends_hx_trigger_flash(client, org_pair):
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


async def test_merge_search_modal_returns_fragment(client, org_pair):
    """GET merge-search returns modal fragment with typeahead input."""
    id_a, _ = org_pair
    response = client.get(
        f"/admin/orgs/{id_a}/merge-search/",
        headers=AUTH_HEADERS,
    )
    assert response.status_code == 200
    assert "merge-target-display" in response.text
    assert "merge-target-results" in response.text


async def test_merge_preview_shows_winner_and_loser(client, org_pair):
    """GET merge-preview shows winner and loser org names."""
    id_a, id_b = org_pair
    response = client.get(
        f"/admin/orgs/{id_a}/merge-preview/{id_b}/",
        headers=AUTH_HEADERS,
    )
    assert response.status_code == 200
    assert "Alberta Gaming" in response.text
    assert "Execute merge" in response.text


async def test_merge_preview_winner_param_flips_direction(client, org_pair):
    """?winner=id_b makes id_b the winner in the preview."""
    id_a, id_b = org_pair
    response = client.get(
        f"/admin/orgs/{id_a}/merge-preview/{id_b}/?winner={id_b}",
        headers=AUTH_HEADERS,
    )
    assert response.status_code == 200
    assert "Execute merge" in response.text
    # The merge form encodes the winner in the action path, not the query string.
    # Form action shape: /admin/orgs/{winner_id}/merge-with/{loser_id}/
    assert f"/admin/orgs/{id_b}/merge-with/{id_a}/" in response.text
    # Sanity: swap button still points back to id_a as winner.
    assert f"?winner={id_a}" in response.text


async def test_merge_preview_shows_conflict_warning(client, db_pool):
    """GET merge-preview shows role conflict warning when title clash exists."""
    id_a, id_b = generate_id(), generate_id()
    role_a, role_b = generate_id(), generate_id()

    async with db_pool.acquire() as conn:
        for oid, name in [(id_a, "Conflict Org A"), (id_b, "Conflict Org B")]:
            await conn.execute("INSERT INTO organizations (id) VALUES ($1)", oid)
            await conn.execute(
                "INSERT INTO organization_names (id, organization_id, name, is_canonical)"
                " VALUES ($1, $2, $3, TRUE)",
                generate_id(),
                oid,
                name,
            )
        for rid, oid in [(role_a, id_a), (role_b, id_b)]:
            await conn.execute(
                "INSERT INTO roles (id, organization_id, title) VALUES ($1, $2, 'Director')",
                rid,
                oid,
            )

    try:
        response = client.get(
            f"/admin/orgs/{id_a}/merge-preview/{id_b}/",
            headers=AUTH_HEADERS,
        )
        assert response.status_code == 200
        assert "Director" in response.text
        assert "conflict" in response.text.lower()
    finally:
        async with db_pool.acquire() as conn:
            for rid in [role_a, role_b]:
                await conn.execute("DELETE FROM roles WHERE id=$1", rid)
            for oid in [id_a, id_b]:
                await conn.execute("DELETE FROM organization_names WHERE organization_id=$1", oid)
                await conn.execute("DELETE FROM organizations WHERE id=$1", oid)


async def test_merge_with_keep_name_ids_transfers_only_checked(client, org_pair, db_pool):
    """POSTing keep_name_ids transfers only the specified names; others are deleted."""
    id_a, id_b = org_pair
    former_name_id = generate_id()

    async with db_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO organization_names (id, organization_id, name, is_canonical)"
            " VALUES ($1, $2, 'Former Name B', FALSE)",
            former_name_id,
            id_b,
        )

    try:
        response = client.post(
            f"/admin/orgs/{id_a}/merge-with/{id_b}/",
            data={"keep_name_ids": former_name_id},
            headers=AUTH_HEADERS,
            follow_redirects=False,
        )
        assert response.status_code == 303

        async with db_pool.acquire() as conn:
            kept = await conn.fetchval(
                "SELECT name FROM organization_names WHERE organization_id=$1"
                " AND name='Former Name B'",
                id_a,
            )
            # id_b's canonical name was NOT in keep_name_ids — must not appear on winner
            unchecked_on_winner = await conn.fetchval(
                "SELECT name FROM organization_names WHERE organization_id=$1"
                " AND name='Alberta Gaming, Liquor, and Cannabis Commission'",
                id_a,
            )
        assert kept == "Former Name B"
        assert unchecked_on_winner is None
    finally:
        async with db_pool.acquire() as conn:
            await conn.execute("DELETE FROM organization_names WHERE id=$1", former_name_id)


async def test_merge_with_role_pairs_merges_assignments_and_deduplicates(client, db_pool):
    """merge_role_pairs reassigns loser role assignments; drops duplicates."""
    id_a, id_b = generate_id(), generate_id()
    role_a, role_b = generate_id(), generate_id()
    person_id = generate_id()
    assign_a, assign_b = generate_id(), generate_id()

    async with db_pool.acquire() as conn:
        for oid, name in [(id_a, "Role Merge Org A"), (id_b, "Role Merge Org B")]:
            await conn.execute("INSERT INTO organizations (id) VALUES ($1)", oid)
            await conn.execute(
                "INSERT INTO organization_names (id, organization_id, name, is_canonical)"
                " VALUES ($1, $2, $3, TRUE)",
                generate_id(),
                oid,
                name,
            )
        for rid, oid in [(role_a, id_a), (role_b, id_b)]:
            await conn.execute(
                "INSERT INTO roles (id, organization_id, title) VALUES ($1, $2, 'Director')",
                rid,
                oid,
            )
        await conn.execute("INSERT INTO people (id) VALUES ($1)", person_id)
        await conn.execute(
            "INSERT INTO person_names (id, person_id, name, is_canonical)"
            " VALUES ($1, $2, 'Test Person', TRUE)",
            generate_id(),
            person_id,
        )
        # Same person assigned to both roles (same start_date = duplicate)
        for aid, rid in [(assign_a, role_a), (assign_b, role_b)]:
            await conn.execute(
                "INSERT INTO role_assignments (id, person_id, role_id, start_date)"
                " VALUES ($1, $2, $3, '2020-01-01')",
                aid,
                person_id,
                rid,
            )

    try:
        response = client.post(
            f"/admin/orgs/{id_a}/merge-with/{id_b}/",
            data={"merge_role_pairs": f"{role_a}:{role_b}"},
            headers=AUTH_HEADERS,
            follow_redirects=False,
        )
        assert response.status_code == 303

        async with db_pool.acquire() as conn:
            # loser org deleted
            loser_exists = await conn.fetchval("SELECT id FROM organizations WHERE id=$1", id_b)
            # loser role deleted
            loser_role_exists = await conn.fetchval("SELECT id FROM roles WHERE id=$1", role_b)
            # exactly one assignment on winner role for the person (deduplicated)
            assignment_count = await conn.fetchval(
                "SELECT count(*) FROM role_assignments"
                " WHERE role_id=$1 AND person_id=$2 AND archived_at IS NULL",
                role_a,
                person_id,
            )
        assert loser_exists is None
        assert loser_role_exists is None
        assert assignment_count == 1
    finally:
        async with db_pool.acquire() as conn:
            await conn.execute("DELETE FROM role_assignments WHERE person_id=$1", person_id)
            for rid in [role_a, role_b]:
                await conn.execute("DELETE FROM roles WHERE id=$1", rid)
            await conn.execute("DELETE FROM person_names WHERE person_id=$1", person_id)
            await conn.execute("DELETE FROM people WHERE id=$1", person_id)
            for oid in [id_a, id_b]:
                await conn.execute("DELETE FROM organization_names WHERE organization_id=$1", oid)
                await conn.execute("DELETE FROM organizations WHERE id=$1", oid)


async def test_merge_with_keep_acronym_ids_transfers_only_checked(client, org_pair, db_pool):
    """keep_acronym_ids transfers checked acronym; unchecked acronym is dropped."""
    id_a, id_b = org_pair
    canonical_id = generate_id()
    non_canonical_id = generate_id()

    async with db_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO organization_acronyms (id, organization_id, acronym, is_canonical)"
            " VALUES ($1, $2, 'AGLCC', TRUE)",
            canonical_id,
            id_b,
        )
        await conn.execute(
            "INSERT INTO organization_acronyms (id, organization_id, acronym, is_canonical)"
            " VALUES ($1, $2, 'AGLCC-OLD', FALSE)",
            non_canonical_id,
            id_b,
        )

    try:
        response = client.post(
            f"/admin/orgs/{id_a}/merge-with/{id_b}/",
            data={"keep_acronym_ids": canonical_id},
            headers=AUTH_HEADERS,
            follow_redirects=False,
        )
        assert response.status_code == 303

        async with db_pool.acquire() as conn:
            transferred = await conn.fetchval(
                "SELECT acronym FROM organization_acronyms"
                " WHERE organization_id=$1 AND acronym='AGLCC'",
                id_a,
            )
            # Check entire table — unchecked acronym must be fully deleted, not orphaned
            dropped_anywhere = await conn.fetchval(
                "SELECT acronym FROM organization_acronyms WHERE acronym='AGLCC-OLD'"
            )
        assert transferred == "AGLCC"
        assert dropped_anywhere is None
    finally:
        async with db_pool.acquire() as conn:
            for aid in [canonical_id, non_canonical_id]:
                await conn.execute("DELETE FROM organization_acronyms WHERE id=$1", aid)


async def test_merge_with_safeguard_handles_conflicts_not_in_submitted_pairs(client, db_pool):
    """Safeguard block auto-resolves title conflicts not submitted in merge_role_pairs."""
    id_a, id_b = generate_id(), generate_id()
    role_a1, role_b1 = generate_id(), generate_id()
    role_a2, role_b2 = generate_id(), generate_id()

    async with db_pool.acquire() as conn:
        for oid, name in [(id_a, "Safeguard Org A"), (id_b, "Safeguard Org B")]:
            await conn.execute("INSERT INTO organizations (id) VALUES ($1)", oid)
            await conn.execute(
                "INSERT INTO organization_names (id, organization_id, name, is_canonical)"
                " VALUES ($1, $2, $3, TRUE)",
                generate_id(),
                oid,
                name,
            )
        for rid, oid, title in [
            (role_a1, id_a, "Director"),
            (role_b1, id_b, "director"),
            (role_a2, id_a, "Manager"),
            (role_b2, id_b, "manager"),
        ]:
            await conn.execute(
                "INSERT INTO roles (id, organization_id, title) VALUES ($1, $2, $3)",
                rid,
                oid,
                title,
            )

    try:
        # Submit only the first pair; safeguard must handle the second
        response = client.post(
            f"/admin/orgs/{id_a}/merge-with/{id_b}/",
            data={"merge_role_pairs": f"{role_a1}:{role_b1}"},
            headers=AUTH_HEADERS,
            follow_redirects=False,
        )
        assert response.status_code == 303

        async with db_pool.acquire() as conn:
            loser_exists = await conn.fetchval("SELECT id FROM organizations WHERE id=$1", id_b)
            rb1_exists = await conn.fetchval("SELECT id FROM roles WHERE id=$1", role_b1)
            rb2_exists = await conn.fetchval("SELECT id FROM roles WHERE id=$1", role_b2)
            ra1_exists = await conn.fetchval("SELECT id FROM roles WHERE id=$1", role_a1)
            ra2_exists = await conn.fetchval("SELECT id FROM roles WHERE id=$1", role_a2)
        assert loser_exists is None
        assert rb1_exists is None  # handled by explicit merge_role_pairs
        assert rb2_exists is None  # handled by safeguard block
        assert ra1_exists is not None
        assert ra2_exists is not None
    finally:
        async with db_pool.acquire() as conn:
            for rid in [role_a1, role_b1, role_a2, role_b2]:
                await conn.execute("DELETE FROM roles WHERE id=$1", rid)
            for oid in [id_a, id_b]:
                await conn.execute("DELETE FROM organization_names WHERE organization_id=$1", oid)
                await conn.execute("DELETE FROM organizations WHERE id=$1", oid)


async def test_merge_with_safeguard_migrates_assignments_from_unsubmitted_pairs(client, db_pool):
    """Safeguard migrates assignments from a conflicting loser role not in merge_role_pairs."""
    id_a, id_b = generate_id(), generate_id()
    role_a1, role_b1 = generate_id(), generate_id()
    role_a2, role_b2 = generate_id(), generate_id()
    person_id = generate_id()
    assign_b2 = generate_id()

    async with db_pool.acquire() as conn:
        for oid, name in [
            (id_a, "Safeguard Assign Org A"),
            (id_b, "Safeguard Assign Org B"),
        ]:
            await conn.execute("INSERT INTO organizations (id) VALUES ($1)", oid)
            await conn.execute(
                "INSERT INTO organization_names (id, organization_id, name, is_canonical)"
                " VALUES ($1, $2, $3, TRUE)",
                generate_id(),
                oid,
                name,
            )
        for rid, oid, title in [
            (role_a1, id_a, "Director"),
            (role_b1, id_b, "director"),
            (role_a2, id_a, "Manager"),
            (role_b2, id_b, "manager"),
        ]:
            await conn.execute(
                "INSERT INTO roles (id, organization_id, title) VALUES ($1, $2, $3)",
                rid,
                oid,
                title,
            )
        await conn.execute("INSERT INTO people (id) VALUES ($1)", person_id)
        await conn.execute(
            "INSERT INTO person_names (id, person_id, name, is_canonical)"
            " VALUES ($1, $2, 'Safeguard Person', TRUE)",
            generate_id(),
            person_id,
        )
        # Assign person to role_b2 only — the safeguard (not explicit pair) must migrate it
        await conn.execute(
            "INSERT INTO role_assignments (id, person_id, role_id, start_date)"
            " VALUES ($1, $2, $3, '2021-06-01')",
            assign_b2,
            person_id,
            role_b2,
        )

    try:
        # Submit only the first pair; safeguard must migrate assign_b2 to role_a2
        response = client.post(
            f"/admin/orgs/{id_a}/merge-with/{id_b}/",
            data={"merge_role_pairs": f"{role_a1}:{role_b1}"},
            headers=AUTH_HEADERS,
            follow_redirects=False,
        )
        assert response.status_code == 303

        async with db_pool.acquire() as conn:
            rb2_exists = await conn.fetchval("SELECT id FROM roles WHERE id=$1", role_b2)
            # Assignment must have been migrated to role_a2
            migrated = await conn.fetchval(
                "SELECT id FROM role_assignments"
                " WHERE role_id=$1 AND person_id=$2 AND archived_at IS NULL",
                role_a2,
                person_id,
            )
        assert rb2_exists is None  # loser role deleted by safeguard
        assert migrated is not None  # assignment landed on winner role
    finally:
        async with db_pool.acquire() as conn:
            await conn.execute("DELETE FROM role_assignments WHERE person_id=$1", person_id)
            for rid in [role_a1, role_b1, role_a2, role_b2]:
                await conn.execute("DELETE FROM roles WHERE id=$1", rid)
            await conn.execute("DELETE FROM person_names WHERE person_id=$1", person_id)
            await conn.execute("DELETE FROM people WHERE id=$1", person_id)
            for oid in [id_a, id_b]:
                await conn.execute("DELETE FROM organization_names WHERE organization_id=$1", oid)
                await conn.execute("DELETE FROM organizations WHERE id=$1", oid)


# ── Links deduplication ──────────────────────────────────────────────────────


@pytest_asyncio.fixture(loop_scope="session")
async def org_pair_with_shared_link(db_pool):
    """Two orgs both carrying the same (url, link_type_id) link; yield (winner_id, loser_id)."""
    winner_id, loser_id = generate_id(), generate_id()

    async with db_pool.acquire() as conn:
        link_type_id = await conn.fetchval("SELECT id FROM link_types ORDER BY id LIMIT 1")
        for oid, name in [
            (winner_id, "Acme Corp Mergetest"),
            (loser_id, "Acme Corporation Mergetest"),
        ]:
            await conn.execute("INSERT INTO organizations (id) VALUES ($1)", oid)
            await conn.execute(
                "INSERT INTO organization_names (id, organization_id, name, is_canonical)"
                " VALUES ($1, $2, $3, TRUE)",
                generate_id(),
                oid,
                name,
            )
            await conn.execute(
                "INSERT INTO links (id, entity_type, entity_id, url, link_type_id)"
                " VALUES ($1, 'organization', $2, $3, $4)",
                generate_id(),
                oid,
                "https://example.com/shared-org-profile",
                link_type_id,
            )

    yield winner_id, loser_id

    # Merge hard-deletes the loser and reassigns its links/names to the winner,
    # so the loser teardown steps below are no-ops after a successful merge.
    async with db_pool.acquire() as conn:
        for oid in [winner_id, loser_id]:
            await conn.execute(
                "DELETE FROM links WHERE entity_type='organization' AND entity_id=$1", oid
            )
            await conn.execute("DELETE FROM organization_names WHERE organization_id=$1", oid)
            await conn.execute("DELETE FROM organizations WHERE id=$1", oid)


@pytest_asyncio.fixture(loop_scope="session")
async def org_pair_with_acronym(db_pool):
    """Near-duplicate orgs where one has a canonical acronym; yields (id_a, id_b)."""
    id_a, id_b = generate_id(), generate_id()
    if id_a > id_b:
        id_a, id_b = id_b, id_a

    async with db_pool.acquire() as conn:
        for oid, name in [
            (id_a, "Cannabis Regulatory Agency of Ontario"),
            (id_b, "Cannabis Regulatory Agency Ontario"),
        ]:
            await conn.execute("INSERT INTO organizations (id) VALUES ($1)", oid)
            await conn.execute(
                "INSERT INTO organization_names"
                " (id, organization_id, name, is_canonical)"
                " VALUES ($1, $2, $3, TRUE)",
                generate_id(),
                oid,
                name,
            )
        # Give id_a a canonical acronym
        await conn.execute(
            "INSERT INTO organization_acronyms"
            " (id, organization_id, acronym, is_canonical)"
            " VALUES ($1, $2, 'CRAO', TRUE)",
            generate_id(),
            id_a,
        )

    yield id_a, id_b

    async with db_pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM duplicate_dismissals"
            " WHERE entity_a_id=$1 OR entity_b_id=$1"
            " OR entity_a_id=$2 OR entity_b_id=$2",
            id_a,
            id_b,
        )
        for oid in [id_a, id_b]:
            await conn.execute("DELETE FROM organization_acronyms WHERE organization_id=$1", oid)
            await conn.execute("DELETE FROM organization_names WHERE organization_id=$1", oid)
            await conn.execute("DELETE FROM organizations WHERE id=$1", oid)


async def test_duplicates_list_shows_acronym_for_org_with_acronym(client, org_pair_with_acronym):
    """Org with a canonical acronym must show 'Name (ACRONYM)' on the dups review page."""
    response = client.get("/admin/orgs/duplicates/", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert "CRAO" in response.text


async def test_org_merge_deduplicates_shared_link(client, org_pair_with_shared_link, db_pool):
    """Merging two orgs that share a link URL must not 500 on uq_links_entity_url."""
    winner_id, loser_id = org_pair_with_shared_link

    resp = client.post(
        f"/admin/orgs/{winner_id}/merge/{loser_id}/",
        headers=AUTH_HEADERS,
        follow_redirects=False,
    )
    assert resp.status_code in (200, 303), f"Expected 2xx/303, got {resp.status_code}: {resp.text}"

    async with db_pool.acquire() as conn:
        count = await conn.fetchval(
            "SELECT count(*) FROM links WHERE entity_type='organization' AND entity_id=$1",
            winner_id,
        )
    assert count == 1


# ── All-names dup detection ───────────────────────────────────────────────────


@pytest_asyncio.fixture(loop_scope="session")
async def org_pair_alternate_match(db_pool):
    """Org A has a non-canonical alternate that matches B's canonical.

    A canonical: "Acme Consolidated Corporation"  (no similarity with B canonical)
    A alternate: "Acme Corp"                      (exact match with B canonical)
    B canonical: "Acme Corp"
    """
    id_a, id_b = generate_id(), generate_id()
    if id_a > id_b:
        id_a, id_b = id_b, id_a

    async with db_pool.acquire() as conn:
        for oid in [id_a, id_b]:
            await conn.execute("INSERT INTO organizations (id) VALUES ($1)", oid)
        await conn.execute(
            "INSERT INTO organization_names (id, organization_id, name, is_canonical)"
            " VALUES ($1, $2, 'Acme Consolidated Corporation', TRUE)",
            generate_id(),
            id_a,
        )
        await conn.execute(
            "INSERT INTO organization_names (id, organization_id, name, is_canonical)"
            " VALUES ($1, $2, 'Acme Corp', FALSE)",
            generate_id(),
            id_a,
        )
        await conn.execute(
            "INSERT INTO organization_names (id, organization_id, name, is_canonical)"
            " VALUES ($1, $2, 'Acme Corp', TRUE)",
            generate_id(),
            id_b,
        )

    yield id_a, id_b

    async with db_pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM duplicate_dismissals"
            " WHERE entity_a_id=$1 OR entity_b_id=$1"
            " OR entity_a_id=$2 OR entity_b_id=$2",
            id_a,
            id_b,
        )
        for oid in [id_a, id_b]:
            await conn.execute("DELETE FROM organization_names WHERE organization_id=$1", oid)
            await conn.execute("DELETE FROM organizations WHERE id=$1", oid)


async def test_org_alternate_name_match_detected(client, org_pair_alternate_match):
    """Pair where only an alternate name matches the other canonical must be surfaced."""
    id_a, id_b = org_pair_alternate_match
    response = client.get("/admin/orgs/duplicates/", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert f"/admin/orgs/{id_a}/merge-preview/{id_b}/" in response.text


async def test_org_alternate_name_match_shows_canonical_name(client, org_pair_alternate_match):
    """Dup card must show canonical name for each org, not the alternate that drove the match."""
    id_a, id_b = org_pair_alternate_match
    response = client.get("/admin/orgs/duplicates/", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert f'/admin/orgs/{id_a}/">Acme Consolidated Corporation' in response.text
    assert f'/admin/orgs/{id_b}/">Acme Corp' in response.text


async def test_org_alternate_name_match_shows_matched_via(client, org_pair_alternate_match):
    """Dup card must show 'matched via:' secondary line when the matching name is non-canonical."""
    id_a, _id_b = org_pair_alternate_match
    response = client.get("/admin/orgs/duplicates/", headers=AUTH_HEADERS)
    assert response.status_code == 200
    section_start = response.text.find(f'/admin/orgs/{id_a}/">')
    assert section_start != -1
    assert "matched via: Acme Corp" in response.text[section_start : section_start + 300]


@pytest_asyncio.fixture(loop_scope="session")
async def org_pair_multi_name(db_pool):
    """Both orgs carry two names that all cross-match above 0.85.

    Without DISTINCT the query returns 4 rows for this single pair.
    A canonical: "Alberta Gaming Commission",  A alternate: "Alberta Gaming Liquor Commission"
    B canonical: "Alberta Gaming Commission",  B alternate: "Alberta Gaming Liquor Commission"
    """
    id_a, id_b = generate_id(), generate_id()
    if id_a > id_b:
        id_a, id_b = id_b, id_a

    async with db_pool.acquire() as conn:
        for oid in [id_a, id_b]:
            await conn.execute("INSERT INTO organizations (id) VALUES ($1)", oid)
            await conn.execute(
                "INSERT INTO organization_names (id, organization_id, name, is_canonical)"
                " VALUES ($1, $2, 'Alberta Gaming Commission', TRUE)",
                generate_id(),
                oid,
            )
            await conn.execute(
                "INSERT INTO organization_names (id, organization_id, name, is_canonical)"
                " VALUES ($1, $2, 'Alberta Gaming Liquor Commission', FALSE)",
                generate_id(),
                oid,
            )

    yield id_a, id_b

    async with db_pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM duplicate_dismissals"
            " WHERE entity_a_id=$1 OR entity_b_id=$1"
            " OR entity_a_id=$2 OR entity_b_id=$2",
            id_a,
            id_b,
        )
        for oid in [id_a, id_b]:
            await conn.execute("DELETE FROM organization_names WHERE organization_id=$1", oid)
            await conn.execute("DELETE FROM organizations WHERE id=$1", oid)


async def test_org_multi_name_pair_appears_once(client, org_pair_multi_name):
    """Pair with multiple matching name combinations must appear exactly once in the list."""
    id_a, id_b = org_pair_multi_name
    response = client.get("/admin/orgs/duplicates/", headers=AUTH_HEADERS)
    assert response.status_code == 200
    # 2 per card (Keep A param + Keep B param); value > 2 indicates duplicate pair rows
    assert response.text.count(f"/admin/orgs/{id_a}/merge-preview/{id_b}/") == 2


# ---------------------------------------------------------------------------
# Jurisdiction affiliation reassignment
# ---------------------------------------------------------------------------

_GOVERNING_TYPE_ID = "01KW0000000000000000000001"
_STATE_JTYPE_ID = "01KT0HK3452TNDD2WM8E50ZTAT"


async def _make_org(conn, name: str) -> str:
    oid = generate_id()
    await conn.execute("INSERT INTO organizations (id) VALUES ($1)", oid)
    await conn.execute(
        "INSERT INTO organization_names (id, organization_id, name, is_canonical)"
        " VALUES ($1, $2, $3, TRUE)",
        generate_id(),
        oid,
        name,
    )
    return oid


async def _make_jur(conn) -> str:
    jid = generate_id()
    await conn.execute(
        "INSERT INTO jurisdictions (id, slug, name, type_id) VALUES ($1, $2, $3, $4)",
        jid,
        f"test-jur-merge-{jid}",
        f"Merge Test Jurisdiction {jid}",
        _STATE_JTYPE_ID,
    )
    return jid


async def _add_affiliation(conn, org_id: str, jur_id: str) -> None:
    await conn.execute(
        "INSERT INTO organization_jurisdiction_affiliations"
        " (id, organization_id, jurisdiction_id, affiliation_type_id)"
        " VALUES ($1, $2, $3, $4)",
        generate_id(),
        org_id,
        jur_id,
        _GOVERNING_TYPE_ID,
    )


async def test_merge_reassigns_unique_jurisdiction_affiliations(client, db_pool):
    """Loser's jurisdiction affiliation is transferred to winner when winner has none."""
    async with db_pool.acquire() as conn:
        winner_id = await _make_org(conn, "Winner Org Jur Test")
        loser_id = await _make_org(conn, "Loser Org Jur Test")
        jur_id = await _make_jur(conn)
        await _add_affiliation(conn, loser_id, jur_id)

    try:
        response = client.post(
            f"/admin/orgs/{winner_id}/merge/{loser_id}/",
            headers=AUTH_HEADERS,
            follow_redirects=False,
        )
        assert response.status_code == 303

        async with db_pool.acquire() as conn:
            count = await conn.fetchval(
                "SELECT count(*) FROM organization_jurisdiction_affiliations"
                " WHERE organization_id=$1 AND jurisdiction_id=$2",
                winner_id,
                jur_id,
            )
        assert count == 1
    finally:
        async with db_pool.acquire() as conn:
            await conn.execute(
                "DELETE FROM organization_jurisdiction_affiliations WHERE organization_id=$1",
                winner_id,
            )
            await conn.execute("DELETE FROM organization_names WHERE organization_id=$1", winner_id)
            await conn.execute("DELETE FROM organizations WHERE id=$1", winner_id)
            await conn.execute("DELETE FROM jurisdictions WHERE id=$1", jur_id)


async def test_merge_deduplicates_shared_jurisdiction_affiliation(client, db_pool):
    """Shared affiliation (same jur + type) on both orgs produces exactly one row on winner."""
    async with db_pool.acquire() as conn:
        winner_id = await _make_org(conn, "Winner Org Jur Dedup Test")
        loser_id = await _make_org(conn, "Loser Org Jur Dedup Test")
        jur_shared = await _make_jur(conn)
        jur_unique = await _make_jur(conn)
        # Both orgs share jur_shared; loser alone has jur_unique
        await _add_affiliation(conn, winner_id, jur_shared)
        await _add_affiliation(conn, loser_id, jur_shared)
        await _add_affiliation(conn, loser_id, jur_unique)

    try:
        response = client.post(
            f"/admin/orgs/{winner_id}/merge/{loser_id}/",
            headers=AUTH_HEADERS,
            follow_redirects=False,
        )
        assert response.status_code == 303

        async with db_pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT jurisdiction_id FROM organization_jurisdiction_affiliations"
                " WHERE organization_id=$1",
                winner_id,
            )
        jur_ids = {r["jurisdiction_id"] for r in rows}
        assert jur_ids == {jur_shared, jur_unique}
    finally:
        async with db_pool.acquire() as conn:
            await conn.execute(
                "DELETE FROM organization_jurisdiction_affiliations WHERE organization_id=$1",
                winner_id,
            )
            await conn.execute("DELETE FROM organization_names WHERE organization_id=$1", winner_id)
            await conn.execute("DELETE FROM organizations WHERE id=$1", winner_id)
            for jid in (jur_shared, jur_unique):
                await conn.execute("DELETE FROM jurisdictions WHERE id=$1", jid)


# ---------------------------------------------------------------------------
# contact_methods dedup during merge
# ---------------------------------------------------------------------------


async def _add_contact_method(conn, org_id: str, value: str, label: str | None = None) -> str:
    cm_id = generate_id()
    await conn.execute(
        "INSERT INTO contact_methods"
        " (id, entity_type, entity_id, contact_type, value, display_label)"
        " VALUES ($1, 'organization', $2, 'phone', $3, $4)",
        cm_id,
        org_id,
        value,
        label,
    )
    return cm_id


async def test_merge_reassigns_unique_contact_methods(client, db_pool):
    """Loser's contact method not on winner is transferred after merge."""
    async with db_pool.acquire() as conn:
        winner_id = await _make_org(conn, "Winner Org CM Test")
        loser_id = await _make_org(conn, "Loser Org CM Test")
        await _add_contact_method(conn, loser_id, "+13605550001")

    try:
        response = client.post(
            f"/admin/orgs/{winner_id}/merge/{loser_id}/",
            headers=AUTH_HEADERS,
            follow_redirects=False,
        )
        assert response.status_code == 303

        async with db_pool.acquire() as conn:
            count = await conn.fetchval(
                "SELECT count(*) FROM contact_methods"
                " WHERE entity_type='organization' AND entity_id=$1",
                winner_id,
            )
        assert count == 1
    finally:
        async with db_pool.acquire() as conn:
            for oid in (winner_id, loser_id):
                await conn.execute(
                    "DELETE FROM contact_methods WHERE entity_type='organization' AND entity_id=$1",
                    oid,
                )
                await conn.execute("DELETE FROM organization_names WHERE organization_id=$1", oid)
                await conn.execute("DELETE FROM organizations WHERE id=$1", oid)


async def test_merge_deduplicates_shared_contact_method(client, db_pool):
    """Shared contact (same type+value) on both orgs yields exactly one row on winner."""
    async with db_pool.acquire() as conn:
        winner_id = await _make_org(conn, "Winner Org CM Dedup Test")
        loser_id = await _make_org(conn, "Loser Org CM Dedup Test")
        # Both share the same phone; loser also has a unique one
        await _add_contact_method(conn, winner_id, "+13605550100")
        await _add_contact_method(conn, loser_id, "+13605550100")
        await _add_contact_method(conn, loser_id, "+13605550200")

    try:
        response = client.post(
            f"/admin/orgs/{winner_id}/merge/{loser_id}/",
            headers=AUTH_HEADERS,
            follow_redirects=False,
        )
        assert response.status_code == 303

        async with db_pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT value FROM contact_methods"
                " WHERE entity_type='organization' AND entity_id=$1",
                winner_id,
            )
        # Must be exactly 2 rows — shared number not double-inserted
        assert len(rows) == 2
        assert {r["value"] for r in rows} == {"+13605550100", "+13605550200"}
    finally:
        async with db_pool.acquire() as conn:
            for oid in (winner_id, loser_id):
                await conn.execute(
                    "DELETE FROM contact_methods WHERE entity_type='organization' AND entity_id=$1",
                    oid,
                )
                await conn.execute("DELETE FROM organization_names WHERE organization_id=$1", oid)
                await conn.execute("DELETE FROM organizations WHERE id=$1", oid)


# entity_addresses dedup during merge (#232)
# ---------------------------------------------------------------------------


async def _add_org_address(
    conn, org_id: str, raw_input: str, address_type: str = "mailing"
) -> tuple[str, str]:
    """Insert addresses + entity_addresses for an org; return (address_id, entity_address_id)."""
    aid = generate_id()
    eaid = generate_id()
    await conn.execute(
        "INSERT INTO addresses (id, raw_input, country) VALUES ($1, $2, 'US')",
        aid,
        raw_input,
    )
    await conn.execute(
        "INSERT INTO entity_addresses"
        " (id, entity_type, entity_id, address_id, address_type)"
        " VALUES ($1, 'organization', $2, $3, $4)",
        eaid,
        org_id,
        aid,
        address_type,
    )
    return aid, eaid


async def test_merge_reassigns_unique_org_address(client, db_pool):
    """Loser org's address not on winner is transferred after merge."""
    async with db_pool.acquire() as conn:
        winner_id = await _make_org(conn, "Winner Org Addr Test")
        loser_id = await _make_org(conn, "Loser Org Addr Test")
        await _add_org_address(conn, loser_id, "100 Unique Org St")

    try:
        response = client.post(
            f"/admin/orgs/{winner_id}/merge/{loser_id}/",
            headers=AUTH_HEADERS,
            follow_redirects=False,
        )
        assert response.status_code == 303

        async with db_pool.acquire() as conn:
            count = await conn.fetchval(
                "SELECT count(*) FROM entity_addresses"
                " WHERE entity_type='organization' AND entity_id=$1",
                winner_id,
            )
        assert count == 1
    finally:
        async with db_pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT address_id FROM entity_addresses"
                " WHERE entity_type='organization' AND entity_id=$1",
                winner_id,
            )
            await conn.execute(
                "DELETE FROM entity_addresses WHERE entity_type='organization' AND entity_id=$1",
                winner_id,
            )
            if rows:
                await conn.execute(
                    "DELETE FROM addresses WHERE id = ANY($1::text[])",
                    [r["address_id"] for r in rows],
                )
            for oid in (winner_id, loser_id):
                await conn.execute("DELETE FROM organization_names WHERE organization_id=$1", oid)
                await conn.execute("DELETE FROM organizations WHERE id=$1", oid)


async def test_org_merge_entity_changes_records_merged_into(client, db_pool):
    """POST merge writes a deleted entity_changes row with merged_into=winner_id."""
    winner_id, loser_id = generate_id(), generate_id()

    async with db_pool.acquire() as conn:
        for oid, name in [(winner_id, "Merged-Into Winner"), (loser_id, "Merged-Into Loser")]:
            await conn.execute("INSERT INTO organizations (id) VALUES ($1)", oid)
            await conn.execute(
                "INSERT INTO organization_names (id, organization_id, name, is_canonical)"
                " VALUES ($1, $2, $3, TRUE)",
                generate_id(),
                oid,
                name,
            )
        before_seq = await conn.fetchval("SELECT COALESCE(MAX(id), 0) FROM entity_changes")

    try:
        response = client.post(
            f"/admin/orgs/{winner_id}/merge/{loser_id}/",
            headers=AUTH_HEADERS,
            follow_redirects=False,
        )
        assert response.status_code == 303

        async with db_pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT change_kind, merged_into FROM entity_changes"
                " WHERE entity_id=$1 AND id > $2"
                " ORDER BY id DESC LIMIT 1",
                loser_id,
                before_seq,
            )
        assert row is not None
        assert row["change_kind"] == "deleted"
        assert row["merged_into"] == winner_id
    finally:
        async with db_pool.acquire() as conn:
            await conn.execute("DELETE FROM entity_changes WHERE entity_id=$1", loser_id)
            await conn.execute(
                "DELETE FROM deleted_entities WHERE entity_type='organization' AND entity_id=$1",
                loser_id,
            )
            await conn.execute("DELETE FROM organization_names WHERE organization_id=$1", winner_id)
            await conn.execute("DELETE FROM organizations WHERE id=$1", winner_id)


async def test_merge_deduplicates_shared_org_address(client, db_pool):
    """Shared address (same address_id + type) on both orgs yields one row on winner."""
    async with db_pool.acquire() as conn:
        winner_id = await _make_org(conn, "Winner Org Addr Dedup Test")
        loser_id = await _make_org(conn, "Loser Org Addr Dedup Test")
        shared_aid = generate_id()
        await conn.execute(
            "INSERT INTO addresses (id, raw_input, country)"
            " VALUES ($1, '100 Shared Org Ave', 'US')",
            shared_aid,
        )
        for oid in (winner_id, loser_id):
            await conn.execute(
                "INSERT INTO entity_addresses"
                " (id, entity_type, entity_id, address_id, address_type)"
                " VALUES ($1, 'organization', $2, $3, 'mailing')",
                generate_id(),
                oid,
                shared_aid,
            )
        unique_aid = generate_id()
        await conn.execute(
            "INSERT INTO addresses (id, raw_input, country) VALUES ($1, '200 Unique Org Rd', 'US')",
            unique_aid,
        )
        await conn.execute(
            "INSERT INTO entity_addresses"
            " (id, entity_type, entity_id, address_id, address_type)"
            " VALUES ($1, 'organization', $2, $3, 'physical')",
            generate_id(),
            loser_id,
            unique_aid,
        )

    try:
        response = client.post(
            f"/admin/orgs/{winner_id}/merge/{loser_id}/",
            headers=AUTH_HEADERS,
            follow_redirects=False,
        )
        assert response.status_code == 303

        async with db_pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT address_id, address_type FROM entity_addresses"
                " WHERE entity_type='organization' AND entity_id=$1",
                winner_id,
            )
        assert len(rows) == 2
        addr_ids = {r["address_id"] for r in rows}
        assert shared_aid in addr_ids
        assert unique_aid in addr_ids
    finally:
        async with db_pool.acquire() as conn:
            await conn.execute(
                "DELETE FROM entity_addresses WHERE entity_type='organization' AND entity_id=$1",
                winner_id,
            )
            await conn.execute(
                "DELETE FROM addresses WHERE id = ANY($1::text[])",
                [shared_aid, unique_aid],
            )
            for oid in (winner_id, loser_id):
                await conn.execute("DELETE FROM organization_names WHERE organization_id=$1", oid)
                await conn.execute("DELETE FROM organizations WHERE id=$1", oid)
