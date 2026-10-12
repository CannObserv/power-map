"""Walking an id through ``deleted_entities`` merge history (#495, #607).

The detail GETs' ``410`` + ``merged_into`` (``src/api/public/gone.py``) walk every
retired id through here. The edge cases that must **not** silently resolve — a
tombstone with no successor, an id PM never heard of, a cycle — were pinned
through the crosswalk seed's wrapper until that retired (#502); they are pinned
on the walk itself now, keyed by tombstone ``entity_type``.
"""

import pytest
import pytest_asyncio

from src.core.db import generate_id
from src.core.merge_history import Resolution, fetch_tombstone, walk_merge_history

pytestmark = [pytest.mark.integration]


@pytest_asyncio.fixture(loop_scope="session")
async def db(db_pool):
    async with db_pool.acquire() as conn:
        tr = conn.transaction()
        await tr.start()
        try:
            yield conn
        finally:
            await tr.rollback()


async def _tombstone(db, entity_type: str, entity_id: str, merged_into: str | None) -> None:
    await db.execute(
        "INSERT INTO deleted_entities (entity_type, entity_id, merged_into) VALUES ($1,$2,$3)",
        entity_type,
        entity_id,
        merged_into,
    )


async def _person(db, *, archived: bool = False) -> str:
    pid = generate_id()
    await db.execute("INSERT INTO people (id) VALUES ($1)", pid)
    if archived:
        await db.execute("UPDATE people SET archived_at = NOW() WHERE id = $1", pid)
    return pid


async def test_a_live_id_resolves_to_itself(db):
    pid = await _person(db)
    assert await walk_merge_history(db, "person", pid) == Resolution("live", pid)


async def test_a_two_hop_merge_resolves_to_the_final_survivor(db):
    """A→B→C: the caller holds A, and only C still exists."""
    final = await _person(db)
    middle, first = generate_id(), generate_id()
    await _tombstone(db, "person", middle, final)
    await _tombstone(db, "person", first, middle)

    assert await walk_merge_history(db, "person", first) == Resolution("merged", final)


async def test_a_tombstone_without_a_successor_does_not_resolve(db):
    """A bare delete names no survivor; re-pointing it would be invention."""
    dropped = generate_id()
    await _tombstone(db, "person", dropped, None)

    assert await walk_merge_history(db, "person", dropped) == Resolution(
        "deleted_no_successor", None
    )


async def test_an_unknown_id_is_missing_not_live(db):
    """No row and no tombstone: PM cannot say whether it was merged or never existed."""
    assert await walk_merge_history(db, "person", generate_id()) == Resolution("missing", None)


async def test_an_archived_row_resolves_but_is_flagged(db):
    pid = await _person(db, archived=True)

    assert await walk_merge_history(db, "person", pid) == Resolution("archived", pid)


async def test_a_chain_ending_on_an_archived_row_is_flagged_archived(db):
    survivor = await _person(db, archived=True)
    loser = generate_id()
    await _tombstone(db, "person", loser, survivor)

    assert await walk_merge_history(db, "person", loser) == Resolution("archived", survivor)


async def test_a_cycle_terminates(db):
    """A→B and B→A cannot arise from a correct merge, but a detail GET must not hang."""
    a, b = generate_id(), generate_id()
    await _tombstone(db, "person", a, b)
    await _tombstone(db, "person", b, a)

    assert await walk_merge_history(db, "person", a) == Resolution("cycle", None)


async def test_walk_is_keyed_by_tombstone_type(db):
    """``role_assignment`` is a tombstone type the crosswalk calls ``assignment``."""
    person, org, role, ra = generate_id(), generate_id(), generate_id(), generate_id()
    await db.execute("INSERT INTO people (id) VALUES ($1)", person)
    await db.execute("INSERT INTO organizations (id) VALUES ($1)", org)
    await db.execute("INSERT INTO roles (id, organization_id, title) VALUES ($1,$2,'T')", role, org)
    await db.execute(
        "INSERT INTO role_assignments (id, person_id, role_id) VALUES ($1,$2,$3)", ra, person, role
    )
    loser = generate_id()
    await _tombstone(db, "role_assignment", loser, ra)

    assert await walk_merge_history(db, "role_assignment", loser) == Resolution("merged", ra)


async def test_walk_rejects_an_unknown_type(db):
    with pytest.raises(ValueError, match="unknown entity type"):
        await walk_merge_history(db, "role_assignment_relationship", generate_id())


async def test_fetch_tombstone_returns_the_row_or_none(db):
    gone = generate_id()
    await _tombstone(db, "jurisdiction", gone, None)

    row = await fetch_tombstone(db, "jurisdiction", gone)

    assert row is not None and row["merged_into"] is None and row["deleted_at"] is not None
    assert await fetch_tombstone(db, "jurisdiction", generate_id()) is None
