#!/usr/bin/env python3
"""CLI entry point: import Cannabis Observer CSV exports into PostgreSQL.

Usage:
    uv run python scripts/import_cannabis_observer.py \\
        --orgs   data/cannabis_observer/Organizations.csv \\
        --people data/cannabis_observer/People.csv \\
        --roles  data/cannabis_observer/Roles.csv           # dry run

    ... --execute                                            # commit

Dry run by default (#402). The default `DATABASE_URL` is **production**, from
any directory, and before #402 a bare invocation applied schema DDL and
committed the whole import with no confirmation. A dry run runs the real
pipeline inside a transaction it then rolls back, so the summary it prints is
the summary --execute would produce. Addresses are the one deliberate
difference: a dry run parses them locally rather than spending the
rate-limited external validator's quota on a run that changes nothing, so
address fields in a preview may differ from a committed run.

Schema DDL is no longer implicit: `scripts/apply-schema.sh` owns applying
schema.sql and carries the #398 production guards. `--apply-schema` remains for
the fresh-database case and requires `--execute` — applying DDL inside a run
that is about to be rolled back would be a lie.

Environment variables:
    DATABASE_URL             — PostgreSQL DSN (written by scripts/setup-db.sh);
                                override per-run with --database-url.
    ADDRESS_VALIDATOR_API_KEY — Required for external address standardization.
                                Loaded from /etc/power-map/.env in production.
                                Without it, addresses are parsed locally only.
    VALIDATE_ADDRESSES       — Set to '1'/'true'/'yes' to enable /validate
                                endpoint (equivalent to --validate-addresses).
"""

import argparse
import asyncio
import sys
from pathlib import Path

import asyncpg

from scripts._dsn import add_dsn_args, resolve_dsn
from src.core.db import apply_schema
from src.core.ingestion.pipeline import ImportConfig, run_import
from src.core.logging import configure_logging, get_logger

logger = get_logger(__name__)


class _DryRunRollback(Exception):
    """Internal sentinel: unwinds the dry-run transaction. Never escapes ``run``."""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Import Cannabis Observer CSV data into PostgreSQL."
    )
    parser.add_argument("--orgs", type=Path, required=True, help="Path to Organizations.csv")
    parser.add_argument("--people", type=Path, required=True, help="Path to People.csv")
    parser.add_argument("--roles", type=Path, required=True, help="Path to Roles.csv")
    add_dsn_args(parser)
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Commit the import (default is a dry run, rolled back).",
    )
    parser.add_argument(
        "--apply-schema",
        action="store_true",
        help=(
            "Apply schema.sql before importing — for a fresh database only. "
            "Requires --execute. Prefer scripts/apply-schema.sh, which carries "
            "the production guards."
        ),
    )
    parser.add_argument(
        "--source-reliability",
        type=float,
        default=0.8,
        help="Source reliability score (0.0–1.0). Default: 0.8",
    )
    parser.add_argument(
        "--validate-addresses",
        action="store_true",
        help=(
            "Also call /validate endpoint for deliverability confirmation "
            "(rate-limited). Addresses are always standardized via "
            "ADDRESS_VALIDATOR_API_KEY when the key is set."
        ),
    )
    parser.add_argument("--imported-by", default="cannabis-observer-csv-import")
    return parser


async def run(dsn: str, config: ImportConfig, *, execute: bool, apply_schema_first: bool) -> None:
    """Import *config*. Dry run (rolled back) unless ``execute``."""
    conn = await asyncpg.connect(dsn)
    try:
        if apply_schema_first:
            await apply_schema(conn)

        if execute:
            summary = await run_import(conn, config)
        else:
            summary = None
            try:
                async with conn.transaction():
                    summary = await run_import(conn, config)
                    raise _DryRunRollback
            except _DryRunRollback:
                pass

        logger.info("import summary: %s", summary)
        if not execute:
            # Diagnostics go to stderr alongside the target echo, so redirecting
            # one stream never leaves half the story.
            print(
                "dry run — rolled back; addresses parsed locally (no validator "
                "calls), so address fields may differ from a committed run. "
                "Pass --execute to commit.",
                file=sys.stderr,
            )
    finally:
        await conn.close()


def main(argv: list[str] | None = None) -> None:
    configure_logging()
    parser = build_parser()
    args = parser.parse_args(argv)

    for path in (args.orgs, args.people, args.roles):
        if not path.exists():
            raise SystemExit(f"File not found: {path}")

    if not 0.0 <= args.source_reliability <= 1.0:
        raise SystemExit("--source-reliability must be between 0.0 and 1.0")

    if args.apply_schema and not args.execute:
        parser.error("--apply-schema requires --execute (a dry run is rolled back)")

    dsn = resolve_dsn(args, parser)

    config = ImportConfig(
        orgs_csv=args.orgs,
        people_csv=args.people,
        roles_csv=args.roles,
        imported_by=args.imported_by,
        source_reliability=args.source_reliability,
        validate_addresses=args.validate_addresses,
        # A preview must not spend the rate-limited validator quota — and
        # standardization fires whenever ADDRESS_VALIDATOR_API_KEY is set,
        # independent of --validate-addresses, so this is the only lever.
        local_addresses_only=not args.execute,
    )
    asyncio.run(
        run(
            dsn,
            config,
            execute=args.execute,
            apply_schema_first=args.apply_schema,
        )
    )


if __name__ == "__main__":
    main()
