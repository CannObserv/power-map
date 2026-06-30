"""Integration tests for src.core.maintenance — outbox/tombstone TTL pruning."""

import pytest
import pytest_asyncio

from src.core.maintenance import (
    DEFAULT_RETENTION_DAYS,
    PruneResult,
    count_prunable,
    prune_outbox,
)

pytestmark = [pytest.mark.integration]


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


async def _insert_change(conn, entity_id, *, days_old):
    await conn.execute(
        "INSERT INTO entity_changes (entity_type, entity_id, change_kind, changed_at) "
        "VALUES ('organization', $1, 'updated', NOW() - make_interval(days => $2::int))",
        entity_id,
        days_old,
    )


async def _insert_tombstone(conn, entity_id, *, days_old):
    await conn.execute(
        "INSERT INTO deleted_entities (entity_type, entity_id, deleted_at) "
        "VALUES ('organization', $1, NOW() - make_interval(days => $2::int))",
        entity_id,
        days_old,
    )


async def _change_ids(conn):
    rows = await conn.fetch("SELECT entity_id FROM entity_changes ORDER BY entity_id")
    return {r["entity_id"] for r in rows}


async def _tombstone_ids(conn):
    rows = await conn.fetch("SELECT entity_id FROM deleted_entities ORDER BY entity_id")
    return {r["entity_id"] for r in rows}


def test_default_retention_is_90_days():
    assert DEFAULT_RETENTION_DAYS == 90


async def test_prune_deletes_stale_keeps_fresh(db):
    await _insert_change(db, "ec-stale", days_old=91)
    await _insert_change(db, "ec-fresh", days_old=1)
    await _insert_tombstone(db, "de-stale", days_old=91)
    await _insert_tombstone(db, "de-fresh", days_old=1)

    result = await prune_outbox(db, retention_days=90)

    assert isinstance(result, PruneResult)
    assert result.entity_changes == 1
    assert result.deleted_entities == 1

    changes = await _change_ids(db)
    assert "ec-stale" not in changes
    assert "ec-fresh" in changes

    tombstones = await _tombstone_ids(db)
    assert "de-stale" not in tombstones
    assert "de-fresh" in tombstones


async def test_prune_respects_retention_days(db):
    """A wider window keeps rows a 90-day window would drop."""
    await _insert_change(db, "ec-91d", days_old=91)
    await _insert_tombstone(db, "de-91d", days_old=91)

    result = await prune_outbox(db, retention_days=120)

    assert result.entity_changes == 0
    assert result.deleted_entities == 0
    assert "ec-91d" in await _change_ids(db)
    assert "de-91d" in await _tombstone_ids(db)


async def test_prune_default_retention(db):
    """Called with no retention arg, uses the 90-day default."""
    await _insert_change(db, "ec-old", days_old=120)
    await _insert_change(db, "ec-recent", days_old=10)

    result = await prune_outbox(db)

    assert result.entity_changes == 1
    changes = await _change_ids(db)
    assert "ec-old" not in changes
    assert "ec-recent" in changes


async def test_count_prunable_matches_delete_and_does_not_mutate(db):
    """The dry-run count equals what execute deletes, and counting leaves rows."""
    await _insert_change(db, "ec-stale", days_old=91)
    await _insert_change(db, "ec-fresh", days_old=1)
    await _insert_tombstone(db, "de-stale", days_old=91)
    await _insert_tombstone(db, "de-fresh", days_old=1)

    eligible = await count_prunable(db, retention_days=90)
    assert eligible.entity_changes == 1
    assert eligible.deleted_entities == 1
    # Counting must not delete anything.
    assert "ec-stale" in await _change_ids(db)
    assert "de-stale" in await _tombstone_ids(db)

    deleted = await prune_outbox(db, retention_days=90)
    assert (deleted.entity_changes, deleted.deleted_entities) == (
        eligible.entity_changes,
        eligible.deleted_entities,
    )


async def test_prune_batches_until_drained(db):
    """A batch_size smaller than the backlog still deletes every expired row."""
    for i in range(5):
        await _insert_change(db, f"ec-{i}", days_old=100)
    await _insert_change(db, "ec-keep", days_old=1)

    result = await prune_outbox(db, retention_days=90, batch_size=2)

    assert result.entity_changes == 5
    changes = await _change_ids(db)
    assert changes == {"ec-keep"}


@pytest.mark.parametrize("bad", [0, -1])
async def test_retention_days_must_be_positive(db, bad):
    with pytest.raises(ValueError):
        await prune_outbox(db, retention_days=bad)
    with pytest.raises(ValueError):
        await count_prunable(db, retention_days=bad)


async def test_prune_commits_durably_across_connections(db_pool):
    """Real-commit path: batches persist (not just savepoints).

    Runs on raw pooled connections — no enclosing rolled-back transaction — so
    the per-batch COMMIT is exercised. A *separate* connection then confirms the
    deletes are durable. Acquires its own connections and cleans up explicitly,
    since nothing rolls back here.
    """
    tag = "ec-commit-durable"
    try:
        async with db_pool.acquire() as setup:
            await setup.execute("DELETE FROM entity_changes WHERE entity_id LIKE $1", f"{tag}-%")
            for i in range(5):
                await _insert_change(setup, f"{tag}-{i}", days_old=200)

        async with db_pool.acquire() as conn:
            result = await prune_outbox(conn, retention_days=90, batch_size=2)

        assert result.entity_changes >= 5  # our 5 stale rows, drained across 3 batches

        # Durability check from a different connection: our rows are gone.
        async with db_pool.acquire() as verify:
            remaining = await verify.fetchval(
                "SELECT COUNT(*) FROM entity_changes WHERE entity_id LIKE $1", f"{tag}-%"
            )
        assert remaining == 0
    finally:
        async with db_pool.acquire() as cleanup:
            await cleanup.execute("DELETE FROM entity_changes WHERE entity_id LIKE $1", f"{tag}-%")
