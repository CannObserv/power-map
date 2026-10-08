"""Daily guard: polymorphic rows orphaned off a deleted entity.

The polymorphic tables key a row on ``(entity_type, entity_id)`` with **no FK**,
so any path that drops the entity alone — a merge dedup, a script, raw SQL —
strands its rows undetected: they point at an id that no longer exists,
invisible to every UI and to the change feed, and are never pruned.

- **Every hard-deletable type (#324, #326, #609):** each polymorphic table × each
  type its CHECK admits — ``links`` / ``contact_methods`` / ``entity_addresses`` /
  ``field_confidence`` / ``import_provenance`` / ``identifiers`` (by identifier
  type, so a cross-type row counts too, #617) / ``curation_overlay`` /
  ``entity_events``, plus another entity's event still linking to the id (#611).
  Namespaced ``<entity_type>.<table>``; ``entity_events_linked`` is the inbound kind.
- **Citations (#319):** every citable type, namespaced ``citation.<entity_type>``.

The merge/delete paths re-home (or drop) these rows before deleting (see
``src.core.ancillary_migrate``); this guard is the continuous backstop against
any path that doesn't — mirrors the schema-parity audit (#315). Read-only.

Exits 3 when any table has orphans (so the systemd unit shows as failed, visible
in ``systemctl --failed`` and a hook for future ``OnFailure=`` alerting). Exit 3
(not 2) stays distinct from argparse usage errors.

Usage:
    uv run python -m scripts.audit_ancillary_orphans          # DATABASE_URL
    uv run python -m scripts.audit_ancillary_orphans --test   # TEST_DATABASE_URL
"""

import asyncio
import sys

import asyncpg

from scripts._dsn import add_dsn_args, build_parser, resolve_dsn
from scripts.cleanup_role_assignment_ancillary_orphans import ORPHAN_TABLES as CLEANUP_TABLES
from src.core.ancillary_migrate import count_orphaned_citations, count_orphaned_polymorphic_rows
from src.core.logging import configure_logging, get_logger

logger = get_logger(__name__)

#: Keys the cleanup script can recover; every other orphan is manual triage.
_CLEANABLE = frozenset(f"role_assignment.{t}" for t in CLEANUP_TABLES)


async def audit(conn: asyncpg.Connection) -> int:
    """Count every orphan scope, log the result, and return the exit code (0 or 3)."""
    citation_counts = await count_orphaned_citations(conn)
    counts = {
        **await count_orphaned_polymorphic_rows(conn),
        **{f"citation.{t}": n for t, n in citation_counts.items()},
    }
    total = sum(counts.values())
    if total == 0:
        logger.info("polymorphic ancillary orphan audit: clean (0 orphans)")
        return 0

    found = [key for key, n in counts.items() if n]
    cleanable = [key for key in found if key in _CLEANABLE]
    manual = [key for key in found if key not in _CLEANABLE]
    hints = []
    if cleanable:
        hints.append(
            f"run scripts.cleanup_role_assignment_ancillary_orphans for {', '.join(cleanable)}"
        )
    if manual:
        hints.append(f"triage {', '.join(manual)} manually (docs/AUDITS.md)")
    logger.warning(
        "ancillary orphans detected: %d total (%s) — %s",
        total,
        ", ".join(f"{key}={counts[key]}" for key in found),
        "; ".join(hints),
    )
    return 3


async def _run(database_url: str) -> int:
    conn = await asyncpg.connect(database_url)
    try:
        return await audit(conn)
    finally:
        await conn.close()


def main() -> None:
    parser = build_parser(__doc__)
    add_dsn_args(parser)
    args = parser.parse_args()
    dsn = resolve_dsn(args, parser)

    configure_logging()
    sys.exit(asyncio.run(_run(dsn)))


if __name__ == "__main__":
    main()
