"""Entity-event re-homing during person/org merges (#611).

A merge hard-deletes the loser, and ``entity_events`` names it two ways with no
FK: as the event's owner (``entity_id``) and as another event's link target
(``linked_entity_id``). ``rehome_entity_events`` re-points both onto the
survivor, so neither kind dangles, and settles the three collisions a re-point
can cause: a content twin, a self-link, and a second active succession edge.
"""

from datetime import UTC, datetime

import pytest
import pytest_asyncio

from src.core.ancillary_migrate import rehome_entity_events
from src.core.db import generate_id

pytestmark = [pytest.mark.integration]

FOUNDED = "01KV0000000000000000000006"
MERGED_WITH = "01KV0000000000000000000008"
SUCCEEDED_BY = "01KV000000000000000000000C"
MARRIAGE = "01KV0000000000000000000003"
JAN = datetime(2026, 1, 1, tzinfo=UTC)
FEB = datetime(2026, 2, 1, tzinfo=UTC)


@pytest_asyncio.fixture(loop_scope="session")
async def db(db_pool):
    async with db_pool.acquire() as conn:
        tr = conn.transaction()
        await tr.start()
        try:
            yield conn
        finally:
            await tr.rollback()


async def _org(db) -> str:
    oid = generate_id()
    await db.execute("INSERT INTO organizations (id) VALUES ($1)", oid)
    return oid


async def _person(db) -> str:
    pid = generate_id()
    await db.execute("INSERT INTO people (id) VALUES ($1)", pid)
    return pid


async def _event(
    db,
    owner,
    event_type_id,
    *,
    entity_type="organization",
    year=None,
    linked=None,
    archived=False,
    created=JAN,
) -> str:
    """Insert one event; ``created`` pins ``created_at`` (NOW() is constant in a txn)."""
    eid = generate_id()
    await db.execute(
        """INSERT INTO entity_events
               (id, entity_type, entity_id, event_type_id, event_year,
                linked_entity_type, linked_entity_id, archived_at, created_at)
           VALUES ($1, $2, $3, $4, $5,
                   CASE WHEN $6::text IS NULL THEN NULL ELSE $2 END, $6,
                   CASE WHEN $7 THEN NOW() END, $8)""",
        eid,
        entity_type,
        owner,
        event_type_id,
        year,
        linked,
        archived,
        created,
    )
    return eid


async def _cite(db, event_id, url) -> str:
    cid = generate_id()
    await db.execute(
        "INSERT INTO citations (id, entity_type, entity_id, url, title)"
        " VALUES ($1, 'entity_event', $2, $3, 't')",
        cid,
        event_id,
        url,
    )
    return cid


async def _row(db, event_id):
    return await db.fetchrow(
        "SELECT entity_id, linked_entity_id, archived_at FROM entity_events WHERE id=$1",
        event_id,
    )


async def _naming(db, entity_id) -> int:
    """Events that still name ``entity_id`` either way — must be 0 for a loser."""
    return await db.fetchval(
        "SELECT count(*) FROM entity_events WHERE entity_id=$1 OR linked_entity_id=$1",
        entity_id,
    )


# ── plain re-points ───────────────────────────────────────────────────────────


async def test_owned_event_moves_with_its_citation(db):
    loser, winner = await _org(db), await _org(db)
    eid = await _event(db, loser, FOUNDED, year=1990)
    cid = await _cite(db, eid, "https://s/founded")
    assert await rehome_entity_events(db, "organization", [(loser, winner)]) == (1, 0, 0)
    assert (await _row(db, eid))["entity_id"] == winner
    assert await db.fetchval("SELECT entity_id FROM citations WHERE id=$1", cid) == eid
    assert await _naming(db, loser) == 0


async def test_archived_owned_event_moves_too(db):
    loser, winner = await _org(db), await _org(db)
    eid = await _event(db, loser, FOUNDED, year=1990, archived=True)
    await rehome_entity_events(db, "organization", [(loser, winner)])
    row = await _row(db, eid)
    assert row["entity_id"] == winner
    assert row["archived_at"] is not None
    assert await _naming(db, loser) == 0


async def test_inbound_org_link_repoints_and_signals_the_linking_org(db):
    loser, winner, pred = await _org(db), await _org(db), await _org(db)
    eid = await _event(db, pred, SUCCEEDED_BY, linked=loser)
    before = await db.fetchval(
        "SELECT count(*) FROM entity_changes WHERE entity_type='organization'"
        " AND entity_id=$1 AND change_kind='updated'",
        pred,
    )
    assert await rehome_entity_events(db, "organization", [(loser, winner)]) == (1, 0, 0)
    row = await _row(db, eid)
    assert (row["entity_id"], row["linked_entity_id"]) == (pred, winner)
    assert row["archived_at"] is None
    after = await db.fetchval(
        "SELECT count(*) FROM entity_changes WHERE entity_type='organization'"
        " AND entity_id=$1 AND change_kind='updated'",
        pred,
    )
    assert after > before
    assert await _naming(db, loser) == 0


async def test_inbound_person_link_repoints(db):
    loser, winner, spouse = await _person(db), await _person(db), await _person(db)
    eid = await _event(db, spouse, MARRIAGE, entity_type="person", linked=loser)
    await rehome_entity_events(db, "person", [(loser, winner)])
    assert (await _row(db, eid))["linked_entity_id"] == winner
    assert await _naming(db, loser) == 0


async def test_other_type_link_with_same_id_untouched(db):
    """Only links of the merging type re-point; the type column scopes the id."""
    loser, winner = await _person(db), await _person(db)
    org = await _org(db)
    eid = await _event(db, org, FOUNDED, year=1990)
    await rehome_entity_events(db, "person", [(loser, winner)])
    assert (await _row(db, eid))["entity_id"] == org


# ── content twins ─────────────────────────────────────────────────────────────


async def test_owned_twin_collapses_into_survivor_row_keeping_citations(db):
    loser, winner = await _org(db), await _org(db)
    keep = await _event(db, winner, FOUNDED, year=1990)
    drop = await _event(db, loser, FOUNDED, year=1990)
    cid = await _cite(db, drop, "https://s/only-on-loser")
    assert await rehome_entity_events(db, "organization", [(loser, winner)]) == (0, 1, 0)
    assert await _row(db, drop) is None
    assert await db.fetchval("SELECT entity_id FROM citations WHERE id=$1", cid) == keep
    assert await _naming(db, loser) == 0


async def test_twin_with_archived_survivor_row_keeps_the_retraction(db):
    loser, winner = await _org(db), await _org(db)
    keep = await _event(db, winner, FOUNDED, year=1990, archived=True)
    drop = await _event(db, loser, FOUNDED, year=1990)
    await rehome_entity_events(db, "organization", [(loser, winner)])
    assert await _row(db, drop) is None
    assert (await _row(db, keep))["archived_at"] is not None


async def test_different_date_is_not_a_twin(db):
    loser, winner = await _org(db), await _org(db)
    await _event(db, winner, FOUNDED, year=1990)
    moved = await _event(db, loser, FOUNDED, year=1991)
    assert await rehome_entity_events(db, "organization", [(loser, winner)]) == (1, 0, 0)
    assert (await _row(db, moved))["entity_id"] == winner


async def test_third_party_twins_keep_the_row_already_naming_the_survivor(db):
    loser, winner, other = await _org(db), await _org(db), await _org(db)
    keep = await _event(db, other, MERGED_WITH, linked=winner)
    drop = await _event(db, other, MERGED_WITH, linked=loser)
    assert await rehome_entity_events(db, "organization", [(loser, winner)]) == (0, 1, 0)
    assert await _row(db, drop) is None
    assert (await _row(db, keep))["linked_entity_id"] == winner


# ── self-links ────────────────────────────────────────────────────────────────


async def test_survivor_event_linking_loser_becomes_archived_self_link(db):
    loser, winner = await _org(db), await _org(db)
    eid = await _event(db, winner, MERGED_WITH, linked=loser)
    assert await rehome_entity_events(db, "organization", [(loser, winner)]) == (1, 0, 1)
    row = await _row(db, eid)
    assert (row["entity_id"], row["linked_entity_id"]) == (winner, winner)
    assert row["archived_at"] is not None


async def test_loser_event_linking_survivor_becomes_archived_self_link(db):
    loser, winner = await _org(db), await _org(db)
    eid = await _event(db, loser, SUCCEEDED_BY, linked=winner)
    await rehome_entity_events(db, "organization", [(loser, winner)])
    row = await _row(db, eid)
    assert (row["entity_id"], row["linked_entity_id"]) == (winner, winner)
    assert row["archived_at"] is not None
    assert await _naming(db, loser) == 0


# ── succession-edge collisions ────────────────────────────────────────────────


async def test_later_moving_succession_edge_is_archived(db):
    loser, winner, pred = await _org(db), await _org(db), await _org(db)
    earlier = await _event(db, pred, SUCCEEDED_BY, linked=winner, created=JAN)
    later = await _event(db, pred, SUCCEEDED_BY, year=2020, linked=loser, created=FEB)
    assert await rehome_entity_events(db, "organization", [(loser, winner)]) == (1, 0, 1)
    assert (await _row(db, earlier))["archived_at"] is None
    row = await _row(db, later)
    assert row["linked_entity_id"] == winner
    assert row["archived_at"] is not None


async def test_later_standing_succession_edge_is_archived(db):
    loser, winner, pred = await _org(db), await _org(db), await _org(db)
    earlier = await _event(db, pred, SUCCEEDED_BY, year=2020, linked=loser, created=JAN)
    later = await _event(db, pred, SUCCEEDED_BY, linked=winner, created=FEB)
    assert await rehome_entity_events(db, "organization", [(loser, winner)]) == (1, 0, 1)
    row = await _row(db, earlier)
    assert (row["linked_entity_id"], row["archived_at"]) == (winner, None)
    assert (await _row(db, later))["archived_at"] is not None


async def test_owned_succession_edges_collide_on_the_survivor(db):
    loser, winner, succ = await _org(db), await _org(db), await _org(db)
    keep = await _event(db, winner, SUCCEEDED_BY, linked=succ, created=JAN)
    late = await _event(db, loser, SUCCEEDED_BY, year=2020, linked=succ, created=FEB)
    await rehome_entity_events(db, "organization", [(loser, winner)])
    assert (await _row(db, keep))["archived_at"] is None
    row = await _row(db, late)
    assert (row["entity_id"], row["archived_at"] is not None) == (winner, True)


async def test_archived_succession_edge_never_collides(db):
    loser, winner, pred = await _org(db), await _org(db), await _org(db)
    await _event(db, pred, SUCCEEDED_BY, linked=winner, created=JAN)
    retracted = await _event(
        db,
        pred,
        SUCCEEDED_BY,
        year=2020,
        linked=loser,
        archived=True,
        created=FEB,
    )
    assert await rehome_entity_events(db, "organization", [(loser, winner)]) == (1, 0, 0)
    assert (await _row(db, retracted))["linked_entity_id"] == winner


# ── contract ──────────────────────────────────────────────────────────────────


async def test_rejects_a_type_without_events(db):
    with pytest.raises(ValueError, match="role"):
        await rehome_entity_events(db, "role", [("a", "b")])
