"""TTL pruning for deletion tombstones and the request log — one window each.

``deleted_entities`` accretes one row per removal; its merge rows are exempt
(#607 — see ``_PRUNABLE_TOMBSTONE``). ``api_request_log`` (issue #260) accretes
one row per ``/api/v1/*`` request. Neither is self-limiting, so each is pruned
to its own retention window (:class:`Retention`) by a scheduled job
(``scripts/prune.py`` under a systemd timer — see ``docs/COMMANDS.md``).

The windows are separate because the tables answer to different contracts
(#502). A bare-delete tombstone's window is how long its id answers ``410``
before falling back to ``404`` (``docs/PUBLIC_API.md`` § Retired ids). The
request log's window bounds the PII its captured bodies hold
(``docs/CONVENTIONS.md``). The third table this job once pruned, the change
outbox, was dropped in #651.

Deletes run in independently-committed batches so a large first-run backlog does
not execute as one long transaction (lock/WAL/bloat). The job is idempotent — a
run that fails partway resumes on the next invocation.
"""

from dataclasses import dataclass, fields

import asyncpg


@dataclass(frozen=True)
class Retention:
    """Retention window, in days, for each pruned table (#502).

    Each defaults to 90 days. The single shared window that preceded this, and
    the #495 104-day hold on it, retired in #502.
    """

    #: Bare-delete tombstones only: merge tombstones never expire (#607).
    deleted_entities: int = 90
    api_request_log: int = 90

    def __post_init__(self) -> None:
        for f in fields(self):
            days = getattr(self, f.name)
            if days < 1:
                raise ValueError(f"{f.name} retention must be >= 1 day, got {days}")


#: The windows the daily prune runs on (``infra/power-map-prune.service`` names none).
DEFAULT_RETENTION = Retention()

#: Rows deleted per batch. Bounds lock duration / WAL burst / dead-tuple bloat so
#: a large first-run backlog doesn't run as one long transaction.
DEFAULT_BATCH_SIZE = 10_000


# Single source of truth for the "expired" predicate ($1 = retention days),
# shared by the count and the delete so a dry run can never drift from what an
# execute actually deletes.
def _expired(col: str) -> str:
    return f"{col} < NOW() - make_interval(days => $1::int)"


# Merge tombstones (``merged_into`` set) never expire: they are the public detail
# GETs' answer to "where did this id go" (#607), and pruning one turns the loser's
# ``410`` + ``merged_into`` back into an indistinguishable ``404``. One row per
# merge, so the table stays small. Bare-delete tombstones keep the TTL.
_PRUNABLE_TOMBSTONE = f"merged_into IS NULL AND {_expired('deleted_at')}"

_COUNT_TOMBSTONES_SQL = f"SELECT COUNT(*) FROM deleted_entities WHERE {_PRUNABLE_TOMBSTONE}"
_COUNT_REQUEST_LOG_SQL = f"SELECT COUNT(*) FROM api_request_log WHERE {_expired('occurred_at')}"

# Batched DELETE via ``ctid IN (… LIMIT $2)``: ctid is the physical row id, so a
# bounded batch needs no key column (the planner may use the expiry index,
# ``idx_deleted_entities_deleted_at`` here, for the inner scan).
_PRUNE_TOMBSTONES_SQL = f"""
DELETE FROM deleted_entities
WHERE ctid IN (
    SELECT ctid FROM deleted_entities
    WHERE {_PRUNABLE_TOMBSTONE}
    LIMIT $2
)
"""

# api_request_log (#260) accretes one row per /api/v1/* request. Its own window,
# same batched-DELETE-via-ctid strategy — no dedicated occurred_at index needed
# beyond idx_arl_occurred, which the planner may use for the LIMIT scan.
_PRUNE_REQUEST_LOG_SQL = f"""
DELETE FROM api_request_log
WHERE ctid IN (
    SELECT ctid FROM api_request_log
    WHERE {_expired("occurred_at")}
    LIMIT $2
)
"""


@dataclass(frozen=True)
class PruneResult:
    """Per-table row counts.

    From :func:`prune_expired`, the rows deleted; from :func:`count_prunable`, the
    rows that *would* be deleted.
    """

    deleted_entities: int
    api_request_log: int


def _rowcount(status: str) -> int:
    """Parse asyncpg's ``DELETE <n>`` command tag into an int."""
    return int(status.split()[-1])


async def count_prunable(
    conn: asyncpg.Connection,
    retention: Retention = DEFAULT_RETENTION,
) -> PruneResult:
    """Count rows eligible for deletion, without deleting any.

    Shares the retention predicate with :func:`prune_expired`, so a dry run can
    never disagree with the subsequent execute.
    """
    tombstones = await conn.fetchval(_COUNT_TOMBSTONES_SQL, retention.deleted_entities)
    request_log = await conn.fetchval(_COUNT_REQUEST_LOG_SQL, retention.api_request_log)
    return PruneResult(deleted_entities=tombstones, api_request_log=request_log)


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


async def prune_expired(
    conn: asyncpg.Connection,
    retention: Retention = DEFAULT_RETENTION,
    *,
    batch_size: int = DEFAULT_BATCH_SIZE,
) -> PruneResult:
    """Delete each table's rows older than its own window in ``retention``.

    Prunes ``deleted_entities`` and ``api_request_log`` in independently
    committed batches. Returns the per-table delete counts.
    """
    if batch_size < 1:
        raise ValueError(f"batch_size must be >= 1, got {batch_size}")
    tombstones = await _prune_table(
        conn, _PRUNE_TOMBSTONES_SQL, retention.deleted_entities, batch_size
    )
    request_log = await _prune_table(
        conn, _PRUNE_REQUEST_LOG_SQL, retention.api_request_log, batch_size
    )
    return PruneResult(deleted_entities=tombstones, api_request_log=request_log)
