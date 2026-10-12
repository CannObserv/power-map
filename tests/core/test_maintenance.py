"""Integration tests for src.core.maintenance — per-table TTL pruning (#204, #502)."""

import pytest
import pytest_asyncio

from src.core.maintenance import (
    DEFAULT_RETENTION,
    PruneResult,
    Retention,
    count_prunable,
    prune_outbox,
)

pytestmark = [pytest.mark.integration]


@pytest_asyncio.fixture(loop_scope="session")
async def db(db_pool):
    """Pool-acquired connection wrapped in a rolled-back transaction.

    The outbox tables are append-only and trigger-populated: every entity
    insert/update/delete in the wider suite (e.g. merges) commits `entity_changes`
    / `deleted_entities` rows that are never cleaned up. Prune assertions that
    check exact table contents (`changes == {...}`) would see that cross-test
    pollution. Emptying both tables *inside* this rolled-back transaction gives
    each prune test an isolated view without durably deleting anything — the
    rollback restores the other tests' committed rows.
    """
    async with db_pool.acquire() as conn:
        tr = conn.transaction()
        await tr.start()
        try:
            await conn.execute("DELETE FROM entity_changes")
            await conn.execute("DELETE FROM deleted_entities")
            await conn.execute("DELETE FROM api_request_log")
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


async def _insert_tombstone(conn, entity_id, *, days_old, merged_into=None):
    await conn.execute(
        "INSERT INTO deleted_entities (entity_type, entity_id, deleted_at, merged_into) "
        "VALUES ('organization', $1, NOW() - make_interval(days => $2::int), $3)",
        entity_id,
        days_old,
        merged_into,
    )


async def _change_ids(conn):
    rows = await conn.fetch("SELECT entity_id FROM entity_changes ORDER BY entity_id")
    return {r["entity_id"] for r in rows}


async def _tombstone_ids(conn):
    rows = await conn.fetch("SELECT entity_id FROM deleted_entities ORDER BY entity_id")
    return {r["entity_id"] for r in rows}


async def _insert_arl(conn, path, *, days_old):
    await conn.execute(
        "INSERT INTO api_request_log "
        "(method, path, route_group, status_code, latency_ms, occurred_at) "
        "VALUES ('GET', $1, 'other', 200, 1, NOW() - make_interval(days => $2::int))",
        path,
        days_old,
    )


async def _arl_paths(conn):
    rows = await conn.fetch("SELECT path FROM api_request_log ORDER BY path")
    return {r["path"] for r in rows}


def test_default_retention_is_90_days_for_every_table():
    """#502 released the #495 104-day hold: each window is set deliberately, all 90."""
    assert DEFAULT_RETENTION == Retention(
        entity_changes=90, deleted_entities=90, api_request_log=90
    )


async def test_prune_deletes_stale_api_request_log(db):
    await _insert_arl(db, "/api/v1/stale", days_old=91)
    await _insert_arl(db, "/api/v1/fresh", days_old=1)

    result = await prune_outbox(db)

    assert result.api_request_log == 1
    paths = await _arl_paths(db)
    assert "/api/v1/stale" not in paths
    assert "/api/v1/fresh" in paths


async def test_count_prunable_includes_api_request_log(db):
    await _insert_arl(db, "/api/v1/stale", days_old=91)

    eligible = await count_prunable(db)

    assert eligible.api_request_log == 1
    # Counting must not delete anything.
    assert "/api/v1/stale" in await _arl_paths(db)


async def test_prune_deletes_stale_keeps_fresh(db):
    await _insert_change(db, "ec-stale", days_old=91)
    await _insert_change(db, "ec-fresh", days_old=1)
    await _insert_tombstone(db, "de-stale", days_old=91)
    await _insert_tombstone(db, "de-fresh", days_old=1)

    result = await prune_outbox(db)

    assert isinstance(result, PruneResult)
    assert result.entity_changes == 1
    assert result.deleted_entities == 1

    changes = await _change_ids(db)
    assert "ec-stale" not in changes
    assert "ec-fresh" in changes

    tombstones = await _tombstone_ids(db)
    assert "de-stale" not in tombstones
    assert "de-fresh" in tombstones


async def test_prune_keeps_merge_tombstones_at_any_age(db):
    """A merge tombstone is the read path's answer to "where did this id go" (#607).

    Pruning it turns the loser's ``410 merged_into`` back into a bare ``404``, so
    only bare-delete tombstones age out. Count and delete share the predicate.
    """
    await _insert_tombstone(db, "de-merged-old", days_old=400, merged_into="winner")
    await _insert_tombstone(db, "de-bare-old", days_old=400)

    eligible = await count_prunable(db)
    result = await prune_outbox(db)

    assert eligible.deleted_entities == result.deleted_entities == 1
    assert await _tombstone_ids(db) == {"de-merged-old"}


async def test_each_table_prunes_on_its_own_window(db):
    """One table's window never governs another's (#502)."""
    await _insert_change(db, "ec-91d", days_old=91)
    await _insert_tombstone(db, "de-91d", days_old=91)
    await _insert_arl(db, "/api/v1/arl-70d", days_old=70)
    await _insert_arl(db, "/api/v1/arl-50d", days_old=50)
    retention = Retention(entity_changes=120, deleted_entities=90, api_request_log=60)

    eligible = await count_prunable(db, retention)
    result = await prune_outbox(db, retention)

    assert (
        eligible == result == PruneResult(entity_changes=0, deleted_entities=1, api_request_log=1)
    )
    assert "ec-91d" in await _change_ids(db)
    assert "de-91d" not in await _tombstone_ids(db)
    assert await _arl_paths(db) == {"/api/v1/arl-50d"}


async def test_prune_default_retention(db):
    """Called with no retention arg, uses the 90-day defaults."""
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

    eligible = await count_prunable(db)
    assert eligible.entity_changes == 1
    assert eligible.deleted_entities == 1
    # Counting must not delete anything.
    assert "ec-stale" in await _change_ids(db)
    assert "de-stale" in await _tombstone_ids(db)

    deleted = await prune_outbox(db)
    assert (deleted.entity_changes, deleted.deleted_entities) == (
        eligible.entity_changes,
        eligible.deleted_entities,
    )


async def test_prune_batches_until_drained(db):
    """A batch_size smaller than the backlog still deletes every expired row."""
    for i in range(5):
        await _insert_change(db, f"ec-{i}", days_old=100)
    await _insert_change(db, "ec-keep", days_old=1)

    result = await prune_outbox(db, batch_size=2)

    assert result.entity_changes == 5
    changes = await _change_ids(db)
    assert changes == {"ec-keep"}


@pytest.mark.parametrize("field", ["entity_changes", "deleted_entities", "api_request_log"])
@pytest.mark.parametrize("bad", [0, -1])
def test_every_window_must_be_positive(field, bad):
    with pytest.raises(ValueError, match=field):
        Retention(**{field: bad})


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
            result = await prune_outbox(conn, batch_size=2)

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
