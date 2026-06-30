"""TTL pruning for the change-feed outbox and deletion tombstones.

The ``entity_changes`` outbox (issue #203) accretes a row on every INSERT/UPDATE
of the five entity tables, and a tombstone row on every hard delete / merge.
``deleted_entities`` accretes one row per removal. Neither is self-limiting, so
both are pruned to a fixed retention window by a scheduled job
(``scripts/prune_outbox.py`` under a systemd timer — see ``docs/COMMANDS.md``).

Retention is the consumer-facing contract: the change feed is a *recent-changes*
window, not a permanent event store. A consumer dark longer than the window must
full-reconcile (see ``docs/PUBLIC_API.md``).
"""

from dataclasses import dataclass

import asyncpg

#: Default retention window (days). Matches the ``deleted_entities`` TTL cited in
#: ``docs/PUBLIC_API.md`` and the change-feed design doc.
DEFAULT_RETENTION_DAYS = 90

# A daily DELETE over a bounded table is cheap; no dedicated ``changed_at`` index
# is maintained, to keep the trigger-heavy insert path lean. Revisit (index or
# range-partitioning) only if steady-state volume makes the scan expensive.
_PRUNE_CHANGES_SQL = """
DELETE FROM entity_changes
WHERE changed_at < NOW() - make_interval(days => $1::int)
"""

_PRUNE_TOMBSTONES_SQL = """
DELETE FROM deleted_entities
WHERE deleted_at < NOW() - make_interval(days => $1::int)
"""


@dataclass(frozen=True)
class PruneResult:
    """Row counts removed by a single :func:`prune_outbox` pass."""

    entity_changes_deleted: int
    deleted_entities_deleted: int


def _rowcount(status: str) -> int:
    """Parse asyncpg's ``DELETE <n>`` command tag into an int."""
    return int(status.split()[-1])


async def prune_outbox(
    conn: asyncpg.Connection,
    retention_days: int = DEFAULT_RETENTION_DAYS,
) -> PruneResult:
    """Delete outbox + tombstone rows older than ``retention_days``.

    Prunes both ``entity_changes`` and ``deleted_entities`` in one pass so the
    two TTLs stay aligned. Returns the per-table delete counts.
    """
    changes_status = await conn.execute(_PRUNE_CHANGES_SQL, retention_days)
    tombstones_status = await conn.execute(_PRUNE_TOMBSTONES_SQL, retention_days)
    return PruneResult(
        entity_changes_deleted=_rowcount(changes_status),
        deleted_entities_deleted=_rowcount(tombstones_status),
    )
