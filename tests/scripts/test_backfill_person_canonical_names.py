"""Integration tests for scripts/backfill_person_canonical_names.py (issue #308c)."""

import pytest
import pytest_asyncio

from scripts.backfill_person_canonical_names import find_candidates, run_backfill
from src.core.db import generate_id

pytestmark = [
    pytest.mark.integration,
]


@pytest_asyncio.fixture(loop_scope="session")
async def conn(db_pool):
    """Pool-acquired connection wrapped in a rolled-back transaction."""
    async with db_pool.acquire() as c:
        tr = c.transaction()
        await tr.start()
        try:
            yield c
        finally:
            await tr.rollback()


async def _person(conn):
    pid = generate_id()
    await conn.execute("INSERT INTO people (id) VALUES ($1)", pid)
    return pid


async def _name(conn, pid, name, *, name_type="legal", is_canonical=False, visibility="public"):
    nid = generate_id()
    await conn.execute(
        "INSERT INTO person_names (id, person_id, name, name_type, visibility, is_canonical)"
        " VALUES ($1, $2, $3, $4, $5, $6)",
        nid,
        pid,
        name,
        name_type,
        visibility,
        is_canonical,
    )
    return nid


async def _canonical_names(conn, pid):
    return {
        r["name"]: r["is_canonical"]
        for r in await conn.fetch(
            "SELECT name, is_canonical FROM person_names WHERE person_id=$1", pid
        )
    }


async def test_find_candidates_picks_sole_uncanonical_public_name(conn):
    pid = await _person(conn)
    await _name(conn, pid, "Steve Kirby")
    cands = await find_candidates(conn)
    assert [c.person_id for c in cands if c.person_id == pid] == [pid]


async def test_find_candidates_skips_person_with_canonical(conn):
    pid = await _person(conn)
    await _name(conn, pid, "Has Canonical", is_canonical=True)
    cands = await find_candidates(conn)
    assert pid not in {c.person_id for c in cands}


async def test_find_candidates_skips_person_with_no_names(conn):
    pid = await _person(conn)
    cands = await find_candidates(conn)
    assert pid not in {c.person_id for c in cands}


async def test_find_candidates_skips_non_public_only_person(conn):
    """Nothing public to promote — leave it for a human."""
    pid = await _person(conn)
    await _name(conn, pid, "Old Name", name_type="deadname", visibility="legal_only")
    cands = await find_candidates(conn)
    assert pid not in {c.person_id for c in cands}


async def test_find_candidates_resolves_multi_name_deterministically(conn):
    """>1 eligible public name → resolved by the display ladder, not skipped.

    Superseded the earlier "skip ambiguous" policy (#308, CR3 #26): the heal pass
    promotes the top-priority name on the next observation anyway, so deferring
    only hid the decision from the operator.
    """
    pid = await _person(conn)
    await _name(conn, pid, "Alice Smith", name_type="legal")
    await _name(conn, pid, "Alice Jones", name_type="alias")
    cand = next(c for c in await find_candidates(conn) if c.person_id == pid)
    assert cand.name_type == "legal"  # legal outranks alias


async def test_find_candidates_excludes_machine_readable_name_types(conn):
    """An mrz-only person is not a display candidate."""
    pid = await _person(conn)
    await _name(conn, pid, "YAMADA<<TARO", name_type="mrz")
    cands = await find_candidates(conn)
    assert pid not in {c.person_id for c in cands}


async def test_run_backfill_dry_run_makes_no_change(conn):
    pid = await _person(conn)
    await _name(conn, pid, "Greg Cheney")
    stats = await run_backfill(conn, dry_run=True)
    assert stats.promoted >= 1
    assert stats.dry_run is True
    assert (await _canonical_names(conn, pid))["Greg Cheney"] is False


async def test_run_backfill_execute_promotes(conn):
    pid = await _person(conn)
    await _name(conn, pid, "Kevin Waters")
    stats = await run_backfill(conn, dry_run=False)
    assert stats.promoted >= 1
    assert (await _canonical_names(conn, pid))["Kevin Waters"] is True


async def test_run_backfill_execute_makes_person_render(conn):
    pid = await _person(conn)
    await _name(conn, pid, "Tina Orwall")
    await run_backfill(conn, dry_run=False)
    rows = await conn.fetch(
        "SELECT display_name FROM v_person_display_names WHERE person_id=$1", pid
    )
    assert len(rows) == 1
    assert rows[0]["display_name"] == "Tina Orwall"


async def test_run_backfill_is_idempotent(conn):
    """A second run must not touch this person again (#308, CR3 #24).

    Asserted person-scoped rather than on the global `promoted` count, which is
    shared-DB state — the previous form's `or` clause was already established by
    the line above it and could never fail.
    """
    pid = await _person(conn)
    await _name(conn, pid, "Steve Kirby")
    await run_backfill(conn, dry_run=False)
    first_state = await _canonical_names(conn, pid)
    second = await run_backfill(conn, dry_run=False)
    assert pid not in {c.person_id for c in await find_candidates(conn)}
    assert pid not in second.promoted_ids
    assert await _canonical_names(conn, pid) == first_state
    assert (
        await conn.fetchval(
            "SELECT count(*) FROM person_names WHERE person_id=$1 AND is_canonical", pid
        )
        == 1
    )


async def test_run_backfill_emits_entity_change_for_subscribers(conn):
    """Promotion must reach the outbox so subscribers re-fetch and see the name.

    Asserts on entity_changes rather than people.updated_at: NOW() is fixed for
    the life of a transaction, so the timestamp cannot advance inside the test's
    own transaction even though the touch trigger does fire.
    """
    pid = await _person(conn)
    await _name(conn, pid, "Steve Kirby")
    before = await conn.fetchval(
        "SELECT count(*) FROM entity_changes WHERE entity_id=$1 AND change_kind='updated'",
        pid,
    )
    await run_backfill(conn, dry_run=False)
    after = await conn.fetchval(
        "SELECT count(*) FROM entity_changes WHERE entity_id=$1 AND change_kind='updated'",
        pid,
    )
    assert after > before


# The `blocked` bucket is gone (#308, Option A): uq_person_canonical_name is
# keyed on (person_id) and chk_person_canonical_is_public guarantees a canonical
# row is visible, so a non-public row can no longer occupy a person's display
# slot. tests/core/test_schema_person_canonical.py asserts the constraint fires.


async def test_find_candidates_resolves_multi_name_by_priority(conn):
    pid = await _person(conn)
    await _name(conn, pid, "Alice Smith", name_type="legal")
    await _name(conn, pid, "Alice", name_type="preferred")
    cand = next(c for c in await find_candidates(conn) if c.person_id == pid)
    assert cand.name == "Alice"
    assert cand.name_type == "preferred"


async def test_run_backfill_promotes_priority_winner(conn):
    pid = await _person(conn)
    await _name(conn, pid, "Alice Smith", name_type="legal")
    await _name(conn, pid, "Alice", name_type="preferred")
    await run_backfill(conn, dry_run=False)
    assert (
        await conn.fetchval(
            "SELECT display_name FROM v_person_display_names WHERE person_id=$1", pid
        )
        == "Alice"
    )


async def test_backfill_matches_heal_choice(conn):
    """The two repair paths must pick the same row for the same person."""
    from src.core.observation import _heal_person_canonical

    names = [("Zed Legal", "legal"), ("Ann Alias", "alias"), ("Pat Preferred", "preferred")]
    pid_backfill = await _person(conn)
    pid_heal = await _person(conn)
    for pid in (pid_backfill, pid_heal):
        for nm, nt in names:
            await _name(conn, pid, nm, name_type=nt)
    await run_backfill(conn, dry_run=False)
    await _heal_person_canonical(conn, pid_heal)
    q = "SELECT display_name FROM v_person_display_names WHERE person_id=$1"
    assert await conn.fetchval(q, pid_backfill) == await conn.fetchval(q, pid_heal)


async def test_backfill_leaves_at_most_one_canonical_per_person(conn):
    pid = await _person(conn)
    await _name(conn, pid, "Alice Smith", name_type="legal")
    await _name(conn, pid, "Alice", name_type="preferred")
    await run_backfill(conn, dry_run=False)
    assert (
        await conn.fetchval(
            "SELECT count(*) FROM person_names WHERE person_id=$1 AND is_canonical", pid
        )
        == 1
    )
