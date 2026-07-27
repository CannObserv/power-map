"""Daily guard: polymorphic ancillary orphaned off deleted roles/role_assignments.

Two entity types keep ancillary rows with **no FK**, so a merge dedup — or any
direct DELETE — that drops the parent can strand them undetected: they point at an
id that no longer exists, invisible to every UI and to the change feed, and are
never pruned.

- **role_assignment (#324):** ``links`` / ``contact_methods`` / ``field_confidence``
  / ``identifiers`` keyed on ``entity_type='role_assignment'``.
- **role (#326):** ``links`` / ``contact_methods`` keyed on ``entity_type='role'``.

The merge/delete paths now re-home (or drop) ancillary before deleting (see
``src.core.ancillary_migrate``), but this guard is a continuous backstop against
any path that doesn't — mirrors the schema-parity audit (#315). Read-only.

Exits 3 when any table has orphans (so the systemd unit shows as failed, visible
in ``systemctl --failed`` and a hook for future ``OnFailure=`` alerting). Exit 3
(not 2) stays distinct from argparse usage errors.

Usage:
    uv run python -m scripts.audit_ancillary_orphans
    uv run python -m scripts.audit_ancillary_orphans \
        --database-url "$TEST_DATABASE_URL"
"""

import argparse
import asyncio
import os
import sys

import asyncpg

from src.core.ancillary_migrate import (
    count_orphaned_role_ancillary,
    count_orphaned_role_assignment_ancillary,
)
from src.core.logging import configure_logging, get_logger

logger = get_logger(__name__)


async def _run(database_url: str) -> int:
    conn = await asyncpg.connect(database_url)
    try:
        ra_counts = await count_orphaned_role_assignment_ancillary(conn)
        role_counts = await count_orphaned_role_ancillary(conn)
    finally:
        await conn.close()

    # Namespace by entity type so a table name shared by both scopes stays distinct.
    counts = {
        **{f"role_assignment.{t}": n for t, n in ra_counts.items()},
        **{f"role.{t}": n for t, n in role_counts.items()},
    }
    total = sum(counts.values())
    if total == 0:
        logger.info("role/role_assignment ancillary orphan audit: clean (0 orphans)")
        return 0

    breakdown = ", ".join(f"{table}={n}" for table, n in counts.items() if n)
    logger.warning(
        "role/role_assignment ancillary orphans detected: %d total (%s) — "
        "run scripts.cleanup_role_assignment_ancillary_orphans",
        total,
        breakdown,
    )
    return 3


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database-url",
        default=os.environ.get("DATABASE_URL"),
        help="DSN to audit (default: DATABASE_URL).",
    )
    args = parser.parse_args()
    if not args.database_url:
        parser.error("no database URL: pass --database-url or set DATABASE_URL")

    configure_logging()
    sys.exit(asyncio.run(_run(args.database_url)))


if __name__ == "__main__":
    main()
