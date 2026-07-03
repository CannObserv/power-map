"""Seed legislative seat-Roles from a seat seed JSON file (#263).

Reads a file of the shape produced by ``scripts.generate_wa_seats`` (a ``seats``
array; see ``data/cannabis_observer/*-usa_wa-legislative-seats.json``). Each seat
references its chamber by the ``org_wa_legislature_chamber`` identifier value,
its district by jurisdiction slug, and its office by role_type slug. Creation
goes through ``resolve_role`` (#261), so the run is idempotent — re-running
attaches to existing seats rather than duplicating them.

Usage:
    uv run python -m scripts.seed_role_seats <path-to-seats.json>            # dry run
    uv run python -m scripts.seed_role_seats <path-to-seats.json> --execute  # commit
"""

import argparse
import asyncio
import json
import os
from pathlib import Path
from typing import Any

import asyncpg

from src.core.logging import configure_logging, get_logger
from src.core.observation import Disposition, resolve_role

logger = get_logger(__name__)


def load_seed_file(path: Path) -> dict[str, Any]:
    """Load and return the seat seed JSON (a ``seats`` array)."""
    with path.open() as f:
        return json.load(f)


async def seed_seats(conn: asyncpg.Connection, seats: list[dict[str, Any]]) -> dict[str, int]:
    """Create-or-attach each seat via resolve_role. Returns {new, attached, rejected}."""
    counts = {"new": 0, "attached": 0, "rejected": 0}
    for seat in seats:
        org_id = await conn.fetchval(
            "SELECT i.entity_id FROM identifiers i"
            " JOIN entity_identifier_types t ON t.id = i.entity_identifier_type_id"
            " WHERE t.slug = 'org_wa_legislature_chamber' AND i.value = $1",
            seat["chamber"],
        )
        if org_id is None:
            logger.warning("seat rejected: unknown chamber=%r (%s)", seat["chamber"], seat["title"])
            counts["rejected"] += 1
            continue

        jurisdiction_id = await conn.fetchval(
            "SELECT id FROM jurisdictions WHERE slug = $1", seat["jurisdiction_slug"]
        )
        if jurisdiction_id is None:
            logger.warning(
                "seat rejected: unknown jurisdiction_slug=%r (%s)",
                seat["jurisdiction_slug"],
                seat["title"],
            )
            counts["rejected"] += 1
            continue

        _role_id, disposition, reason = await resolve_role(
            conn,
            org_id,
            seat["title"],
            role_type=seat["role_type"],
            jurisdiction_id=jurisdiction_id,
            qualifier=seat["qualifier"],
        )
        if disposition is Disposition.NEW:
            counts["new"] += 1
        elif disposition is Disposition.AUTO_ATTACHED:
            counts["attached"] += 1
        else:
            logger.warning("seat rejected: %s (%s)", seat["title"], reason)
            counts["rejected"] += 1
    return counts


async def run(seed_path: Path, *, execute: bool) -> None:
    """Load the seed file and seed seats. Dry run unless ``execute``."""
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        raise RuntimeError("DATABASE_URL not set")

    seats = load_seed_file(seed_path).get("seats", [])
    logger.info(
        "Loaded %d seat(s) from %s%s",
        len(seats),
        seed_path,
        " (dry run)" if not execute else "",
    )
    if not execute:
        logger.info("Dry run — pass --execute to commit changes")
        return

    conn = await asyncpg.connect(dsn)
    try:
        async with conn.transaction():
            counts = await seed_seats(conn, seats)
        logger.info(
            "Seeded seats: %d new, %d attached, %d rejected",
            counts["new"],
            counts["attached"],
            counts["rejected"],
        )
    finally:
        await conn.close()


def main() -> None:
    configure_logging()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("seed_file", type=Path, help="Path to the seat seed JSON file")
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Commit changes (default is dry run)",
    )
    args = parser.parse_args()
    asyncio.run(run(args.seed_file, execute=args.execute))


if __name__ == "__main__":
    main()
