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

import asyncpg

from scripts._dsn import add_dsn_args, resolve_dsn
from src.core.logging import configure_logging, get_logger
from src.core.maintenance import DEFAULT_RETENTION_DAYS, count_prunable, prune_outbox

logger = get_logger(__name__)


async def run(dsn: str, *, execute: bool, retention_days: int) -> None:
    """Prune (or, in dry-run mode, count) expired outbox + tombstone rows."""
    conn = await asyncpg.connect(dsn)
    try:
        if not execute:
            eligible = await count_prunable(conn, retention_days)
            logger.info(
                "Dry run — %d entity_changes + %d deleted_entities + %d api_request_log "
                "row(s) older than %d days are eligible; pass --execute to delete",
                eligible.entity_changes,
                eligible.deleted_entities,
                eligible.api_request_log,
                retention_days,
            )
            return

        result = await prune_outbox(conn, retention_days)
        logger.info(
            "Pruned %d entity_changes + %d deleted_entities + %d api_request_log "
            "row(s) older than %d days",
            result.entity_changes,
            result.deleted_entities,
            result.api_request_log,
            retention_days,
        )
    finally:
        await conn.close()


def main() -> None:
    configure_logging()
    parser = argparse.ArgumentParser(description=__doc__)
    add_dsn_args(parser)
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
    # Resolved after validation: the echo means "about to connect", so it must
    # not precede a usage error that ends the run without one. See
    # docs/RUNBOOKS.md §"Operational scripts — dry run by default & target echo".
    dsn = resolve_dsn(args, parser)
    asyncio.run(run(dsn, execute=args.execute, retention_days=args.retention_days))


if __name__ == "__main__":
    main()
