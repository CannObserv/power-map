"""Integration tests for people duplicate detection and dismiss routes."""

import json

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient

from src.api.admin.people_dups import get_person_dup_count
from src.api.main import app
from src.core.db import generate_id

pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
]

AUTH_HEADERS = {
    "X-ExeDev-UserID": "usr_test",
    "X-ExeDev-Email": "admin@test.com",
}
HTMX_HEADERS = {**AUTH_HEADERS, "HX-Request": "true"}


@pytest.fixture
def client_with_person_dups():
    """Client with person_dup_count forced to 3 via dependency override."""
    app.dependency_overrides[get_person_dup_count] = lambda: 3
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


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


async def test_people_list_shows_duplicate_banner(client, person_pair):
    response = client.get("/admin/people/", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert "possible duplicate" in response.text.lower()


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
