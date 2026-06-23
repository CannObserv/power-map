"""Integration tests for people duplicate detection and dismiss routes."""

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
async def person_pair(db_pool):
    """Insert two near-duplicate people (id_a < id_b), yield (id_a, id_b), teardown."""
    id_a, id_b = generate_id(), generate_id()
    if id_a > id_b:
        id_a, id_b = id_b, id_a

    async with db_pool.acquire() as conn:
        for pid, name in [
            (id_a, "Jonathan Smithfield"),
            (id_b, "Jonathan Smithfield Jr"),  # deliberate near-match (similarity ~0.91)
        ]:
            await conn.execute("INSERT INTO people (id) VALUES ($1)", pid)
            await conn.execute(
                "INSERT INTO person_names"
                " (id, person_id, name, is_canonical)"
                " VALUES ($1, $2, $3, TRUE)",
                generate_id(),
                pid,
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
        for pid in [id_a, id_b]:
            await conn.execute("DELETE FROM person_names WHERE person_id=$1", pid)
            await conn.execute("DELETE FROM people WHERE id=$1", pid)


# ── List screen ─────────────────────────────────────────────────────────────


async def test_people_list_has_dup_banner_slot(client, person_pair):
    """People list page has the async HTMX slot for the dup banner, not an inline count."""
    response = client.get("/admin/people/", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert 'hx-get="/admin/_dup-badge/people/?variant=banner"' in response.text
    assert 'hx-swap="innerHTML"' in response.text


# ── Duplicates review screen ─────────────────────────────────────────────────


async def test_duplicates_list_returns_200(client, person_pair):
    response = client.get("/admin/people/duplicates/", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert "duplicate" in response.text.lower()


async def test_duplicates_list_shows_pair(client, person_pair):
    response = client.get("/admin/people/duplicates/", headers=AUTH_HEADERS)
    assert "Jonathan Smithfield" in response.text


# ── Dismiss ──────────────────────────────────────────────────────────────────


async def test_dismiss_pair_removes_from_list(client, person_pair):
    id_a, id_b = person_pair
    response = client.post(
        f"/admin/people/{id_a}/dismiss-duplicate/{id_b}/",
        headers=AUTH_HEADERS,
        follow_redirects=False,
    )
    assert response.status_code in (302, 303)
    response2 = client.get("/admin/people/duplicates/", headers=AUTH_HEADERS)
    assert response2.status_code == 200
    assert (
        "Jonathan Smithfield" not in response2.text
        and "Jonathan Smithfield Jr" not in response2.text
    )


async def test_dismiss_htmx_returns_200_with_region(client, person_pair):
    id_a, id_b = person_pair
    response = client.post(
        f"/admin/people/{id_a}/dismiss-duplicate/{id_b}/",
        headers=HTMX_HEADERS,
        follow_redirects=False,
    )
    assert response.status_code == 200
    assert "candidate" in response.text or "No duplicate" in response.text


async def test_dismiss_htmx_sends_hx_trigger_flash(client, person_pair):
    id_a, id_b = person_pair
    response = client.post(
        f"/admin/people/{id_a}/dismiss-duplicate/{id_b}/",
        headers=HTMX_HEADERS,
        follow_redirects=False,
    )
    assert response.status_code == 200
    assert "HX-Trigger" in response.headers
    payload = json.loads(response.headers["HX-Trigger"])
    assert payload["showFlash"]["level"] == "info"
    assert "hx-swap-oob" not in response.text


async def test_dismiss_htmx_emits_refresh_dup_badge(client, person_pair):
    """HTMX dismiss response must include refreshDupBadge in HX-Trigger."""
    id_a, id_b = person_pair
    response = client.post(
        f"/admin/people/{id_a}/dismiss-duplicate/{id_b}/",
        headers=HTMX_HEADERS,
        follow_redirects=False,
    )
    assert response.status_code == 200
    trigger = json.loads(response.headers.get("HX-Trigger", "{}"))
    assert "refreshDupBadge" in trigger


# ── Sidebar badge on non-list pages ──────────────────────────────────────────


@pytest_asyncio.fixture(loop_scope="session")
async def person_pair_with_roles(db_pool):
    """
    Two near-duplicate people, each with:
      - one shared role+start_date (conflict → loser's deleted on merge)
      - one unique role (reassigned to winner on merge)
    Yields (id_winner, id_loser, shared_role_id, unique_role_id_loser).
    """
    id_a, id_b = generate_id(), generate_id()
    if id_a > id_b:
        id_a, id_b = id_b, id_a
    id_org = generate_id()
    shared_role_id = generate_id()
    unique_role_id = generate_id()
    ra_a_shared = generate_id()
    ra_b_shared = generate_id()
    ra_b_unique = generate_id()

    async with db_pool.acquire() as conn:
        await conn.execute("INSERT INTO organizations (id) VALUES ($1)", id_org)
        await conn.execute(
            "INSERT INTO organization_names (id, organization_id, name, is_canonical)"
            " VALUES ($1, $2, 'Test Org', TRUE)",
            generate_id(),
            id_org,
        )
        for pid, name in [
            (id_a, "Jonathan Smithfield"),
            (id_b, "Jonathan Smithfield Jr"),
        ]:
            await conn.execute("INSERT INTO people (id) VALUES ($1)", pid)
            await conn.execute(
                "INSERT INTO person_names (id, person_id, name, is_canonical)"
                " VALUES ($1, $2, $3, TRUE)",
                generate_id(),
                pid,
                name,
            )
        for rid, title in [(shared_role_id, "Director"), (unique_role_id, "Advisor")]:
            await conn.execute(
                "INSERT INTO roles (id, organization_id, title) VALUES ($1, $2, $3)",
                rid,
                id_org,
                title,
            )
        # Both people hold shared_role starting 2024-01-01
        for ra_id, pid in [(ra_a_shared, id_a), (ra_b_shared, id_b)]:
            await conn.execute(
                "INSERT INTO role_assignments"
                " (id, person_id, role_id, is_current, start_date)"
                " VALUES ($1, $2, $3, FALSE, '2024-01-01')",
                ra_id,
                pid,
                shared_role_id,
            )
        # Only loser holds unique_role
        await conn.execute(
            "INSERT INTO role_assignments"
            " (id, person_id, role_id, is_current)"
            " VALUES ($1, $2, $3, TRUE)",
            ra_b_unique,
            id_b,
            unique_role_id,
        )

    yield id_a, id_b, shared_role_id, unique_role_id

    async with db_pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM duplicate_dismissals"
            " WHERE entity_a_id=$1 OR entity_b_id=$1"
            " OR entity_a_id=$2 OR entity_b_id=$2",
            id_a,
            id_b,
        )
        await conn.execute(
            "DELETE FROM role_assignments WHERE person_id=$1 OR person_id=$2",
            id_a,
            id_b,
        )
        await conn.execute(
            "DELETE FROM roles WHERE id=$1 OR id=$2",
            shared_role_id,
            unique_role_id,
        )
        for pid in [id_a, id_b]:
            await conn.execute("DELETE FROM person_names WHERE person_id=$1", pid)
            await conn.execute("DELETE FROM people WHERE id=$1", pid)
        await conn.execute("DELETE FROM organization_names WHERE organization_id=$1", id_org)
        await conn.execute("DELETE FROM organizations WHERE id=$1", id_org)


# ── Merge ────────────────────────────────────────────────────────────────────


async def test_merge_hard_deletes_loser(client, person_pair, db_pool):
    id_a, id_b = person_pair
    response = client.post(
        f"/admin/people/{id_a}/merge/{id_b}/",
        headers=AUTH_HEADERS,
        follow_redirects=False,
    )
    assert response.status_code == 303

    async with db_pool.acquire() as conn:
        row = await conn.fetchrow("SELECT id FROM people WHERE id=$1", id_b)
    assert row is None


async def test_merge_reassigns_loser_names_as_aliases(client, person_pair, db_pool):
    id_a, id_b = person_pair

    async with db_pool.acquire() as conn:
        loser_names = await conn.fetch(
            "SELECT name, is_canonical FROM person_names WHERE person_id=$1", id_b
        )
    loser_canonical = next(r["name"] for r in loser_names if r["is_canonical"])

    client.post(
        f"/admin/people/{id_a}/merge/{id_b}/",
        headers=AUTH_HEADERS,
        follow_redirects=False,
    )

    async with db_pool.acquire() as conn:
        winner_names = await conn.fetch(
            "SELECT name, is_canonical FROM person_names WHERE person_id=$1", id_a
        )
    winner_name_strs = {r["name"] for r in winner_names}
    # Loser's canonical name now exists on winner as a non-canonical alias
    assert loser_canonical in winner_name_strs
    canonical_rows = [r for r in winner_names if r["is_canonical"]]
    assert len(canonical_rows) == 1  # exactly one canonical remains


async def test_merge_deletes_conflicting_role_assignment(client, person_pair_with_roles, db_pool):
    id_winner, id_loser, shared_role_id, unique_role_id = person_pair_with_roles
    client.post(
        f"/admin/people/{id_winner}/merge/{id_loser}/",
        headers=AUTH_HEADERS,
        follow_redirects=False,
    )

    async with db_pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id FROM role_assignments WHERE person_id=$1 AND role_id=$2",
            id_winner,
            shared_role_id,
        )
    assert len(rows) == 1  # only winner's original assignment remains


async def test_merge_reassigns_unique_role_assignment(client, person_pair_with_roles, db_pool):
    id_winner, id_loser, shared_role_id, unique_role_id = person_pair_with_roles
    client.post(
        f"/admin/people/{id_winner}/merge/{id_loser}/",
        headers=AUTH_HEADERS,
        follow_redirects=False,
    )

    async with db_pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id FROM role_assignments WHERE person_id=$1 AND role_id=$2",
            id_winner,
            unique_role_id,
        )
    assert row is not None  # loser's unique role now on winner


async def test_merge_returns_404_for_unknown_person(client, person_pair):
    id_a, _ = person_pair
    response = client.post(
        f"/admin/people/{id_a}/merge/nonexistent-id/",
        headers=AUTH_HEADERS,
        follow_redirects=False,
    )
    assert response.status_code == 404


async def test_merge_preserves_winner_existing_roles(client, person_pair_with_roles, db_pool):
    id_winner, id_loser, shared_role_id, unique_role_id = person_pair_with_roles

    async with db_pool.acquire() as conn:
        count_before = await conn.fetchval(
            "SELECT count(*) FROM role_assignments WHERE person_id=$1",
            id_winner,
        )

    client.post(
        f"/admin/people/{id_winner}/merge/{id_loser}/",
        headers=AUTH_HEADERS,
        follow_redirects=False,
    )

    async with db_pool.acquire() as conn:
        count_after = await conn.fetchval(
            "SELECT count(*) FROM role_assignments WHERE person_id=$1",
            id_winner,
        )
    # Winner had 1 role; loser had 1 shared (deleted) + 1 unique (reassigned) → winner gets 2
    assert count_after == count_before + 1  # shared conflict deleted; unique role added


async def test_merge_htmx_returns_200_with_region(client, person_pair):
    id_a, id_b = person_pair
    response = client.post(
        f"/admin/people/{id_a}/merge/{id_b}/",
        headers=HTMX_HEADERS,
        follow_redirects=False,
    )
    assert response.status_code == 200
    assert "candidate" in response.text or "No duplicate" in response.text


async def test_merge_htmx_sends_hx_trigger_flash(client, person_pair):
    id_a, id_b = person_pair
    response = client.post(
        f"/admin/people/{id_a}/merge/{id_b}/",
        headers=HTMX_HEADERS,
        follow_redirects=False,
    )
    assert response.status_code == 200
    assert "HX-Trigger" in response.headers
    payload = json.loads(response.headers["HX-Trigger"])
    assert payload["showFlash"]["level"] == "success"
    assert "Jonathan Smithfield" in payload["showFlash"]["body"]
    assert f"/admin/people/{id_a}/" in payload["showFlash"]["body"]
    assert "hx-swap-oob" not in response.text


async def test_merge_htmx_emits_refresh_dup_badge(client, person_pair):
    """HTMX merge response (dup-page branch) must include refreshDupBadge in HX-Trigger."""
    id_a, id_b = person_pair
    response = client.post(
        f"/admin/people/{id_a}/merge/{id_b}/",
        headers=HTMX_HEADERS,
        follow_redirects=False,
    )
    assert response.status_code == 200
    trigger = json.loads(response.headers.get("HX-Trigger", "{}"))
    assert "refreshDupBadge" in trigger


@pytest_asyncio.fixture(loop_scope="session")
async def person_pair_exact_name(db_pool):
    """Two people with the same canonical name — merge must not produce duplicate name rows."""
    id_a, id_b = generate_id(), generate_id()
    if id_a > id_b:
        id_a, id_b = id_b, id_a

    async with db_pool.acquire() as conn:
        for pid in [id_a, id_b]:
            await conn.execute("INSERT INTO people (id) VALUES ($1)", pid)
            await conn.execute(
                "INSERT INTO person_names (id, person_id, name, is_canonical)"
                " VALUES ($1, $2, 'Jordan Smith', TRUE)",
                generate_id(),
                pid,
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
        for pid in [id_a, id_b]:
            await conn.execute("DELETE FROM person_names WHERE person_id=$1", pid)
            await conn.execute("DELETE FROM people WHERE id=$1", pid)


async def test_merge_deduplicates_identical_names(client, person_pair_exact_name, db_pool):
    id_a, id_b = person_pair_exact_name
    client.post(
        f"/admin/people/{id_a}/merge/{id_b}/",
        headers=AUTH_HEADERS,
        follow_redirects=False,
    )

    async with db_pool.acquire() as conn:
        rows = await conn.fetch("SELECT name FROM person_names WHERE person_id=$1", id_a)
    assert len(rows) == 1
    assert rows[0]["name"] == "Jordan Smith"


async def test_duplicates_region_has_keep_a_keep_b_buttons(client, person_pair):
    id_a, id_b = person_pair
    response = client.get("/admin/people/duplicates/", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert "Keep A" in response.text
    assert "Keep B" in response.text
    assert f"/admin/people/{id_a}/merge/{id_b}/" in response.text
    assert f"/admin/people/{id_b}/merge/{id_a}/" in response.text


# ── Notes merge ──────────────────────────────────────────────────────────────


async def test_merge_notes_loser_only(client, person_pair, db_pool):
    """Loser has notes, winner does not → winner gets prefixed loser notes."""
    id_a, id_b = person_pair

    async with db_pool.acquire() as conn:
        await conn.execute(
            "UPDATE people SET notes=$1 WHERE id=$2",
            "Loser note content.",
            id_b,
        )

    client.post(
        f"/admin/people/{id_a}/merge/{id_b}/",
        headers=AUTH_HEADERS,
        follow_redirects=False,
    )

    async with db_pool.acquire() as conn:
        notes = await conn.fetchval("SELECT notes FROM people WHERE id=$1", id_a)
    assert notes is not None
    assert "Loser note content." in notes
    assert "Merged from" in notes
    assert "admin@test.com" in notes
    # No leading blank line — winner had no prior notes
    assert not notes.startswith("\n")


async def test_merge_notes_both(client, person_pair, db_pool):
    """Both have notes → winner's existing notes + blank line + prefixed loser notes."""
    id_a, id_b = person_pair

    async with db_pool.acquire() as conn:
        await conn.execute("UPDATE people SET notes=$1 WHERE id=$2", "Winner original.", id_a)
        await conn.execute("UPDATE people SET notes=$1 WHERE id=$2", "Loser note.", id_b)

    client.post(
        f"/admin/people/{id_a}/merge/{id_b}/",
        headers=AUTH_HEADERS,
        follow_redirects=False,
    )

    async with db_pool.acquire() as conn:
        notes = await conn.fetchval("SELECT notes FROM people WHERE id=$1", id_a)
    assert notes.startswith("Winner original.")
    assert "\n\n" in notes
    assert "Loser note." in notes
    assert "Merged from" in notes


async def test_merge_notes_skipped_when_loser_has_none(client, person_pair, db_pool):
    """Loser has no notes → winner notes unchanged (NULL stays NULL)."""
    id_a, id_b = person_pair

    async with db_pool.acquire() as conn:
        await conn.execute("UPDATE people SET notes=NULL WHERE id=$1", id_b)

    client.post(
        f"/admin/people/{id_a}/merge/{id_b}/",
        headers=AUTH_HEADERS,
        follow_redirects=False,
    )

    async with db_pool.acquire() as conn:
        notes = await conn.fetchval("SELECT notes FROM people WHERE id=$1", id_a)
    assert notes is None


async def test_merge_notes_skipped_preserves_winner_notes(client, person_pair, db_pool):
    """Loser has no notes, winner has notes → winner notes unchanged."""
    id_a, id_b = person_pair

    async with db_pool.acquire() as conn:
        await conn.execute("UPDATE people SET notes=$1 WHERE id=$2", "Winner only.", id_a)

    client.post(
        f"/admin/people/{id_a}/merge/{id_b}/",
        headers=AUTH_HEADERS,
        follow_redirects=False,
    )

    async with db_pool.acquire() as conn:
        notes = await conn.fetchval("SELECT notes FROM people WHERE id=$1", id_a)
    assert notes == "Winner only."


# ── Links deduplication ──────────────────────────────────────────────────────


@pytest_asyncio.fixture(loop_scope="session")
async def person_pair_with_shared_link(db_pool):
    """Two people both carrying the same (url, link_type_id) link; yield (winner_id, loser_id)."""
    winner_id, loser_id = generate_id(), generate_id()

    async with db_pool.acquire() as conn:
        link_type_id = await conn.fetchval("SELECT id FROM link_types ORDER BY id LIMIT 1")
        for pid, name in [
            (winner_id, "Alice Mergetest"),
            (loser_id, "Alicia Mergetest"),
        ]:
            await conn.execute("INSERT INTO people (id) VALUES ($1)", pid)
            await conn.execute(
                "INSERT INTO person_names (id, person_id, name, is_canonical)"
                " VALUES ($1, $2, $3, TRUE)",
                generate_id(),
                pid,
                name,
            )
            await conn.execute(
                "INSERT INTO links (id, entity_type, entity_id, url, link_type_id)"
                " VALUES ($1, 'person', $2, $3, $4)",
                generate_id(),
                pid,
                "https://example.com/shared-profile",
                link_type_id,
            )

    yield winner_id, loser_id

    # Merge hard-deletes the loser and reassigns its links/names to the winner,
    # so the loser teardown steps below are no-ops after a successful merge.
    async with db_pool.acquire() as conn:
        for pid in [winner_id, loser_id]:
            await conn.execute("DELETE FROM links WHERE entity_type='person' AND entity_id=$1", pid)
            await conn.execute("DELETE FROM person_names WHERE person_id=$1", pid)
            await conn.execute("DELETE FROM people WHERE id=$1", pid)


async def test_merge_deduplicates_shared_link(client, person_pair_with_shared_link, db_pool):
    """Merging two people who share a link URL must not 500 on uq_links_entity_url."""
    winner_id, loser_id = person_pair_with_shared_link

    resp = client.post(
        f"/admin/people/{winner_id}/merge/{loser_id}/",
        headers=AUTH_HEADERS,
        follow_redirects=False,
    )
    assert resp.status_code in (200, 303), f"Expected 2xx/303, got {resp.status_code}: {resp.text}"

    async with db_pool.acquire() as conn:
        count = await conn.fetchval(
            "SELECT count(*) FROM links WHERE entity_type='person' AND entity_id=$1",
            winner_id,
        )
    assert count == 1


# ── All-names dup detection ───────────────────────────────────────────────────


@pytest_asyncio.fixture(loop_scope="session")
async def person_pair_alternate_match(db_pool):
    """Person A has a non-canonical public alternate that matches B's canonical.

    A canonical: "Jordan Fitzgerald Marsh"  (similarity ~0.46 vs B canonical — no match)
    A alternate: "Jordan Marsh"             (similarity 1.0 vs B canonical — match)
    B canonical: "Jordan Marsh"
    """
    id_a, id_b = generate_id(), generate_id()
    if id_a > id_b:
        id_a, id_b = id_b, id_a

    async with db_pool.acquire() as conn:
        for pid in [id_a, id_b]:
            await conn.execute("INSERT INTO people (id) VALUES ($1)", pid)
        await conn.execute(
            "INSERT INTO person_names (id, person_id, name, is_canonical)"
            " VALUES ($1, $2, 'Jordan Fitzgerald Marsh', TRUE)",
            generate_id(),
            id_a,
        )
        await conn.execute(
            "INSERT INTO person_names (id, person_id, name, is_canonical)"
            " VALUES ($1, $2, 'Jordan Marsh', FALSE)",
            generate_id(),
            id_a,
        )
        await conn.execute(
            "INSERT INTO person_names (id, person_id, name, is_canonical)"
            " VALUES ($1, $2, 'Jordan Marsh', TRUE)",
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
        for pid in [id_a, id_b]:
            await conn.execute("DELETE FROM person_names WHERE person_id=$1", pid)
            await conn.execute("DELETE FROM people WHERE id=$1", pid)


async def test_alternate_name_match_detected(client, person_pair_alternate_match):
    """Pair where only an alternate name matches the other canonical must be surfaced."""
    id_a, id_b = person_pair_alternate_match
    response = client.get("/admin/people/duplicates/", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert f"/admin/people/{id_a}/merge/{id_b}/" in response.text


@pytest_asyncio.fixture(loop_scope="session")
async def person_pair_multi_name(db_pool):
    """Both people carry two public names that all cross-match above 0.85.

    Without DISTINCT the query returns 4 rows for this single pair.
    Names are deliberately distinct from other fixtures to avoid cross-fixture pair detection.
    A canonical: "Tiberius Blackwood",    A alternate: "Tiberius Blackwood Jr"
    B canonical: "Tiberius Blackwood",    B alternate: "Tiberius Blackwood Jr"
    """
    id_a, id_b = generate_id(), generate_id()
    if id_a > id_b:
        id_a, id_b = id_b, id_a

    async with db_pool.acquire() as conn:
        for pid in [id_a, id_b]:
            await conn.execute("INSERT INTO people (id) VALUES ($1)", pid)
            await conn.execute(
                "INSERT INTO person_names (id, person_id, name, is_canonical)"
                " VALUES ($1, $2, 'Tiberius Blackwood', TRUE)",
                generate_id(),
                pid,
            )
            await conn.execute(
                "INSERT INTO person_names (id, person_id, name, is_canonical)"
                " VALUES ($1, $2, 'Tiberius Blackwood Jr', FALSE)",
                generate_id(),
                pid,
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
        for pid in [id_a, id_b]:
            await conn.execute("DELETE FROM person_names WHERE person_id=$1", pid)
            await conn.execute("DELETE FROM people WHERE id=$1", pid)


async def test_multi_name_pair_appears_once(client, person_pair_multi_name):
    """Pair with multiple matching name combinations must appear exactly once in the list."""
    id_a, id_b = person_pair_multi_name
    response = client.get("/admin/people/duplicates/", headers=AUTH_HEADERS)
    assert response.status_code == 200
    # Each pair card renders this merge URL exactly once; count > 1 means duplicate rows
    assert response.text.count(f"/admin/people/{id_a}/merge/{id_b}/") == 1


@pytest_asyncio.fixture(loop_scope="session")
async def person_pair_hidden_match(db_pool):
    """Person A has a hidden name that matches B's canonical; should not be detected.

    A canonical: "John Adams"   (no similarity with B canonical)
    A hidden:    "Jane Miller"  (exact match with B canonical, but visibility='hidden')
    B canonical: "Jane Miller"
    """
    id_a, id_b = generate_id(), generate_id()
    if id_a > id_b:
        id_a, id_b = id_b, id_a

    async with db_pool.acquire() as conn:
        for pid in [id_a, id_b]:
            await conn.execute("INSERT INTO people (id) VALUES ($1)", pid)
        await conn.execute(
            "INSERT INTO person_names (id, person_id, name, is_canonical)"
            " VALUES ($1, $2, 'John Adams', TRUE)",
            generate_id(),
            id_a,
        )
        await conn.execute(
            "INSERT INTO person_names (id, person_id, name, is_canonical, visibility)"
            " VALUES ($1, $2, 'Jane Miller', FALSE, 'hidden')",
            generate_id(),
            id_a,
        )
        await conn.execute(
            "INSERT INTO person_names (id, person_id, name, is_canonical)"
            " VALUES ($1, $2, 'Jane Miller', TRUE)",
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
        for pid in [id_a, id_b]:
            await conn.execute("DELETE FROM person_names WHERE person_id=$1", pid)
            await conn.execute("DELETE FROM people WHERE id=$1", pid)


async def test_hidden_name_not_detected(client, person_pair_hidden_match):
    """Match via a hidden name must not surface the pair."""
    id_a, id_b = person_pair_hidden_match
    response = client.get("/admin/people/duplicates/", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert f"/admin/people/{id_a}/merge/{id_b}/" not in response.text


@pytest_asyncio.fixture(loop_scope="session")
async def person_pair_legal_only_match(db_pool):
    """Person A has a legal_only name that matches B's canonical; should be detected.

    legal_only names participate in dup detection (only 'hidden' is excluded).
    Names are distinct from person_pair_hidden_match to avoid cross-fixture pairs.

    A canonical: "Peter Wimsey"     (no similarity with B canonical)
    A legal_only: "Harriet Vane"    (exact match with B canonical, visibility='legal_only')
    B canonical:  "Harriet Vane"
    """
    id_a, id_b = generate_id(), generate_id()
    if id_a > id_b:
        id_a, id_b = id_b, id_a

    async with db_pool.acquire() as conn:
        for pid in [id_a, id_b]:
            await conn.execute("INSERT INTO people (id) VALUES ($1)", pid)
        await conn.execute(
            "INSERT INTO person_names (id, person_id, name, is_canonical)"
            " VALUES ($1, $2, 'Peter Wimsey', TRUE)",
            generate_id(),
            id_a,
        )
        await conn.execute(
            "INSERT INTO person_names (id, person_id, name, is_canonical, visibility)"
            " VALUES ($1, $2, 'Harriet Vane', FALSE, 'legal_only')",
            generate_id(),
            id_a,
        )
        await conn.execute(
            "INSERT INTO person_names (id, person_id, name, is_canonical)"
            " VALUES ($1, $2, 'Harriet Vane', TRUE)",
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
        for pid in [id_a, id_b]:
            await conn.execute("DELETE FROM person_names WHERE person_id=$1", pid)
            await conn.execute("DELETE FROM people WHERE id=$1", pid)


async def test_legal_only_name_detected(client, person_pair_legal_only_match):
    """Match via a legal_only name must surface the pair."""
    id_a, id_b = person_pair_legal_only_match
    response = client.get("/admin/people/duplicates/", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert f"/admin/people/{id_a}/merge/{id_b}/" in response.text


async def test_alternate_name_match_shows_canonical_name(client, person_pair_alternate_match):
    """Dup card must show canonical name ('Jordan Fitzgerald Marsh'), not the matched alternate."""
    id_a, id_b = person_pair_alternate_match
    response = client.get("/admin/people/duplicates/", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert f'/admin/people/{id_a}/">Jordan Fitzgerald Marsh' in response.text
    assert f'/admin/people/{id_b}/">Jordan Marsh' in response.text


async def test_alternate_name_match_shows_matched_via_public(client, person_pair_alternate_match):
    """Dup card shows 'matched via:' secondary line for a public alternate match."""
    id_a, _id_b = person_pair_alternate_match
    response = client.get("/admin/people/duplicates/", headers=AUTH_HEADERS)
    assert response.status_code == 200
    section_start = response.text.find(f'/admin/people/{id_a}/">')
    assert section_start != -1
    assert "matched via: Jordan Marsh" in response.text[section_start : section_start + 300]


async def test_legal_only_name_match_does_not_show_matched_via(
    client, person_pair_legal_only_match
):
    """Dup card must NOT reveal a legal_only matched name in 'matched via:' secondary line."""
    response = client.get("/admin/people/duplicates/", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert "Peter Wimsey" in response.text
    assert "matched via: Harriet Vane" not in response.text


# ---------------------------------------------------------------------------
# entity_addresses dedup during merge (#232)
# ---------------------------------------------------------------------------


async def _make_person(conn, name: str) -> str:
    pid = generate_id()
    await conn.execute("INSERT INTO people (id) VALUES ($1)", pid)
    await conn.execute(
        "INSERT INTO person_names (id, person_id, name, is_canonical) VALUES ($1, $2, $3, TRUE)",
        generate_id(),
        pid,
        name,
    )
    return pid


async def _add_person_address(
    conn, person_id: str, raw_input: str, address_type: str = "mailing"
) -> tuple[str, str]:
    """Insert addresses + entity_addresses; return (address_id, entity_address_id)."""
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
        " VALUES ($1, 'person', $2, $3, $4)",
        eaid,
        person_id,
        aid,
        address_type,
    )
    return aid, eaid


async def test_merge_reassigns_unique_person_address(client, db_pool):
    """Loser's address not on winner is transferred to winner after merge."""
    async with db_pool.acquire() as conn:
        winner_id = await _make_person(conn, "Winner Person Addr Test")
        loser_id = await _make_person(conn, "Loser Person Addr Test")
        aid, _eaid = await _add_person_address(conn, loser_id, "100 Unique St")

    try:
        response = client.post(
            f"/admin/people/{winner_id}/merge/{loser_id}/",
            headers=AUTH_HEADERS,
            follow_redirects=False,
        )
        assert response.status_code == 303

        async with db_pool.acquire() as conn:
            count = await conn.fetchval(
                "SELECT count(*) FROM entity_addresses WHERE entity_type='person' AND entity_id=$1",
                winner_id,
            )
        assert count == 1
    finally:
        async with db_pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT address_id FROM entity_addresses"
                " WHERE entity_type='person' AND entity_id=$1",
                winner_id,
            )
            await conn.execute(
                "DELETE FROM entity_addresses WHERE entity_type='person' AND entity_id=$1",
                winner_id,
            )
            if rows:
                await conn.execute(
                    "DELETE FROM addresses WHERE id = ANY($1::text[])",
                    [r["address_id"] for r in rows],
                )
            for pid in (winner_id, loser_id):
                await conn.execute("DELETE FROM person_names WHERE person_id=$1", pid)
                await conn.execute("DELETE FROM people WHERE id=$1", pid)


async def test_merge_deduplicates_shared_person_address(client, db_pool):
    """Shared address (same address_id + type) on both people yields one row on winner."""
    async with db_pool.acquire() as conn:
        winner_id = await _make_person(conn, "Winner Person Addr Dedup Test")
        loser_id = await _make_person(conn, "Loser Person Addr Dedup Test")
        # One address_id shared by both; loser also has a unique one.
        shared_aid = generate_id()
        await conn.execute(
            "INSERT INTO addresses (id, raw_input, country) VALUES ($1, '100 Shared Ave', 'US')",
            shared_aid,
        )
        for pid in (winner_id, loser_id):
            await conn.execute(
                "INSERT INTO entity_addresses"
                " (id, entity_type, entity_id, address_id, address_type)"
                " VALUES ($1, 'person', $2, $3, 'mailing')",
                generate_id(),
                pid,
                shared_aid,
            )
        unique_aid = generate_id()
        await conn.execute(
            "INSERT INTO addresses (id, raw_input, country) VALUES ($1, '200 Unique Rd', 'US')",
            unique_aid,
        )
        await conn.execute(
            "INSERT INTO entity_addresses"
            " (id, entity_type, entity_id, address_id, address_type)"
            " VALUES ($1, 'person', $2, $3, 'physical')",
            generate_id(),
            loser_id,
            unique_aid,
        )

    try:
        response = client.post(
            f"/admin/people/{winner_id}/merge/{loser_id}/",
            headers=AUTH_HEADERS,
            follow_redirects=False,
        )
        assert response.status_code == 303

        async with db_pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT address_id, address_type FROM entity_addresses"
                " WHERE entity_type='person' AND entity_id=$1",
                winner_id,
            )
        # Exactly 2: shared (mailing) deduplicated to one + unique (physical).
        assert len(rows) == 2
        addr_ids = {r["address_id"] for r in rows}
        assert shared_aid in addr_ids
        assert unique_aid in addr_ids
    finally:
        async with db_pool.acquire() as conn:
            await conn.execute(
                "DELETE FROM entity_addresses WHERE entity_type='person' AND entity_id=$1",
                winner_id,
            )
            await conn.execute(
                "DELETE FROM addresses WHERE id = ANY($1::text[])",
                [shared_aid, unique_aid],
            )
            for pid in (winner_id, loser_id):
                await conn.execute("DELETE FROM person_names WHERE person_id=$1", pid)
                await conn.execute("DELETE FROM people WHERE id=$1", pid)


# ---------------------------------------------------------------------------
# contact_methods dedup during merge (#232 CR follow-up)
# ---------------------------------------------------------------------------


async def _add_person_contact(conn, person_id: str, value: str) -> str:
    cm_id = generate_id()
    await conn.execute(
        "INSERT INTO contact_methods"
        " (id, entity_type, entity_id, contact_type, value)"
        " VALUES ($1, 'person', $2, 'phone', $3)",
        cm_id,
        person_id,
        value,
    )
    return cm_id


async def test_merge_reassigns_unique_person_contact(client, db_pool):
    """Loser's contact method not on winner is transferred after merge."""
    async with db_pool.acquire() as conn:
        winner_id = await _make_person(conn, "Winner Person CM Test")
        loser_id = await _make_person(conn, "Loser Person CM Test")
        await _add_person_contact(conn, loser_id, "+13605550001")

    try:
        response = client.post(
            f"/admin/people/{winner_id}/merge/{loser_id}/",
            headers=AUTH_HEADERS,
            follow_redirects=False,
        )
        assert response.status_code == 303

        async with db_pool.acquire() as conn:
            count = await conn.fetchval(
                "SELECT count(*) FROM contact_methods WHERE entity_type='person' AND entity_id=$1",
                winner_id,
            )
        assert count == 1
    finally:
        async with db_pool.acquire() as conn:
            await conn.execute(
                "DELETE FROM contact_methods WHERE entity_type='person' AND entity_id=$1",
                winner_id,
            )
            for pid in (winner_id, loser_id):
                await conn.execute("DELETE FROM person_names WHERE person_id=$1", pid)
                await conn.execute("DELETE FROM people WHERE id=$1", pid)


async def test_merge_deduplicates_shared_person_contact(client, db_pool):
    """Shared contact (same type+value) on both people yields exactly one row on winner."""
    async with db_pool.acquire() as conn:
        winner_id = await _make_person(conn, "Winner Person CM Dedup Test")
        loser_id = await _make_person(conn, "Loser Person CM Dedup Test")
        await _add_person_contact(conn, winner_id, "+13605550100")
        await _add_person_contact(conn, loser_id, "+13605550100")
        await _add_person_contact(conn, loser_id, "+13605550200")

    try:
        response = client.post(
            f"/admin/people/{winner_id}/merge/{loser_id}/",
            headers=AUTH_HEADERS,
            follow_redirects=False,
        )
        assert response.status_code == 303

        async with db_pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT value FROM contact_methods WHERE entity_type='person' AND entity_id=$1",
                winner_id,
            )
        assert len(rows) == 2
        assert {r["value"] for r in rows} == {"+13605550100", "+13605550200"}
    finally:
        async with db_pool.acquire() as conn:
            await conn.execute(
                "DELETE FROM contact_methods WHERE entity_type='person' AND entity_id=$1",
                winner_id,
            )
            for pid in (winner_id, loser_id):
                await conn.execute("DELETE FROM person_names WHERE person_id=$1", pid)
                await conn.execute("DELETE FROM people WHERE id=$1", pid)


async def test_person_merge_entity_changes_records_merged_into(client, db_pool):
    """POST merge writes a deleted entity_changes row with merged_into=winner_id."""
    async with db_pool.acquire() as conn:
        winner_id = await _make_person(conn, "Merged-Into Winner Person")
        loser_id = await _make_person(conn, "Merged-Into Loser Person")
        before_seq = await conn.fetchval("SELECT COALESCE(MAX(id), 0) FROM entity_changes")

    try:
        response = client.post(
            f"/admin/people/{winner_id}/merge/{loser_id}/",
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
                "DELETE FROM deleted_entities WHERE entity_type='person' AND entity_id=$1",
                loser_id,
            )
            await conn.execute("DELETE FROM person_names WHERE person_id=$1", winner_id)
            await conn.execute("DELETE FROM people WHERE id=$1", winner_id)
