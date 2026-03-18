#!/usr/bin/env python3
"""CLI entry point: import Cannabis Observer CSV exports into PostgreSQL.

Usage:
    uv run python scripts/import_cannabis_observer.py \\
        --orgs   data/cannabis_observer/Organizations.csv \\
        --people data/cannabis_observer/People.csv \\
        --roles  data/cannabis_observer/Roles.csv

Environment variables:
    DATABASE_URL             — PostgreSQL DSN (written by scripts/setup-db.sh)
    ADDRESS_VALIDATOR_API_KEY — Required for external address standardization.
                                Loaded from /etc/power-map/env in production.
                                Without it, addresses are parsed locally only.
    VALIDATE_ADDRESSES       — Set to '1'/'true'/'yes' to enable /validate
                                endpoint (equivalent to --validate-addresses).
"""

import argparse
import asyncio
import os
from pathlib import Path

import asyncpg

from src.core.db import apply_schema
from src.core.ingestion.pipeline import ImportConfig, run_import
from src.core.logging import configure_logging, get_logger

logger = get_logger(__name__)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Import Cannabis Observer CSV data into PostgreSQL."
    )
    parser.add_argument("--orgs",   type=Path, required=True, help="Path to Organizations.csv")
    parser.add_argument("--people", type=Path, required=True, help="Path to People.csv")
    parser.add_argument("--roles",  type=Path, required=True, help="Path to Roles.csv")
    parser.add_argument(
        "--source-reliability", type=float, default=0.8,
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
    return parser.parse_args()


async def main() -> None:
    configure_logging()
    args = parse_args()

    for path in (args.orgs, args.people, args.roles):
        if not path.exists():
            raise SystemExit(f"File not found: {path}")

    if not 0.0 <= args.source_reliability <= 1.0:
        raise SystemExit("--source-reliability must be between 0.0 and 1.0")

    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        raise SystemExit("DATABASE_URL is not set. Run: export $(cat env | xargs)")

    conn = await asyncpg.connect(dsn)
    try:
        await apply_schema(conn)
        config = ImportConfig(
            orgs_csv=args.orgs,
            people_csv=args.people,
            roles_csv=args.roles,
            imported_by=args.imported_by,
            source_reliability=args.source_reliability,
            validate_addresses=args.validate_addresses,
        )
        summary = await run_import(conn, config)
        logger.info("import summary: %s", summary)
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
