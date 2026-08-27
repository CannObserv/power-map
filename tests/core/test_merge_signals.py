"""Unit tests for the two consumer-facing signals a merge owes (#467).

The route-level tests exercise these helpers incidentally, through whichever merge
path calls them. These pin the contract directly: what each does with an empty
pair list, what it rejects, and what it does on a second application — the
properties a caller relies on but no single merge test asserts.
"""

import hashlib
import os

import pytest
import pytest_asyncio

from src.core.db import generate_id
from src.core.merge_signals import (
    MERGEABLE_ENTITY_TYPES,
    mirror_subscriptions,
    record_merge_tombstones,
)

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
async def api_key_id(db):
    uid, kid = generate_id(), generate_id()
    raw_key = "pm_" + os.urandom(16).hex()
    await db.execute("INSERT INTO app_users (id, email) VALUES ($1,$2)", uid, "signals@test.com")
    await db.execute(
        "INSERT INTO api_keys (id, user_id, label, key_prefix, key_hash) VALUES ($1,$2,$3,$4,$5)",
        kid,
        uid,
        "Signals Unit",
        raw_key[:8],
        hashlib.sha256(raw_key.encode()).hexdigest(),
    )
    return kid


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
    """A→B then A→C keeps B: the first hop is the one the subscriber missed."""
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


# ---------------------------------------------------------------------------
# mirror_subscriptions
# ---------------------------------------------------------------------------


async def _subscribe(db, kid, entity_id, entity_type):
    await db.execute(
        "INSERT INTO api_key_entity_subscriptions (api_key_id, entity_id, entity_type)"
        " VALUES ($1,$2,$3)",
        kid,
        entity_id,
        entity_type,
    )


async def test_mirror_adds_the_winner_and_keeps_the_loser(db, api_key_id, org_pair):
    """The loser row is the audience for the loser's tombstone — it must survive."""
    loser, winner = org_pair
    await _subscribe(db, api_key_id, loser, "organization")
    await mirror_subscriptions(db, [(loser, winner)])

    rows = await db.fetch(
        "SELECT entity_id, entity_type FROM api_key_entity_subscriptions WHERE api_key_id=$1",
        api_key_id,
    )
    assert {(r["entity_id"], r["entity_type"]) for r in rows} == {
        (loser, "organization"),
        (winner, "organization"),
    }


async def test_mirror_copies_entity_type_from_the_loser_row(db, api_key_id):
    """The type is read from the row being mirrored, not supplied by the caller.

    That makes a caller's mismatched argument impossible rather than silently
    retyping a subscription — there is no argument to mismatch.
    """
    org = generate_id()
    role_owner = generate_id()
    loser_role, winner_role = generate_id(), generate_id()
    await db.execute("INSERT INTO organizations (id) VALUES ($1)", org)
    await db.execute("INSERT INTO organizations (id) VALUES ($1)", role_owner)
    for rid, oid in ((loser_role, org), (winner_role, role_owner)):
        await db.execute(
            "INSERT INTO roles (id, organization_id, title) VALUES ($1,$2,$3)", rid, oid, rid[:8]
        )
    await _subscribe(db, api_key_id, loser_role, "role")
    await mirror_subscriptions(db, [(loser_role, winner_role)])

    assert (
        await db.fetchval(
            "SELECT entity_type FROM api_key_entity_subscriptions"
            " WHERE api_key_id=$1 AND entity_id=$2",
            api_key_id,
            winner_role,
        )
        == "role"
    )


async def test_mirror_is_idempotent_when_the_winner_is_already_subscribed(db, api_key_id, org_pair):
    """The PK is (api_key_id, entity_id); a re-insert must not raise."""
    loser, winner = org_pair
    await _subscribe(db, api_key_id, loser, "organization")
    await _subscribe(db, api_key_id, winner, "organization")
    await mirror_subscriptions(db, [(loser, winner)])
    await mirror_subscriptions(db, [(loser, winner)])

    assert (
        await db.fetchval(
            "SELECT count(*) FROM api_key_entity_subscriptions WHERE api_key_id=$1", api_key_id
        )
        == 2
    )


async def test_mirror_adds_nothing_when_nobody_watched_the_loser(db, api_key_id, org_pair):
    """No watcher means no need for the winner — the winner is not force-subscribed."""
    loser, winner = org_pair
    await mirror_subscriptions(db, [(loser, winner)])

    assert (
        await db.fetchval(
            "SELECT count(*) FROM api_key_entity_subscriptions WHERE entity_id = ANY($1::text[])",
            [loser, winner],
        )
        == 0
    )


async def test_mirror_empty_pairs_is_a_noop(db):
    before = await db.fetchval("SELECT count(*) FROM api_key_entity_subscriptions")
    await mirror_subscriptions(db, [])
    assert await db.fetchval("SELECT count(*) FROM api_key_entity_subscriptions") == before
