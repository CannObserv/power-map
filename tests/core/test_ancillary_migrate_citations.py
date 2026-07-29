"""Citation re-homing + orphan counting during merges (#319).

Citations are polymorphic no-FK ancillary across all seven citable entity types,
so a merge that collapses an entity must re-home its citations onto the survivor
(NULL-safe identity dedup) or they orphan. Covers migrate_citations directly plus
its wiring into the role_assignment/role re-home helpers, and the orphan audit.
"""

import pytest
import pytest_asyncio

from src.core.ancillary_migrate import (
    count_orphaned_citations,
    delete_citations,
    delete_event_citations_for_owner,
    migrate_citations,
    rehome_citations,
    rehome_conflicting_assignment_ancillary,
    rehome_role_ancillary,
)
from src.core.db import generate_id

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


async def _person(db) -> str:
    pid = generate_id()
    await db.execute("INSERT INTO people (id) VALUES ($1)", pid)
    return pid


async def _cite(
    db, entity_type, entity_id, *, url="https://s/x", field=None, archived=False
) -> str:
    cid = generate_id()
    await db.execute(
        "INSERT INTO citations (id, entity_type, entity_id, field_name, url, title, archived_at)"
        " VALUES ($1,$2,$3,$4,$5,'t', CASE WHEN $6 THEN now() ELSE NULL END)",
        cid,
        entity_type,
        entity_id,
        field,
        url,
        archived,
    )
    return cid


async def _count(db, entity_id) -> int:
    return await db.fetchval("SELECT count(*) FROM citations WHERE entity_id=$1", entity_id)


async def _owner(db, cid) -> str:
    return await db.fetchval("SELECT entity_id FROM citations WHERE id=$1", cid)


# ── migrate_citations ─────────────────────────────────────────────────────────


async def test_repoint_moves_citation(db):
    loser, winner = await _person(db), await _person(db)
    cid = await _cite(db, "person", loser, url="https://s/a", field="notes")
    moved, deduped = await migrate_citations(db, "person", loser, winner)
    assert (moved, deduped) == (1, 0)
    assert await _owner(db, cid) == winner


async def test_active_twin_dedups(db):
    loser, winner = await _person(db), await _person(db)
    await _cite(db, "person", loser, url="https://s/a", field="notes")
    await _cite(db, "person", winner, url="https://s/a", field="notes")  # active twin
    moved, deduped = await migrate_citations(db, "person", loser, winner)
    assert (moved, deduped) == (0, 1)
    assert await _count(db, loser) == 0
    assert await _count(db, winner) == 1  # survivor keeps its one


async def test_urlless_twin_dedups_via_nulls_not_distinct(db):
    loser, winner = await _person(db), await _person(db)
    await _cite(db, "person", loser, url=None, field="notes")
    await _cite(db, "person", winner, url=None, field="notes")
    moved, deduped = await migrate_citations(db, "person", loser, winner)
    assert (moved, deduped) == (0, 1)


async def test_archived_loser_always_repoints(db):
    loser, winner = await _person(db), await _person(db)
    # Winner has an active twin, but the loser row is archived → no active-index
    # collision, so it re-points rather than dedups.
    await _cite(db, "person", winner, url="https://s/a", field="notes")
    cid = await _cite(db, "person", loser, url="https://s/a", field="notes", archived=True)
    moved, deduped = await migrate_citations(db, "person", loser, winner)
    assert (moved, deduped) == (1, 0)
    assert await _owner(db, cid) == winner


async def test_repoint_signals_survivor(db):
    loser, winner = await _person(db), await _person(db)
    await _cite(db, "person", loser, field="notes")
    before = await db.fetchval(
        "SELECT count(*) FROM entity_changes WHERE entity_type='person'"
        " AND entity_id=$1 AND change_kind='updated'",
        winner,
    )
    await rehome_citations(db, "person", [(loser, winner)])
    after = await db.fetchval(
        "SELECT count(*) FROM entity_changes WHERE entity_type='person'"
        " AND entity_id=$1 AND change_kind='updated'",
        winner,
    )
    assert after == before + 1  # touch trigger self-emits on re-point


# ── wiring into role_assignment / role re-home ────────────────────────────────


async def _role_assignment_pair(db) -> tuple[str, str]:
    """Two distinct assignments (separate roles to dodge the (person,role,start)
    unique index); the re-home helper just needs a (loser, winner) id pair."""
    oid, r1, r2, pid = (generate_id() for _ in range(4))
    await db.execute("INSERT INTO organizations (id) VALUES ($1)", oid)
    await db.execute(
        "INSERT INTO roles (id, organization_id, title) VALUES ($1,$2,'A'),($3,$2,'B')",
        r1,
        oid,
        r2,
    )
    await db.execute("INSERT INTO people (id) VALUES ($1)", pid)
    a, b = generate_id(), generate_id()
    for aid, rid in ((a, r1), (b, r2)):
        await db.execute(
            "INSERT INTO role_assignments (id, person_id, role_id, is_current)"
            " VALUES ($1,$2,$3,TRUE)",
            aid,
            pid,
            rid,
        )
    return a, b


async def test_assignment_rehome_includes_citations(db):
    loser, winner = await _role_assignment_pair(db)
    cid = await _cite(db, "role_assignment", loser, field="start_date")
    result = await rehome_conflicting_assignment_ancillary(db, [(loser, winner)])
    assert result["citations"] == (1, 0)
    assert await _owner(db, cid) == winner


async def test_role_rehome_includes_citations(db):
    oid = generate_id()
    await db.execute("INSERT INTO organizations (id) VALUES ($1)", oid)
    loser, winner = generate_id(), generate_id()
    await db.execute(
        "INSERT INTO roles (id, organization_id, title) VALUES ($1,$2,'A'),($3,$2,'B')",
        loser,
        oid,
        winner,
    )
    cid = await _cite(db, "role", loser, field="title")
    result = await rehome_role_ancillary(db, loser, winner)
    assert result["citations"] == (1, 0)
    assert await _owner(db, cid) == winner


# ── orphan audit ──────────────────────────────────────────────────────────────


async def test_count_orphaned_citations(db):
    # A citation pointing at a non-existent person is an orphan.
    await _cite(db, "person", generate_id(), field="notes")
    counts = await count_orphaned_citations(db)
    assert counts["person"] >= 1
    # An existing target is not counted.
    live = await _person(db)
    await _cite(db, "person", live, url="https://s/live", field="notes")
    counts2 = await count_orphaned_citations(db)
    assert counts2["person"] == counts["person"]  # unchanged by the live one


# ── delete helpers (sub-entity removal without a survivor) ────────────────────


async def test_delete_citations_removes_all_for_entity(db):
    person = await _person(db)
    await _cite(db, "person", person, url="https://s/a", field="notes")
    await _cite(db, "person", person, url="https://s/b", field="notes")
    await delete_citations(db, "person", person)
    assert await _count(db, person) == 0


async def test_delete_event_citations_for_owner(db):
    oid, eid = generate_id(), generate_id()
    await db.execute("INSERT INTO organizations (id) VALUES ($1)", oid)
    await db.execute(
        "INSERT INTO entity_events (id, entity_type, entity_id, event_type_id)"
        " VALUES ($1,'organization',$2,(SELECT id FROM entity_event_types LIMIT 1))",
        eid,
        oid,
    )
    await _cite(db, "entity_event", eid, url="https://s/evt")
    await delete_event_citations_for_owner(db, "organization", oid)
    assert await _count(db, eid) == 0
