"""Re-pointing anchors after a merge (#514).

The crosswalk stores each anchor's *resolved* PM id, so a merge that retires a
row leaves every anchor naming it pointing at nothing until something re-points
it. The seed would re-walk `deleted_entities` to find the survivor, but that
walk forgets after the tombstone TTL and the applier reads the stored id, not
the walk. `repoint_anchors` writes the walk's answer at merge time.
"""

import pytest
import pytest_asyncio

from src.core.db import generate_id
from src.core.ingestion.crosswalk import PRODUCER_SOURCE, repoint_anchors

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


async def _person(db, *, archived=False) -> str:
    pid = generate_id()
    await db.execute(
        "INSERT INTO people (id, archived_at) VALUES ($1, CASE WHEN $2 THEN NOW() END)",
        pid,
        archived,
    )
    return pid


async def _anchor(db, kind, pm_id, *, source=PRODUCER_SOURCE, resolution="live", exported=None):
    producer_id = generate_id()
    await db.execute(
        "INSERT INTO producer_crosswalk"
        " (id, source, kind, producer_id, exported_pm_id, pm_id, resolution)"
        " VALUES ($1, $2, $3, $4, $5, $6, $7)",
        generate_id(),
        source,
        kind,
        producer_id,
        exported or pm_id,
        pm_id,
        resolution,
    )
    return producer_id


async def _state(db, producer_id):
    row = await db.fetchrow(
        "SELECT exported_pm_id, pm_id, resolution FROM producer_crosswalk WHERE producer_id=$1",
        producer_id,
    )
    return tuple(row)


async def test_every_source_anchoring_the_loser_follows_it_to_the_survivor(db):
    loser, winner = await _person(db), await _person(db)
    ours = await _anchor(db, "person", loser)
    theirs = await _anchor(db, "person", loser, source="another_producer")

    assert await repoint_anchors(db, "person", [(loser, winner)]) == 2

    # exported_pm_id is the producer's word and stays; pm_id is where it resolves now.
    assert await _state(db, ours) == (loser, winner, "merged")
    assert await _state(db, theirs) == (loser, winner, "merged")


async def test_an_anchor_already_merged_onto_the_loser_follows_the_chain(db):
    older, loser, winner = generate_id(), await _person(db), await _person(db)
    chained = await _anchor(db, "person", loser, resolution="merged", exported=older)

    await repoint_anchors(db, "person", [(loser, winner)])

    assert await _state(db, chained) == (older, winner, "merged")


async def test_an_archived_survivor_resolves_archived(db):
    """The seed's own rule: a walk ending on an archived row is `archived`, not `merged`."""
    loser, winner = await _person(db), await _person(db, archived=True)
    anchor = await _anchor(db, "person", loser)

    await repoint_anchors(db, "person", [(loser, winner)])

    assert await _state(db, anchor) == (loser, winner, "archived")


async def test_anchors_of_another_kind_and_other_rows_are_untouched(db):
    loser, winner, bystander = await _person(db), await _person(db), await _person(db)
    other_kind = await _anchor(db, "organization", loser)
    unrelated = await _anchor(db, "person", bystander)

    assert await repoint_anchors(db, "person", [(loser, winner)]) == 0
    assert await _state(db, other_kind) == (loser, loser, "live")
    assert await _state(db, unrelated) == (bystander, bystander, "live")


async def test_no_pairs_is_a_noop(db):
    assert await repoint_anchors(db, "person", []) == 0


async def test_rejects_a_kind_the_crosswalk_does_not_hold(db):
    """The crosswalk says `assignment`; `role_assignment` is the tombstone vocabulary."""
    with pytest.raises(ValueError, match="role_assignment"):
        await repoint_anchors(db, "role_assignment", [("a", "b")])
