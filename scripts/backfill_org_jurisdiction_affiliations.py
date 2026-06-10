"""Backfill organization_jurisdiction_affiliations for existing orgs.

Issue #194: adds the org-jurisdiction affiliation model. This script is a
template for operators to populate affiliations for orgs that existed before
this migration. It reads a CSV with columns ``org_id``, ``jurisdiction_id``,
``affiliation_type_slug`` and inserts the corresponding rows (idempotent).

CSV format (no header assumed — pass --has-header if the first row is headers):
    <org_ulid>,<jurisdiction_ulid_or_slug>,<affiliation_type_slug>

Example:
    01KT...,usa-wa,governing

Usage:
    uv run python -m scripts.backfill_org_jurisdiction_affiliations affiliations.csv
    uv run python -m scripts.backfill_org_jurisdiction_affiliations affiliations.csv --execute
"""

import argparse
import asyncio
import csv
import os
import sys

import asyncpg

from src.core.db import generate_id
from src.core.logging import configure_logging, get_logger

logger = get_logger(__name__)


async def main(csv_path: str, execute: bool, has_header: bool) -> None:
    """Read CSV and insert affiliation rows."""
    database_url = os.environ["DATABASE_URL"]
    conn = await asyncpg.connect(database_url)
    try:
        with open(csv_path, newline="") as fh:
            reader = csv.reader(fh)
            if has_header:
                next(reader)

            inserted = 0
            skipped = 0
            errors = 0

            for i, row in enumerate(reader, start=2 if has_header else 1):
                if len(row) != 3:
                    logger.error("row %d: expected 3 columns, got %d — skipping", i, len(row))
                    errors += 1
                    continue

                org_id, jur_ref, aff_type_slug = [c.strip() for c in row]

                # Resolve jurisdiction by ULID or slug.
                jur_id = await conn.fetchval(
                    "SELECT id FROM jurisdictions WHERE id=$1 OR slug=$1", jur_ref
                )
                if jur_id is None:
                    logger.error("row %d: jurisdiction not found: %r", i, jur_ref)
                    errors += 1
                    continue

                type_id = await conn.fetchval(
                    "SELECT id FROM organization_jurisdiction_affiliation_types WHERE slug=$1",
                    aff_type_slug,
                )
                if type_id is None:
                    logger.error("row %d: unknown affiliation_type_slug: %r", i, aff_type_slug)
                    errors += 1
                    continue

                exists = await conn.fetchval(
                    """
                    SELECT 1 FROM organization_jurisdiction_affiliations
                    WHERE organization_id=$1 AND jurisdiction_id=$2 AND affiliation_type_id=$3
                    """,
                    org_id,
                    jur_id,
                    type_id,
                )
                if exists:
                    logger.info(
                        "row %d: already exists org=%s jur=%s type=%s — skip",
                        i,
                        org_id,
                        jur_id,
                        aff_type_slug,
                    )
                    skipped += 1
                    continue

                if execute:
                    await conn.execute(
                        """
                        INSERT INTO organization_jurisdiction_affiliations
                            (id, organization_id, jurisdiction_id, affiliation_type_id)
                        VALUES ($1, $2, $3, $4)
                        ON CONFLICT (organization_id, jurisdiction_id, affiliation_type_id)
                        DO NOTHING
                        """,
                        generate_id(),
                        org_id,
                        jur_id,
                        type_id,
                    )
                    logger.info("inserted: org=%s jur=%s type=%s", org_id, jur_id, aff_type_slug)
                else:
                    logger.info(
                        "dry-run: would insert org=%s jur=%s type=%s",
                        org_id,
                        jur_id,
                        aff_type_slug,
                    )
                inserted += 1

        logger.info(
            "done: %d %s, %d skipped, %d errors",
            inserted,
            "inserted" if execute else "would insert",
            skipped,
            errors,
        )
        if errors:
            sys.exit(1)
    finally:
        await conn.close()


if __name__ == "__main__":
    configure_logging()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv_path", help="Path to CSV file")
    parser.add_argument("--execute", action="store_true", help="Commit changes (default: dry run)")
    parser.add_argument("--has-header", action="store_true", help="Skip first row of CSV")
    args = parser.parse_args()
    asyncio.run(main(args.csv_path, args.execute, args.has_header))
