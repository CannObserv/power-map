"""Audit and backfill the addresses.precision column.

Classifies existing address rows into precision tiers based on which
structured fields are populated, then (with --execute) writes the derived
tier back to ``addresses.precision``.

Usage:
    uv run python -m scripts.audit_address_precision            # dry run
    uv run python -m scripts.audit_address_precision --execute  # backfill

Requires DATABASE_URL environment variable.

Precision tier logic (evaluated in order):
    street        — address_line_1 IS NOT NULL
    postal        — postal_code IS NOT NULL, address_line_1 IS NULL
    city          — city IS NOT NULL, postal_code IS NULL, address_line_1 IS NULL
    region        — region IS NOT NULL, city IS NULL, postal_code IS NULL, address_line_1 IS NULL
    country       — country IS NOT NULL, all other structured fields NULL
    unclassifiable — none of the above (e.g. only raw_input populated)

Unclassifiable rows are reported but never updated (precision stays NULL).
"""

import argparse
import asyncio
import os

import asyncpg

# ---------------------------------------------------------------------------
# SQL helpers
# ---------------------------------------------------------------------------

_CLASSIFY_SQL = """
SELECT
    id,
    CASE
        WHEN address_line_1 IS NOT NULL
            THEN 'street'
        WHEN postal_code IS NOT NULL AND address_line_1 IS NULL
            THEN 'postal'
        WHEN city IS NOT NULL AND postal_code IS NULL AND address_line_1 IS NULL
            THEN 'city'
        WHEN region IS NOT NULL AND city IS NULL
             AND postal_code IS NULL AND address_line_1 IS NULL
            THEN 'region'
        WHEN country IS NOT NULL AND region IS NULL AND city IS NULL
             AND postal_code IS NULL AND address_line_1 IS NULL
            THEN 'country'
        ELSE 'unclassifiable'
    END AS tier
FROM addresses
WHERE precision IS NULL
ORDER BY id
"""

_UPDATE_SQL = """
UPDATE addresses
SET precision = tier_data.tier
FROM (
    SELECT
        id,
        CASE
            WHEN address_line_1 IS NOT NULL
                THEN 'street'
            WHEN postal_code IS NOT NULL AND address_line_1 IS NULL
                THEN 'postal'
            WHEN city IS NOT NULL AND postal_code IS NULL AND address_line_1 IS NULL
                THEN 'city'
            WHEN region IS NOT NULL AND city IS NULL
                 AND postal_code IS NULL AND address_line_1 IS NULL
                THEN 'region'
            WHEN country IS NOT NULL AND region IS NULL AND city IS NULL
                 AND postal_code IS NULL AND address_line_1 IS NULL
                THEN 'country'
            ELSE NULL
        END AS tier
    FROM addresses
    WHERE precision IS NULL
) AS tier_data
WHERE addresses.id = tier_data.id
  AND tier_data.tier IS NOT NULL
"""

# ---------------------------------------------------------------------------
# Core logic
# ---------------------------------------------------------------------------

TIERS = ("street", "postal", "city", "region", "country", "unclassifiable")


async def classify(conn: asyncpg.Connection) -> dict[str, list[str]]:
    """Return mapping of tier → list of address IDs."""
    rows = await conn.fetch(_CLASSIFY_SQL)
    buckets: dict[str, list[str]] = {t: [] for t in TIERS}
    for row in rows:
        buckets[row["tier"]].append(row["id"])
    return buckets


async def backfill(conn: asyncpg.Connection) -> int:
    """Run the UPDATE and return the number of rows changed."""
    result = await conn.execute(_UPDATE_SQL)
    # asyncpg returns e.g. "UPDATE 142"
    return int(result.split()[-1])


def print_summary(buckets: dict[str, list[str]], *, dry_run: bool) -> None:
    """Print per-tier counts and any unclassifiable IDs."""
    mode = "dry run" if dry_run else "execute"
    print(f"\nAddress precision audit ({mode})")
    print("=" * 38)
    for tier in TIERS:
        print(f"  {tier:<15} {len(buckets[tier]):>5}")

    total = sum(len(v) for v in buckets.values())
    unclassifiable = buckets["unclassifiable"]
    print(f"\nTotal: {total} rows with precision IS NULL.", end="")
    if not total:
        print(" Nothing to do.")
        return
    if unclassifiable:
        print(f" {len(unclassifiable)} unclassifiable (will stay NULL).")
        print("\nUnclassifiable IDs:")
        for aid in unclassifiable:
            print(f"  {aid}")
    else:
        print(" All classified.")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


async def _main() -> None:
    """Parse args, connect, audit, and optionally backfill."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Write derived precision values. Default is dry run (no changes).",
    )
    args = parser.parse_args()
    dry_run = not args.execute

    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        raise SystemExit("DATABASE_URL environment variable is required")

    conn = await asyncpg.connect(dsn)
    try:
        buckets = await classify(conn)
        print_summary(buckets, dry_run=dry_run)

        classifiable = sum(len(v) for t, v in buckets.items() if t != "unclassifiable")

        if dry_run:
            if classifiable:
                print("\nRun with --execute to backfill precision column.")
        else:
            if classifiable:
                updated = await backfill(conn)
                print(f"\nUpdated {updated} rows. precision column backfilled.")
            else:
                print("\nNo rows to update.")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(_main())
