"""Seed jurisdictions from a pre-seed JSON file.

Reads JSON files of the shape produced by the cannabis_observer pre-seed
bootstrap (see data/cannabis_observer/). Idempotent — safe to re-run.

Usage:
    uv run python -m scripts.seed_jurisdictions <path-to-seed.json>            # dry run
    uv run python -m scripts.seed_jurisdictions <path-to-seed.json> --execute  # commit
"""

import argparse
import asyncio
import json
import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import asyncpg

from src.core.db import generate_id
from src.core.logging import configure_logging, get_logger

logger = get_logger(__name__)


def load_seed_file(path: Path) -> dict[str, Any]:
    """Load and return the seed JSON (jurisdictions + relationships arrays)."""
    with path.open() as f:
        return json.load(f)


async def upsert_jurisdictions(conn: asyncpg.Connection, rows: Iterator[dict]) -> int:
    """Upsert jurisdiction rows; also seed jur_slug identifier when missing.

    Returns the number of rows processed.
    """
    type_rows = await conn.fetch("SELECT id, slug FROM jurisdiction_types")
    type_map = {r["slug"]: r["id"] for r in type_rows}

    jur_slug_type_id = await conn.fetchval(
        "SELECT id FROM entity_identifier_types WHERE slug = 'jur_slug'"
    )

    count = 0
    for row in rows:
        type_id = type_map.get(row["type"])
        if type_id is None:
            raise ValueError(f"Unknown jurisdiction type: {row['type']!r}")

        jur_id = await conn.fetchval(
            """
            INSERT INTO jurisdictions (id, slug, name, type_id)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (slug) DO UPDATE
                SET name    = EXCLUDED.name,
                    type_id = EXCLUDED.type_id
            RETURNING id
            """,
            generate_id(),
            row["slug"],
            row["name"],
            type_id,
        )

        if jur_slug_type_id:
            await conn.execute(
                """
                INSERT INTO identifiers (id, entity_id, entity_identifier_type_id, value)
                SELECT $1, $2, $3, $4
                WHERE NOT EXISTS (
                    SELECT 1 FROM identifiers
                    WHERE entity_id = $2
                      AND entity_identifier_type_id = $3
                )
                """,
                generate_id(),
                jur_id,
                jur_slug_type_id,
                row["slug"],
            )

        count += 1

    return count


async def upsert_jurisdiction_relationships(conn: asyncpg.Connection, rows: Iterator[dict]) -> int:
    """Insert jurisdiction relationships that do not already exist.

    Returns the number of rows processed (not necessarily inserted — existing
    edges are silently skipped).
    """
    rel_type_rows = await conn.fetch("SELECT id, slug FROM jurisdiction_relationship_types")
    rel_type_map = {r["slug"]: r["id"] for r in rel_type_rows}

    count = 0
    for row in rows:
        rel_type_id = rel_type_map.get(row["relationship_type"])
        if rel_type_id is None:
            raise ValueError(f"Unknown relationship type: {row['relationship_type']!r}")

        from_id = await conn.fetchval(
            "SELECT id FROM jurisdictions WHERE slug = $1", row["subject_slug"]
        )
        if from_id is None:
            raise ValueError(f"Unknown jurisdiction slug (subject): {row['subject_slug']!r}")

        to_id = await conn.fetchval(
            "SELECT id FROM jurisdictions WHERE slug = $1", row["object_slug"]
        )
        if to_id is None:
            raise ValueError(f"Unknown jurisdiction slug (object): {row['object_slug']!r}")

        await conn.execute(
            """
            INSERT INTO jurisdiction_relationships (id, from_id, to_id, rel_type_id)
            SELECT $1, $2, $3, $4
            WHERE NOT EXISTS (
                SELECT 1 FROM jurisdiction_relationships
                WHERE from_id     = $2
                  AND to_id       = $3
                  AND rel_type_id = $4
                  AND superseded_at IS NULL
            )
            """,
            generate_id(),
            from_id,
            to_id,
            rel_type_id,
        )

        count += 1

    return count


async def run(seed_path: Path, *, execute: bool) -> None:
    """Load seed file and upsert all jurisdictions + relationships."""
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        raise RuntimeError("DATABASE_URL not set")

    data = load_seed_file(seed_path)
    jur_rows = data.get("jurisdictions", [])
    rel_rows = data.get("relationships", [])

    logger.info(
        "Loaded %d jurisdictions, %d relationships from %s%s",
        len(jur_rows),
        len(rel_rows),
        seed_path,
        " (dry run)" if not execute else "",
    )

    if not execute:
        logger.info("Dry run — pass --execute to commit changes")
        return

    conn = await asyncpg.connect(dsn)
    try:
        async with conn.transaction():
            n_jur = await upsert_jurisdictions(conn, iter(jur_rows))
            n_rel = await upsert_jurisdiction_relationships(conn, iter(rel_rows))
        logger.info("Seeded %d jurisdiction(s), %d relationship(s)", n_jur, n_rel)
    finally:
        await conn.close()


def main() -> None:
    configure_logging()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("seed_file", type=Path, help="Path to the seed JSON file")
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Commit changes (default is dry run)",
    )
    args = parser.parse_args()
    asyncio.run(run(args.seed_file, execute=args.execute))


if __name__ == "__main__":
    main()
