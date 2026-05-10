"""One-time cleanup: collapse duplicate ``links`` rows by URL natural key.

Issue #142 introduces a UNIQUE INDEX on
``(entity_type, entity_id, url, link_type_id)``. Any pre-existing prod rows
violating that key would block ``CREATE UNIQUE INDEX``, so this script must
run before the schema migration commits.

For each duplicate group, the row with the lowest ``id`` (which, given ULID
ordering, is the oldest write) is kept; the rest are deleted. The script is
safe to re-run: a clean DB produces a no-op.

Usage:
    uv run python -m scripts.dedup_links            # dry run (default)
    uv run python -m scripts.dedup_links --execute  # commit changes

Requires the DATABASE_URL environment variable.
"""

import argparse
import asyncio
import os
from dataclasses import dataclass

import asyncpg

from src.core.logging import configure_logging, get_logger

logger = get_logger(__name__)


@dataclass
class ConsolidationResult:
    """Summary of what was (or would be) removed."""

    rows_removed: int
    dry_run: bool


_DELETE_DUPLICATES_SQL = """
DELETE FROM links
WHERE id IN (
    SELECT id FROM (
        SELECT id, ROW_NUMBER() OVER (
            PARTITION BY entity_type, entity_id, url, link_type_id
            ORDER BY created_at, id
        ) AS rn
        FROM links
    ) ranked
    WHERE ranked.rn > 1
)
"""


async def _do_consolidation(conn: asyncpg.Connection) -> int:
    """Delete all but the oldest row per natural-key group. Returns deletion count."""
    dup_groups = await conn.fetchval(
        "SELECT count(*) FROM ("
        "  SELECT 1 FROM links"
        "  GROUP BY entity_type, entity_id, url, link_type_id"
        "  HAVING count(*) > 1"
        ") g"
    )
    if not dup_groups:
        logger.info("links dedup: no duplicate groups found — nothing to do")
        return 0
    logger.info("links dedup: %d duplicate groups found", dup_groups)
    result = await conn.execute(_DELETE_DUPLICATES_SQL)
    return int(result.split()[-1])


async def run_consolidation(
    conn: asyncpg.Connection, dry_run: bool = True
) -> ConsolidationResult:
    """Consolidate duplicate ``links`` rows.

    On ``dry_run=True``, all SQL runs inside a savepoint that is rolled back
    so no changes persist; the count still reflects what would be removed.
    On ``dry_run=False``, changes are committed.
    """
    sp = conn.transaction()
    await sp.start()
    try:
        rows_removed = await _do_consolidation(conn)
    except Exception:
        await sp.rollback()
        raise

    if dry_run:
        await sp.rollback()
    else:
        await sp.commit()

    return ConsolidationResult(rows_removed=rows_removed, dry_run=dry_run)


async def _main() -> None:
    """Entry point: parse args, connect to DB, run consolidation."""
    configure_logging()

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Commit changes. Default is dry run (no changes made).",
    )
    args = parser.parse_args()
    dry_run = not args.execute

    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        raise SystemExit("DATABASE_URL environment variable is required")

    conn = await asyncpg.connect(dsn)
    try:
        result = await run_consolidation(conn, dry_run=dry_run)
    finally:
        await conn.close()

    mode = "DRY RUN" if result.dry_run else "EXECUTED"
    print(f"\n[{mode}] Links consolidation complete:")
    print(f"  Rows removed: {result.rows_removed}")
    if result.dry_run:
        print("\nRe-run with --execute to apply changes.")


if __name__ == "__main__":
    asyncio.run(_main())
