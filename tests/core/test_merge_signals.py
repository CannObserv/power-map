"""Unit tests for the tombstone a merge owes each hard-deleted row (#467, #607).

The route-level tests exercise the helper incidentally, through whichever merge
path calls it. These pin the contract directly: what it does with an empty pair
list, what it rejects, and what it does on a second application — the properties
a caller relies on but no single merge test asserts. (The subscription mirror
tested beside it retired with the change feed, #502.)
"""

import pytest
import pytest_asyncio

from src.core.db import generate_id
from src.core.merge_signals import MERGEABLE_ENTITY_TYPES, record_merge_tombstones

pytestmark = [
    pytest.mark.integration,
]


@pytest_asyncio.fixture(loop_scope="session")
async def db(db_pool):
    async with db_pool.acquire() as conn:
        tr = conn.transaction()
        await tr.start()
        try:
            yield conn
        finally:
            await tr.rollback()


@pytest_asyncio.fixture(loop_scope="session")
async def org_pair(db):
    ids = (generate_id(), generate_id())
    for oid in ids:
        await db.execute("INSERT INTO organizations (id) VALUES ($1)", oid)
    return ids


# ---------------------------------------------------------------------------
# record_merge_tombstones
# ---------------------------------------------------------------------------


async def test_tombstone_writes_the_row_and_the_outbox_signal(db, org_pair):
    """One `deleted_entities` row per pair; its INSERT trigger emits the outbox row."""
    loser, winner = org_pair
    await record_merge_tombstones(db, "organization", [(loser, winner)])

    assert (
        await db.fetchval(
            "SELECT merged_into FROM deleted_entities WHERE entity_type='organization'"
            " AND entity_id=$1",
            loser,
        )
        == winner
    )
    assert (
        await db.fetchval(
            "SELECT merged_into FROM entity_changes WHERE entity_id=$1 AND change_kind='deleted'",
            loser,
        )
        == winner
    )


async def test_tombstone_is_idempotent(db, org_pair):
    """A second application is a no-op, not a 23505 — a re-merge must not abort."""
    loser, winner = org_pair
    await record_merge_tombstones(db, "organization", [(loser, winner)])
    await record_merge_tombstones(db, "organization", [(loser, winner)])

    assert await db.fetchval("SELECT count(*) FROM deleted_entities WHERE entity_id=$1", loser) == 1


async def test_tombstone_keeps_the_first_winner_on_a_second_hop(db, org_pair):
    """A→B then A→C keeps B: the first hop is the one a reader missed."""
    loser, winner = org_pair
    later = generate_id()
    await db.execute("INSERT INTO organizations (id) VALUES ($1)", later)
    await record_merge_tombstones(db, "organization", [(loser, winner)])
    await record_merge_tombstones(db, "organization", [(loser, later)])

    assert (
        await db.fetchval("SELECT merged_into FROM deleted_entities WHERE entity_id=$1", loser)
        == winner
    )


async def test_tombstone_empty_pairs_is_a_noop(db):
    """Callers pass whatever their conflict query returned, without a length check."""
    before = await db.fetchval("SELECT count(*) FROM deleted_entities")
    await record_merge_tombstones(db, "role_assignment", [])
    assert await db.fetchval("SELECT count(*) FROM deleted_entities") == before


@pytest.mark.parametrize("bad", ["organisation", "identifier", "", "Person"])
async def test_tombstone_rejects_an_unmergeable_entity_type(db, org_pair, bad):
    """Fail in Python, naming the value — a CHECK violation from executemany does not."""
    with pytest.raises(ValueError, match="not a mergeable entity type"):
        await record_merge_tombstones(db, bad, [org_pair])


def test_mergeable_types_are_a_subset_of_what_the_schema_accepts():
    """Guards the frozenset against drifting into a type the CHECK constraint rejects."""
    assert MERGEABLE_ENTITY_TYPES <= {
        "person",
        "organization",
        "jurisdiction",
        "role",
        "role_assignment",
        "role_assignment_relationship",
    }


def test_edges_and_jurisdictions_are_excluded_deliberately():
    """Both are permitted by the CHECK constraint and withheld here on purpose.

    No merge folds one jurisdiction into another; and an edge is announced through
    the two assignments it joins, each of which carries its own tombstone, so it has
    no independent anchor a consumer could repair.
    """
    assert "jurisdiction" not in MERGEABLE_ENTITY_TYPES
    assert "role_assignment_relationship" not in MERGEABLE_ENTITY_TYPES
