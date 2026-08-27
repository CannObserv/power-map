"""Chain-aware duplicate exclusion (#469).

Orgs connected by active `succeeded_by` events form one succession chain — an
equivalence class over the transitive, undirected closure of the edges. Chain
members are the same institution across source re-keys, so they must never
present as merge candidates: the detector excludes any pair inside one chain.
"""

import asyncpg
import pytest
import pytest_asyncio

from src.api.admin.org_dups import fetch_duplicate_pairs
from src.core.db import generate_id

pytestmark = [
    pytest.mark.integration,
]


@pytest_asyncio.fixture(loop_scope="session")
async def db(db_pool):
    """Pool-acquired connection wrapped in a rolled-back transaction."""
    async with db_pool.acquire() as conn:
        tr = conn.transaction()
        await tr.start()
        try:
            yield conn
        finally:
            await tr.rollback()


async def _mk_org(db, name):
    oid = generate_id()
    await db.execute("INSERT INTO organizations (id) VALUES ($1)", oid)
    await db.execute(
        "INSERT INTO organization_names (id, organization_id, name, is_canonical)"
        " VALUES ($1, $2, $3, TRUE)",
        generate_id(),
        oid,
        name,
    )
    return oid


async def _link_succession(db, pred, succ, archived=False):
    eid = generate_id()
    await db.execute(
        """INSERT INTO entity_events
               (id, entity_type, entity_id, event_type_id,
                linked_entity_type, linked_entity_id, archived_at)
           SELECT $1, 'organization', $2, t.id, 'organization', $3,
                  CASE WHEN $4 THEN NOW() END
           FROM entity_event_types t WHERE t.slug = 'succeeded_by'""",
        eid,
        pred,
        succ,
        archived,
    )
    return eid


async def _candidate_pairs(db):
    rows = await fetch_duplicate_pairs(db)
    return {frozenset((r["a_id"], r["b_id"])) for r in rows}


NAME = "Chamber Committee on Chain Testing"


async def test_unlinked_near_duplicates_are_candidates(db):
    a = await _mk_org(db, NAME)
    b = await _mk_org(db, NAME)
    assert frozenset((a, b)) in await _candidate_pairs(db)


async def test_directly_linked_pair_excluded(db):
    a = await _mk_org(db, NAME)
    b = await _mk_org(db, NAME)
    await _link_succession(db, a, b)
    assert frozenset((a, b)) not in await _candidate_pairs(db)


async def test_transitive_chain_excluded(db):
    """A→B→C: A/C never met by an edge, but share the chain — excluded."""
    a = await _mk_org(db, NAME)
    b = await _mk_org(db, NAME)
    c = await _mk_org(db, NAME)
    await _link_succession(db, a, b)
    await _link_succession(db, b, c)
    pairs = await _candidate_pairs(db)
    assert frozenset((a, c)) not in pairs
    assert frozenset((a, b)) not in pairs
    assert frozenset((b, c)) not in pairs


async def test_shared_successor_siblings_excluded(db):
    """A→C and B→C: A and B share a component even with no path A→B."""
    a = await _mk_org(db, NAME)
    b = await _mk_org(db, NAME)
    c = await _mk_org(db, NAME)
    await _link_succession(db, a, c)
    await _link_succession(db, b, c)
    assert frozenset((a, b)) not in await _candidate_pairs(db)


async def test_archived_succession_does_not_exclude(db):
    """A retracted link restores the pair to the candidate list."""
    a = await _mk_org(db, NAME)
    b = await _mk_org(db, NAME)
    await _link_succession(db, a, b, archived=True)
    assert frozenset((a, b)) in await _candidate_pairs(db)


async def test_chain_does_not_leak_to_outsiders(db):
    """A chain excludes only its members; an unlinked near-duplicate still shows."""
    a = await _mk_org(db, NAME)
    b = await _mk_org(db, NAME)
    outsider = await _mk_org(db, NAME)
    await _link_succession(db, a, b)
    pairs = await _candidate_pairs(db)
    assert frozenset((a, outsider)) in pairs
    assert frozenset((b, outsider)) in pairs


async def test_duplicate_active_succession_edge_is_refused_by_schema(db):
    """#469 CR: uq_entity_events_succession_edge — at most one ACTIVE
    succeeded_by edge per (predecessor, successor); closes the concurrent
    double-link race the app-level chain check cannot."""
    a = await _mk_org(db, NAME)
    b = await _mk_org(db, NAME)
    await _link_succession(db, a, b)
    with pytest.raises(asyncpg.UniqueViolationError):
        await _link_succession(db, a, b)


async def test_archived_duplicate_edge_is_allowed(db):
    """The index is partial on active rows — a retracted edge never blocks."""
    a = await _mk_org(db, NAME)
    b = await _mk_org(db, NAME)
    await _link_succession(db, a, b, archived=True)
    await _link_succession(db, a, b)  # no raise
