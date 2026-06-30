"""Prune the change-feed outbox and deletion tombstones past their TTL.

Issue #204. The ``entity_changes`` outbox grows on every INSERT/UPDATE of the
five entity tables (plus a tombstone per delete/merge); ``deleted_entities``
grows one row per removal. Neither self-limits. This job deletes rows older than
the retention window (default 90 days) and is wired to a daily systemd timer
(``infra/power-map-prune.timer`` — see ``docs/COMMANDS.md``).

Retention is the consumer contract: the feed is a recent-changes window, not a
permanent event store. See ``docs/PUBLIC_API.md``.

Usage:
    uv run python -m scripts.prune_outbox                       # dry run (counts only)
    uv run python -m scripts.prune_outbox --execute             # commit deletes
    uv run python -m scripts.prune_outbox --execute --retention-days 90
"""

import argparse
import asyncio
import os

import asyncpg

from src.core.logging import configure_logging, get_logger
from src.core.maintenance import DEFAULT_RETENTION_DAYS, prune_outbox

logger = get_logger(__name__)

_COUNT_SQL = """
SELECT
    (SELECT COUNT(*) FROM entity_changes
       WHERE changed_at < NOW() - make_interval(days => $1::int))   AS entity_changes,
    (SELECT COUNT(*) FROM deleted_entities
       WHERE deleted_at < NOW() - make_interval(days => $1::int))   AS deleted_entities
"""


async def run(*, execute: bool, retention_days: int) -> None:
    """Prune (or, in dry-run mode, count) expired outbox + tombstone rows."""
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        raise RuntimeError("DATABASE_URL not set")

    conn = await asyncpg.connect(dsn)
    try:
        if not execute:
            counts = await conn.fetchrow(_COUNT_SQL, retention_days)
            logger.info(
                "Dry run — %d entity_changes + %d deleted_entities row(s) older "
                "than %d days are eligible; pass --execute to delete",
                counts["entity_changes"],
                counts["deleted_entities"],
                retention_days,
            )
            return

        result = await prune_outbox(conn, retention_days)
        logger.info(
            "Pruned %d entity_changes + %d deleted_entities row(s) older than %d days",
            result.entity_changes_deleted,
            result.deleted_entities_deleted,
            retention_days,
        )
    finally:
        await conn.close()


def main() -> None:
    configure_logging()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Commit deletes (default is dry run)",
    )
    parser.add_argument(
        "--retention-days",
        type=int,
        default=DEFAULT_RETENTION_DAYS,
        help=f"Retention window in days (default {DEFAULT_RETENTION_DAYS})",
    )
    args = parser.parse_args()
    if args.retention_days < 1:
        parser.error("--retention-days must be >= 1")
    asyncio.run(run(execute=args.execute, retention_days=args.retention_days))


if __name__ == "__main__":
    main()
