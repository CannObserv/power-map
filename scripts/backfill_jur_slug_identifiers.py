"""Backfill jur_slug identifiers for existing jurisdictions.

Issue #183: adds the ``jur_slug`` entity_identifier_type so sibling services
can match jurisdictions by slug. Jurisdictions created before this migration
have no ``jur_slug`` identifier row; this script inserts one for each, using
the slug already stored on the ``jurisdictions`` row.

Idempotent — the ``WHERE NOT EXISTS`` query skips jurisdictions that already
have a ``jur_slug`` identifier row.

Usage:
    uv run python -m scripts.backfill_jur_slug_identifiers            # dry run
    uv run python -m scripts.backfill_jur_slug_identifiers --execute  # commit
"""

import argparse
import asyncio

import asyncpg

from scripts._dsn import add_dsn_args, resolve_dsn
from src.core.db import generate_id
from src.core.logging import configure_logging, get_logger

logger = get_logger(__name__)

_FIND_MISSING_SQL = """
SELECT j.id, j.slug
FROM jurisdictions j
WHERE NOT EXISTS (
    SELECT 1
    FROM identifiers i
    JOIN entity_identifier_types t ON t.id = i.entity_identifier_type_id
    WHERE i.entity_id = j.id
      AND t.slug = 'jur_slug'
)
ORDER BY j.created_at
"""

_INSERT_SQL = """
INSERT INTO identifiers (id, entity_id, entity_identifier_type_id, value)
VALUES ($1, $2, $3, $4)
"""


async def run(dsn: str, *, execute: bool) -> None:
    """Backfill jur_slug identifiers for jurisdictions that lack one."""
    conn = await asyncpg.connect(dsn)
    try:
        jur_slug_eit = await conn.fetchrow(
            "SELECT id FROM entity_identifier_types WHERE slug = 'jur_slug'"
        )
        if jur_slug_eit is None:
            raise RuntimeError("jur_slug identifier type not found — run apply_schema first")

        jur_slug_type_id = jur_slug_eit["id"]
        rows = await conn.fetch(_FIND_MISSING_SQL)

        if not rows:
            logger.info("No jurisdictions missing jur_slug identifiers — nothing to do")
            return

        logger.info(
            "%d jurisdiction(s) missing jur_slug identifier%s",
            len(rows),
            " (dry run)" if not execute else "",
        )
        for row in rows:
            logger.info("  %s  slug=%r", row["id"], row["slug"])

        if not execute:
            logger.info("Dry run — pass --execute to commit changes")
            return

        async with conn.transaction():
            for row in rows:
                await conn.execute(
                    _INSERT_SQL,
                    generate_id(),
                    row["id"],
                    jur_slug_type_id,
                    row["slug"],
                )

        logger.info("Backfilled %d jur_slug identifier(s)", len(rows))
    finally:
        await conn.close()


def main() -> None:
    configure_logging()
    parser = argparse.ArgumentParser(description=__doc__)
    add_dsn_args(parser)
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Commit changes (default is dry run)",
    )
    args = parser.parse_args()
    dsn = resolve_dsn(args, parser)
    asyncio.run(run(dsn, execute=args.execute))


if __name__ == "__main__":
    main()
