"""One-time migration script: collapse duplicate roles and role_assignments.

Duplicates arose when the import pipeline was re-run with modified CSV files
(new file hash → new batch) before the role_index was pre-populated from the DB.

Usage:
    uv run python -m scripts.deduplicate_roles          # dry run (default)
    uv run python -m scripts.deduplicate_roles --execute  # commit changes

Requires DATABASE_URL environment variable.
"""

import argparse
import asyncio
import os
from dataclasses import dataclass

import asyncpg

from src.core.logging import configure_logging, get_logger

logger = get_logger(__name__)


@dataclass
class DeduplicationResult:
    """Summary of what was (or would be) removed."""

    roles_removed: int
    assignments_removed: int
    dry_run: bool


async def _do_deduplication(conn: asyncpg.Connection) -> tuple[int, int]:
    """Execute deduplication SQL. Returns (roles_removed, assignments_removed).

    Performs raw DML with no transaction management of its own; the caller
    (run_deduplication) wraps this in a savepoint for dry-run support.
    """
    roles_removed = 0
    assignments_removed = 0

    # ------------------------------------------------------------------
    # Step A: deduplicate roles
    # ------------------------------------------------------------------
    dup_role_groups = await conn.fetch(
        """
        SELECT
            organization_id,
            lower(title) AS title_lower,
            min(id)      AS canonical_id,
            array_agg(id ORDER BY id) AS all_ids,
            count(*)     AS cnt
        FROM roles
        WHERE archived_at IS NULL
        GROUP BY organization_id, lower(title)
        HAVING count(*) > 1
        """
    )

    for group in dup_role_groups:
        canonical_id: str = group["canonical_id"]
        dup_ids: list[str] = [i for i in group["all_ids"] if i != canonical_id]

        logger.info(
            "role dedup: org=%s title=%r canonical=%s duplicates=%s",
            group["organization_id"], group["title_lower"],
            canonical_id, dup_ids,
        )

        # Assignments on duplicate roles that would conflict when re-pointed
        # (person+canonical_role+start_date already exists) must be deleted first.
        # Orphaned children are cleaned up before the assignment is removed.
        conflicting_ra_ids: list[str] = [
            row["id"]
            for row in await conn.fetch(
                """
                SELECT ra_dup.id
                FROM role_assignments ra_dup
                WHERE ra_dup.role_id = ANY($2::text[])
                  AND ra_dup.archived_at IS NULL
                  AND EXISTS (
                      SELECT 1 FROM role_assignments ra_can
                      WHERE ra_can.role_id = $1
                        AND ra_can.person_id = ra_dup.person_id
                        AND ra_can.start_date IS NOT DISTINCT FROM ra_dup.start_date
                        AND ra_can.archived_at IS NULL
                  )
                """,
                canonical_id, dup_ids,
            )
        ]
        if conflicting_ra_ids:
            for table in ("contact_methods", "identifiers", "field_confidence"):
                await conn.execute(
                    f"DELETE FROM {table} WHERE entity_id = ANY($1::text[])",  # noqa: S608
                    conflicting_ra_ids,
                )
            await conn.execute(
                "DELETE FROM links"
                " WHERE entity_type = 'role_assignment' AND entity_id = ANY($1::text[])",
                conflicting_ra_ids,
            )
            await conn.execute(
                "DELETE FROM role_assignments WHERE id = ANY($1::text[])",
                conflicting_ra_ids,
            )
            assignments_removed += len(conflicting_ra_ids)

        # Re-point remaining assignments to canonical role (no conflicts possible now)
        await conn.execute(
            "UPDATE role_assignments SET role_id = $1 WHERE role_id = ANY($2::text[])",
            canonical_id, dup_ids,
        )

        # Migrate role links that won't conflict; delete any remaining
        await conn.execute(
            """
            UPDATE links
            SET entity_id = $1
            WHERE entity_type = 'role'
              AND entity_id = ANY($2::text[])
              AND NOT (
                  is_canonical = TRUE
                  AND EXISTS (
                      SELECT 1 FROM links l2
                      WHERE l2.entity_type = 'role'
                        AND l2.entity_id = $1
                        AND l2.is_canonical = TRUE
                  )
              )
            """,
            canonical_id, dup_ids,
        )
        await conn.execute(
            "DELETE FROM links WHERE entity_type = 'role' AND entity_id = ANY($1::text[])",
            dup_ids,
        )

        deleted = await conn.execute(
            "DELETE FROM roles WHERE id = ANY($1::text[])",
            dup_ids,
        )
        roles_removed += int(deleted.split()[-1])

    # ------------------------------------------------------------------
    # Step B: deduplicate role_assignments
    # ------------------------------------------------------------------
    dup_ra_groups = await conn.fetch(
        """
        SELECT
            person_id,
            role_id,
            start_date,
            min(id)      AS canonical_id,
            array_agg(id ORDER BY id) AS all_ids,
            count(*)     AS cnt
        FROM role_assignments
        WHERE archived_at IS NULL
        GROUP BY person_id, role_id, start_date
        HAVING count(*) > 1
        """
    )

    for group in dup_ra_groups:
        canonical_id = group["canonical_id"]
        dup_ids = [i for i in group["all_ids"] if i != canonical_id]

        logger.info(
            "assignment dedup: person=%s role=%s start=%s canonical=%s duplicates=%s",
            group["person_id"], group["role_id"], group["start_date"],
            canonical_id, dup_ids,
        )

        # Migrate polymorphic children (no unique constraints — always safe).
        # import_provenance is left on the duplicate rows intentionally: it is an
        # audit log that records which import batch created each assignment.
        for table in ("contact_methods", "identifiers", "field_confidence"):
            await conn.execute(
                f"UPDATE {table} SET entity_id = $1 WHERE entity_id = ANY($2::text[])",  # noqa: S608
                canonical_id, dup_ids,
            )

        # Links: migrate non-conflicting canonical links; delete any remaining
        await conn.execute(
            """
            UPDATE links
            SET entity_id = $1
            WHERE entity_type = 'role_assignment'
              AND entity_id = ANY($2::text[])
              AND NOT (
                  is_canonical = TRUE
                  AND EXISTS (
                      SELECT 1 FROM links l2
                      WHERE l2.entity_type = 'role_assignment'
                        AND l2.entity_id = $1
                        AND l2.is_canonical = TRUE
                  )
              )
            """,
            canonical_id, dup_ids,
        )
        await conn.execute(
            "DELETE FROM links"
            " WHERE entity_type = 'role_assignment' AND entity_id = ANY($1::text[])",
            dup_ids,
        )

        deleted = await conn.execute(
            "DELETE FROM role_assignments WHERE id = ANY($1::text[])",
            dup_ids,
        )
        assignments_removed += int(deleted.split()[-1])

    return roles_removed, assignments_removed


async def run_deduplication(
    conn: asyncpg.Connection, dry_run: bool = True
) -> DeduplicationResult:
    """Collapse duplicate roles and role_assignments.

    On dry_run=True, all SQL runs inside a savepoint that is rolled back so no
    changes persist; counts still reflect what would be removed.
    On dry_run=False, changes are committed.
    """
    sp = conn.transaction()
    await sp.start()
    try:
        roles_removed, assignments_removed = await _do_deduplication(conn)
    except Exception:
        await sp.rollback()
        raise

    if dry_run:
        await sp.rollback()
    else:
        await sp.commit()

    return DeduplicationResult(
        roles_removed=roles_removed,
        assignments_removed=assignments_removed,
        dry_run=dry_run,
    )


async def _main() -> None:
    """Entry point: parse args, connect to DB, run deduplication."""
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
        result = await run_deduplication(conn, dry_run=dry_run)
    finally:
        await conn.close()

    mode = "DRY RUN" if result.dry_run else "EXECUTED"
    print(f"\n[{mode}] Deduplication complete:")
    print(f"  Roles removed:       {result.roles_removed}")
    print(f"  Assignments removed: {result.assignments_removed}")
    if result.dry_run:
        print("\nRe-run with --execute to apply changes.")


if __name__ == "__main__":
    asyncio.run(_main())
