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
import sys

import asyncpg

from scripts._dsn import add_dsn_args, resolve_dsn
from src.core.ancillary_migrate import (
    count_orphaned_citations,
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
        citation_counts = await count_orphaned_citations(conn)
    finally:
        await conn.close()

    # Namespace by scope so a table name shared across scopes stays distinct.
    counts = {
        **{f"role_assignment.{t}": n for t, n in ra_counts.items()},
        **{f"role.{t}": n for t, n in role_counts.items()},
        **{f"citation.{t}": n for t, n in citation_counts.items()},
    }
    total = sum(counts.values())
    if total == 0:
        logger.info("role/role_assignment/citation ancillary orphan audit: clean (0 orphans)")
        return 0

    breakdown = ", ".join(f"{table}={n}" for table, n in counts.items() if n)
    logger.warning(
        "ancillary orphans detected: %d total (%s) — "
        "run scripts.cleanup_role_assignment_ancillary_orphans (role_assignment scope) "
        "or triage citation.* / role.* manually",
        total,
        breakdown,
    )
    return 3


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    add_dsn_args(parser)
    args = parser.parse_args()
    dsn = resolve_dsn(args, parser)

    configure_logging()
    sys.exit(asyncio.run(_run(dsn)))


if __name__ == "__main__":
    main()
