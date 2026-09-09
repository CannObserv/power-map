"""Resolving a producer anchor through PM's merge history (#495, safeguard 1).

usa-wa's anchors name PM ids captured before merges that PM alone knows about.
Resolution walks `deleted_entities.merged_into` — whose target is *that row's*
survivor, not the parent merge's winner (`src/core/merge_signals.py`).

The cases that matter are the ones that must **not** silently resolve: a
tombstone with no successor, and an id PM has never heard of. Both belong on the
blocking report. Guessing there is how a seed mints duplicates.
"""

import pytest
import pytest_asyncio

from src.core.db import generate_id
from src.core.ingestion.crosswalk import Resolution, resolve_anchor

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


async def _person(db, *, archived: bool = False) -> str:
    pid = generate_id()
    await db.execute("INSERT INTO people (id) VALUES ($1)", pid)
    if archived:
        await db.execute("UPDATE people SET archived_at = NOW() WHERE id = $1", pid)
    return pid


async def _tombstone(db, entity_type: str, entity_id: str, merged_into: str | None) -> None:
    await db.execute(
        "INSERT INTO deleted_entities (entity_type, entity_id, merged_into) VALUES ($1,$2,$3)",
        entity_type,
        entity_id,
        merged_into,
    )


async def test_a_live_id_resolves_to_itself(db):
    pid = await _person(db)
    assert await resolve_anchor(db, "person", pid) == Resolution("live", pid)


async def test_a_merged_id_resolves_to_its_survivor(db):
    survivor = await _person(db)
    loser = generate_id()
    await _tombstone(db, "person", loser, survivor)

    assert await resolve_anchor(db, "person", loser) == Resolution("merged", survivor)


async def test_a_two_hop_merge_resolves_to_the_final_survivor(db):
    """A→B→C: the anchor holds A, and only C still exists."""
    final = await _person(db)
    middle, first = generate_id(), generate_id()
    await _tombstone(db, "person", middle, final)
    await _tombstone(db, "person", first, middle)

    assert await resolve_anchor(db, "person", first) == Resolution("merged", final)


async def test_a_tombstone_without_a_successor_is_unresolvable(db):
    """32 of prod's 43 tombstones are bare deletes; re-pointing them is invention."""
    dropped = generate_id()
    await _tombstone(db, "person", dropped, None)

    assert await resolve_anchor(db, "person", dropped) == Resolution("deleted_no_successor", None)


async def test_an_unknown_id_is_missing_not_live(db):
    """No row and no tombstone — PM cannot say whether it was merged or never existed.

    `deleted_entities` is pruned at 90 days (`scripts/prune_outbox.py`), so an
    anchor broken by an older merge lands here. 'missing' must never be read as
    'never existed'.
    """
    assert await resolve_anchor(db, "person", generate_id()) == Resolution("missing", None)


async def test_an_archived_survivor_resolves_but_is_flagged(db):
    """#481: writing onto a soft-deleted row is the failure this flag exists to stop."""
    pid = await _person(db, archived=True)

    assert await resolve_anchor(db, "person", pid) == Resolution("archived", pid)


async def test_a_chain_ending_on_an_archived_row_is_flagged_archived(db):
    """The chain resolved, but its destination is still not safe to write onto."""
    survivor = await _person(db, archived=True)
    loser = generate_id()
    await _tombstone(db, "person", loser, survivor)

    assert await resolve_anchor(db, "person", loser) == Resolution("archived", survivor)


async def test_a_cycle_does_not_hang(db):
    """A→B and B→A cannot arise from a correct merge, but the walk must still terminate."""
    a, b = generate_id(), generate_id()
    await _tombstone(db, "person", a, b)
    await _tombstone(db, "person", b, a)

    assert await resolve_anchor(db, "person", a) == Resolution("cycle", None)


async def test_assignment_kind_walks_the_role_assignment_tombstones(db):
    """The anchor's `kind` vocabulary is usa-wa's; PM's tombstone type is not the same word."""
    pid = await _person(db)
    role_id, org_id = generate_id(), generate_id()
    await db.execute("INSERT INTO organizations (id) VALUES ($1)", org_id)
    await db.execute(
        "INSERT INTO roles (id, organization_id, title) VALUES ($1,$2,'Member')", role_id, org_id
    )
    survivor = generate_id()
    await db.execute(
        "INSERT INTO role_assignments (id, person_id, role_id) VALUES ($1,$2,$3)",
        survivor,
        pid,
        role_id,
    )
    loser = generate_id()
    await _tombstone(db, "role_assignment", loser, survivor)

    assert await resolve_anchor(db, "assignment", loser) == Resolution("merged", survivor)
