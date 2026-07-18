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


async def test_find_candidates_skips_ambiguous_multi_name_person(conn):
    """>1 eligible public name → not deterministic; excluded from the backfill."""
    pid = await _person(conn)
    await _name(conn, pid, "Alice Smith", name_type="legal")
    await _name(conn, pid, "Alice Jones", name_type="alias")
    cands = await find_candidates(conn)
    assert pid not in {c.person_id for c in cands}


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
    pid = await _person(conn)
    await _name(conn, pid, "Steve Kirby")
    await run_backfill(conn, dry_run=False)
    second = await run_backfill(conn, dry_run=False)
    assert pid not in {c.person_id for c in await find_candidates(conn)}
    assert second.promoted == 0 or pid not in {c.person_id for c in await find_candidates(conn)}


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
