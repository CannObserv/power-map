"""Prune the outbox, deletion tombstones and request log, each on its own window.

Issue #204. The ``entity_changes`` outbox grows on every INSERT/UPDATE of the
five entity tables (plus a tombstone per delete/merge); ``deleted_entities``
grows one row per removal; ``api_request_log`` one row per ``/api/v1/*``
request. None self-limits. This job deletes each table's rows older than that
table's retention window (90 days each by default, #502) and is wired to a
daily systemd timer (``infra/power-map-prune.timer`` — see ``docs/COMMANDS.md``).
Merge tombstones (``merged_into`` set) are exempt at any age (#607): they back
the detail GETs' ``410`` + ``merged_into``.

Each window is its own flag; the single ``--retention-days`` (and the #495
104-day hold it carried) retired in #502. See ``docs/RUNBOOKS.md`` § prune.

Usage:
    uv run python -m scripts.prune_outbox                       # dry run (counts only)
    uv run python -m scripts.prune_outbox --execute             # commit deletes
    uv run python -m scripts.prune_outbox --execute --request-log-retention-days 30
"""

import argparse
import asyncio

import asyncpg

from scripts._dsn import add_dsn_args, build_parser, resolve_dsn
from src.core.logging import configure_logging, get_logger
from src.core.maintenance import DEFAULT_RETENTION, Retention, count_prunable, prune_outbox

logger = get_logger(__name__)

#: flag → the :class:`Retention` field it sets.
_WINDOW_FLAGS = {
    "--changes-retention-days": "entity_changes",
    "--tombstone-retention-days": "deleted_entities",
    "--request-log-retention-days": "api_request_log",
}


async def run(dsn: str, *, execute: bool, retention: Retention) -> None:
    """Prune (or, in dry-run mode, count) each table's expired rows."""
    conn = await asyncpg.connect(dsn)
    try:
        if not execute:
            eligible = await count_prunable(conn, retention)
            logger.info(
                "Dry run — %d entity_changes (>%dd) + %d deleted_entities (>%dd) + "
                "%d api_request_log (>%dd) row(s) are eligible; pass --execute to delete",
                eligible.entity_changes,
                retention.entity_changes,
                eligible.deleted_entities,
                retention.deleted_entities,
                eligible.api_request_log,
                retention.api_request_log,
            )
            return

        result = await prune_outbox(conn, retention)
        logger.info(
            "Pruned %d entity_changes (>%dd) + %d deleted_entities (>%dd) + "
            "%d api_request_log (>%dd) row(s)",
            result.entity_changes,
            retention.entity_changes,
            result.deleted_entities,
            retention.deleted_entities,
            result.api_request_log,
            retention.api_request_log,
        )
    finally:
        await conn.close()


def build_arg_parser() -> argparse.ArgumentParser:
    """The script's parser: DSN flags, ``--execute``, and one window flag per table."""
    parser = build_parser(__doc__)
    add_dsn_args(parser)
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Commit deletes (default is dry run)",
    )
    for flag, field in _WINDOW_FLAGS.items():
        default = getattr(DEFAULT_RETENTION, field)
        parser.add_argument(
            flag,
            dest=field,
            type=int,
            default=default,
            help=f"{field} retention window in days (default {default})",
        )
    return parser


def retention_from_args(args: argparse.Namespace) -> Retention:
    """The :class:`Retention` the parsed window flags name."""
    return Retention(**{field: getattr(args, field) for field in _WINDOW_FLAGS.values()})


def main() -> None:
    configure_logging()
    parser = build_arg_parser()
    args = parser.parse_args()
    # Validated per flag, so the usage error names what the operator typed
    # (Retention re-checks, naming its field, for any other caller).
    for flag, field in _WINDOW_FLAGS.items():
        if getattr(args, field) < 1:
            parser.error(f"{flag} must be >= 1")
    retention = retention_from_args(args)
    # Resolved after validation: the echo means "about to connect", so it must
    # not precede a usage error that ends the run without one. See
    # docs/RUNBOOKS.md §"Operational scripts — dry run by default & target echo".
    dsn = resolve_dsn(args, parser)
    asyncio.run(run(dsn, execute=args.execute, retention=retention))


if __name__ == "__main__":
    main()
