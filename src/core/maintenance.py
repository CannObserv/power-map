"""TTL pruning for the change-feed outbox and deletion tombstones.

The ``entity_changes`` outbox (issue #203) accretes a row on every INSERT/UPDATE
of the five entity tables, and a tombstone row on every hard delete / merge.
``deleted_entities`` accretes one row per removal. Neither is self-limiting, so
both are pruned to a fixed retention window by a scheduled job
(``scripts/prune_outbox.py`` under a systemd timer — see ``docs/COMMANDS.md``).

Retention is the consumer-facing contract: the change feed is a *recent-changes*
window, not a permanent event store. A consumer dark longer than the window must
full-reconcile (see ``docs/PUBLIC_API.md``).

Deletes run in independently-committed batches so a large first-run backlog does
not execute as one long transaction (lock/WAL/bloat). The job is idempotent — a
run that fails partway resumes on the next invocation.
"""

from dataclasses import dataclass

import asyncpg

#: Default retention window (days). Matches the ``deleted_entities`` TTL cited in
#: ``docs/PUBLIC_API.md`` and the change-feed design doc.
DEFAULT_RETENTION_DAYS = 90

#: Rows deleted per batch. Bounds lock duration / WAL burst / dead-tuple bloat so
#: a large first-run backlog doesn't run as one long transaction.
DEFAULT_BATCH_SIZE = 10_000


# Single source of truth for the "expired" predicate ($1 = retention days),
# shared by the count and the delete so a dry run can never drift from what an
# execute actually deletes.
def _expired(col: str) -> str:
    return f"{col} < NOW() - make_interval(days => $1::int)"


_COUNT_CHANGES_SQL = f"SELECT COUNT(*) FROM entity_changes WHERE {_expired('changed_at')}"
_COUNT_TOMBSTONES_SQL = f"SELECT COUNT(*) FROM deleted_entities WHERE {_expired('deleted_at')}"

# Batched DELETE via ``ctid IN (… LIMIT $2)`` (ctid is the physical row id, so no
# dedicated ``changed_at`` index is needed — keeps the trigger-heavy insert path
# lean). Revisit range-partitioning only if the daily scan grows expensive.
_PRUNE_CHANGES_SQL = f"""
DELETE FROM entity_changes
WHERE ctid IN (
    SELECT ctid FROM entity_changes
    WHERE {_expired("changed_at")}
    LIMIT $2
)
"""

_PRUNE_TOMBSTONES_SQL = f"""
DELETE FROM deleted_entities
WHERE ctid IN (
    SELECT ctid FROM deleted_entities
    WHERE {_expired("deleted_at")}
    LIMIT $2
)
"""


@dataclass(frozen=True)
class PruneResult:
    """Per-table row counts.

    From :func:`prune_outbox`, the rows deleted; from :func:`count_prunable`, the
    rows that *would* be deleted.
    """

    entity_changes_deleted: int
    deleted_entities_deleted: int


def _validate_retention(retention_days: int) -> None:
    if retention_days < 1:
        raise ValueError(f"retention_days must be >= 1, got {retention_days}")


def _rowcount(status: str) -> int:
    """Parse asyncpg's ``DELETE <n>`` command tag into an int."""
    return int(status.split()[-1])


async def count_prunable(
    conn: asyncpg.Connection,
    retention_days: int = DEFAULT_RETENTION_DAYS,
) -> PruneResult:
    """Count rows eligible for deletion, without deleting any.

    Shares the retention predicate with :func:`prune_outbox`, so a dry run can
    never disagree with the subsequent execute.
    """
    _validate_retention(retention_days)
    changes = await conn.fetchval(_COUNT_CHANGES_SQL, retention_days)
    tombstones = await conn.fetchval(_COUNT_TOMBSTONES_SQL, retention_days)
    return PruneResult(entity_changes_deleted=changes, deleted_entities_deleted=tombstones)


async def _prune_table(
    conn: asyncpg.Connection,
    sql: str,
    retention_days: int,
    batch_size: int,
) -> int:
    """Delete expired rows from one table in committed batches; return the total.

    Each batch runs in its own transaction (a savepoint when the caller is
    already in one), so completed batches persist even if a later batch fails.
    """
    total = 0
    while True:
        async with conn.transaction():
            status = await conn.execute(sql, retention_days, batch_size)
        deleted = _rowcount(status)
        total += deleted
        if deleted < batch_size:
            return total


async def prune_outbox(
    conn: asyncpg.Connection,
    retention_days: int = DEFAULT_RETENTION_DAYS,
    *,
    batch_size: int = DEFAULT_BATCH_SIZE,
) -> PruneResult:
    """Delete outbox + tombstone rows older than ``retention_days``.

    Prunes both ``entity_changes`` and ``deleted_entities`` in independently
    committed batches so the two TTLs stay aligned. Returns the per-table delete
    counts.
    """
    _validate_retention(retention_days)
    if batch_size < 1:
        raise ValueError(f"batch_size must be >= 1, got {batch_size}")
    changes = await _prune_table(conn, _PRUNE_CHANGES_SQL, retention_days, batch_size)
    tombstones = await _prune_table(conn, _PRUNE_TOMBSTONES_SQL, retention_days, batch_size)
    return PruneResult(
        entity_changes_deleted=changes,
        deleted_entities_deleted=tombstones,
    )
