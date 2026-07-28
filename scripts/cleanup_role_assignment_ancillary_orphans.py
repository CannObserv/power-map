"""Re-home / purge the polymorphic ancillary orphaned by pre-#324 merge dedup.

Before #324 the merge paths hard-deleted duplicate ``role_assignments`` without
re-homing their ``links`` / ``contact_methods`` / ``field_confidence`` /
``identifiers`` (keyed on ``(entity_type='role_assignment', entity_id)`` with no
FK). Those rows now dangle off assignment ids that no longer exist. The merge
paths are fixed going forward; this one-off recovers the existing strays.

The owning assignment is gone and merges leave no assignment-level tombstone, so
recovery is **heuristic** and works per *dead assignment id* (every orphan on one
dead id belonged to the same person's seat, so they all re-home to one survivor):

* **PDC filer** — a ``role_wa_pdc`` identifier whose ``filer_id`` resolves through
  a ``person_wa_pdc_filer`` identifier to a person who still holds a current WA
  legislative seat → re-home the whole group onto that seat.
* **Official email** — a ``first.last@…`` contact whose ``First Last`` matches
  *exactly one* person who still holds a current WA legislative seat → re-home.
* **Redundant link** — a leftover link whose identical ``(url, link_type)`` still
  lives on a non-orphaned entity → purge (no information lost).
* Everything else → **manual**: reported, never touched.

Re-home reuses ``migrate_role_assignment_ancillary`` (re-point, or dedup when the
survivor already carries the row) and emits the survivor outbox signal.

Dry-run by default (report only). ``--execute`` applies re-home + purge and leaves
every ``manual`` row untouched.

Usage:
    uv run python -m scripts.cleanup_role_assignment_ancillary_orphans
    uv run python -m scripts.cleanup_role_assignment_ancillary_orphans --execute
"""

import argparse
import asyncio
import os
import sys
from collections import defaultdict
from typing import Literal, NamedTuple

import asyncpg

from scripts.archive_legacy_legislator_roles import filer_id_from_url
from src.core.ancillary_migrate import (
    TRIGGERLESS_ANCILLARY_TABLES,
    migrate_role_assignment_ancillary,
)
from src.core.logging import configure_logging, get_logger

logger = get_logger(__name__)

Disposition = Literal["rehome", "purge", "manual"]


class OrphanRow(NamedTuple):
    """One orphaned ancillary row keyed on a dead role_assignment id."""

    table: str
    row_id: str
    dead_id: str  # the role_assignment id that no longer exists
    label: str  # human-readable value for the report (url / email / filer / field)


class DeadGroup(NamedTuple):
    """All orphan rows sharing one dead assignment id, plus its resolution."""

    dead_id: str
    rows: list[OrphanRow]
    target_id: str | None  # survivor assignment to re-home onto, when resolved
    method: str | None  # which heuristic resolved the target


# ── Orphan discovery ─────────────────────────────────────────────────────────

_ORPHAN_QUERIES: tuple[tuple[str, str], ...] = (
    (
        "links",
        "SELECT id, entity_id AS dead_id, url AS label FROM links x"
        " WHERE x.entity_type='role_assignment'"
        " AND NOT EXISTS (SELECT 1 FROM role_assignments ra WHERE ra.id = x.entity_id)",
    ),
    (
        "contact_methods",
        "SELECT id, entity_id AS dead_id, value AS label FROM contact_methods x"
        " WHERE x.entity_type='role_assignment'"
        " AND NOT EXISTS (SELECT 1 FROM role_assignments ra WHERE ra.id = x.entity_id)",
    ),
    (
        "field_confidence",
        "SELECT id, entity_id AS dead_id, field_name AS label FROM field_confidence x"
        " WHERE x.entity_type='role_assignment'"
        " AND NOT EXISTS (SELECT 1 FROM role_assignments ra WHERE ra.id = x.entity_id)",
    ),
    (
        "identifiers",
        "SELECT i.id, i.entity_id AS dead_id, i.value AS label FROM identifiers i"
        " JOIN entity_identifier_types t ON t.id = i.entity_identifier_type_id"
        " WHERE t.entity_type='role_assignment'"
        " AND NOT EXISTS (SELECT 1 FROM role_assignments ra WHERE ra.id = i.entity_id)",
    ),
    (
        "import_provenance",
        "SELECT id, entity_id AS dead_id, action AS label FROM import_provenance x"
        " WHERE x.entity_type='role_assignment'"
        " AND NOT EXISTS (SELECT 1 FROM role_assignments ra WHERE ra.id = x.entity_id)",
    ),
)


async def _fetch_orphans(conn: asyncpg.Connection) -> list[OrphanRow]:
    orphans: list[OrphanRow] = []
    for table, sql in _ORPHAN_QUERIES:
        for row in await conn.fetch(sql):
            orphans.append(OrphanRow(table, row["id"], row["dead_id"], row["label"]))
    return orphans


# ── Heuristic target resolution ──────────────────────────────────────────────

# A person's current WA legislative seat: an active typed assignment on a chamber
# org (identified by the org_wa_legislature_chamber identifier), most current first.
_CURRENT_SEAT_SQL = """
SELECT ra.id
FROM role_assignments ra
JOIN roles r ON r.id = ra.role_id
WHERE ra.person_id = $1
  AND r.role_type_id IS NOT NULL
  AND ra.archived_at IS NULL AND r.archived_at IS NULL
  AND r.organization_id IN (
      SELECT i.entity_id FROM identifiers i
      JOIN entity_identifier_types t ON t.id = i.entity_identifier_type_id
      WHERE t.slug = 'org_wa_legislature_chamber'
  )
ORDER BY ra.is_current DESC, ra.start_date DESC NULLS LAST, ra.id
LIMIT 1
"""

_FILER_TO_PERSON_SQL = """
SELECT i.entity_id FROM identifiers i
JOIN entity_identifier_types t ON t.id = i.entity_identifier_type_id
WHERE t.slug = 'person_wa_pdc_filer' AND i.value = $1
"""

_PERSON_BY_DISPLAY_NAME_SQL = """
SELECT person_id FROM v_person_display_names WHERE lower(display_name) = lower($1)
"""


async def _current_seat(conn: asyncpg.Connection, person_id: str) -> str | None:
    return await conn.fetchval(_CURRENT_SEAT_SQL, person_id)


async def _resolve_via_pdc_filer(conn: asyncpg.Connection, label: str) -> str | None:
    """A role_wa_pdc identifier value → filer_id → person → their current seat."""
    filer_id = filer_id_from_url(label)
    if filer_id is None:
        return None
    person_id = await conn.fetchval(_FILER_TO_PERSON_SQL, filer_id)
    if person_id is None:
        return None
    return await _current_seat(conn, person_id)


def _name_from_email(email: str) -> str | None:
    """`first.last@host` → `first last`; None when the local part isn't dotted.

    Casing is irrelevant — the resolver matches case-insensitively against
    `v_person_display_names` — so no cosmetic capitalization is applied.
    """
    local = email.split("@", 1)[0]
    if "." not in local:
        return None
    parts = [p for p in local.split(".") if p]
    if len(parts) < 2:
        return None
    return " ".join(parts)


async def _resolve_via_email(conn: asyncpg.Connection, email: str) -> str | None:
    """`first.last@…` → exactly one person by display name → their current seat."""
    name = _name_from_email(email)
    if name is None:
        return None
    people = await conn.fetch(_PERSON_BY_DISPLAY_NAME_SQL, name)
    if len(people) != 1:  # ambiguous or no match → unsafe to auto-rehome
        return None
    return await _current_seat(conn, people[0]["person_id"])


async def _resolve_target(
    conn: asyncpg.Connection, rows: list[OrphanRow]
) -> tuple[str | None, str | None]:
    """Best (target_assignment_id, method) for a dead group, or (None, None)."""
    for r in rows:
        if r.table == "identifiers":
            target = await _resolve_via_pdc_filer(conn, r.label)
            if target:
                return target, "pdc_filer"
    for r in rows:
        if r.table == "contact_methods":
            target = await _resolve_via_email(conn, r.label)
            if target:
                return target, "email_name"
    return None, None


# ── Purge (redundant link) ───────────────────────────────────────────────────

_LINK_LIVE_DUP_SQL = """
SELECT 1 FROM links dup
WHERE dup.url = (SELECT url FROM links WHERE id = $1)
  AND dup.link_type_id = (SELECT link_type_id FROM links WHERE id = $1)
  AND dup.id <> $1
  AND NOT (dup.entity_type = 'role_assignment'
           AND NOT EXISTS (SELECT 1 FROM role_assignments ra WHERE ra.id = dup.entity_id))
LIMIT 1
"""


async def _link_is_redundant(conn: asyncpg.Connection, row_id: str) -> bool:
    """True when an identical (url, link_type) still lives on a non-orphaned entity."""
    return bool(await conn.fetchval(_LINK_LIVE_DUP_SQL, row_id))


# ── Planning ─────────────────────────────────────────────────────────────────


async def plan_cleanup(conn: asyncpg.Connection) -> list[DeadGroup]:
    """Group orphans by dead assignment id and resolve a re-home target for each."""
    by_dead: dict[str, list[OrphanRow]] = defaultdict(list)
    for orphan in await _fetch_orphans(conn):
        by_dead[orphan.dead_id].append(orphan)

    groups: list[DeadGroup] = []
    for dead_id, rows in sorted(by_dead.items()):
        target, method = await _resolve_target(conn, rows)
        groups.append(DeadGroup(dead_id, rows, target, method))
    return groups


async def _classify_unresolved(conn: asyncpg.Connection, row: OrphanRow) -> tuple[Disposition, str]:
    """Disposition for an orphan whose dead group found no re-home target."""
    if row.table == "links" and await _link_is_redundant(conn, row.row_id):
        return "purge", "identical link on a live entity"
    return "manual", "no heuristic match"


# ── Apply ────────────────────────────────────────────────────────────────────


async def apply_cleanup(conn: asyncpg.Connection, groups: list[DeadGroup]) -> dict[str, int]:
    """Execute the plan: re-home resolved groups, purge redundant links.

    Returns ``{'rehomed_rows', 'purged', 'manual'}`` counts. Manual rows are never
    mutated. Caller owns the transaction.
    """
    stats = {"rehomed_rows": 0, "purged": 0, "manual": 0}
    for group in groups:
        if group.target_id is not None:
            counts = await migrate_role_assignment_ancillary(conn, group.dead_id, group.target_id)
            moved = sum(m for m, _ in counts.values())
            deduped = sum(d for _, d in counts.values())
            stats["rehomed_rows"] += moved + deduped
            # links/contact_methods/identifiers self-emit the survivor 'updated'
            # via their touch triggers (#327); only a trigger-less move
            # (field_confidence/import_provenance) needs a manual signal here.
            if any(counts[t][0] for t in TRIGGERLESS_ANCILLARY_TABLES):
                await conn.execute(
                    "INSERT INTO entity_changes (entity_type, entity_id, change_kind)"
                    " VALUES ('role_assignment', $1, 'updated')",
                    group.target_id,
                )
            continue
        for row in group.rows:
            disposition, _ = await _classify_unresolved(conn, row)
            if disposition == "purge":
                await conn.execute(f"DELETE FROM {row.table} WHERE id=$1", row.row_id)
                stats["purged"] += 1
            else:
                stats["manual"] += 1
    return stats


# ── Reporting ────────────────────────────────────────────────────────────────


async def _report(conn: asyncpg.Connection, groups: list[DeadGroup]) -> dict[str, int]:
    tally = {"rehome_groups": 0, "rehome_rows": 0, "purge": 0, "manual": 0}
    for group in groups:
        if group.target_id is not None:
            tally["rehome_groups"] += 1
            tally["rehome_rows"] += len(group.rows)
            logger.info(
                "REHOME dead=%s -> %s via %s : %s",
                group.dead_id,
                group.target_id,
                group.method,
                ", ".join(f"{r.table}:{r.label}" for r in group.rows),
            )
            continue
        for row in group.rows:
            disposition, why = await _classify_unresolved(conn, row)
            tally[disposition] += 1
            logger.info(
                "%s dead=%s %s:%s (%s)",
                disposition.upper(),
                group.dead_id,
                row.table,
                row.label,
                why,
            )
    return tally


async def _run(database_url: str, *, execute: bool) -> int:
    conn = await asyncpg.connect(database_url)
    try:
        groups = await plan_cleanup(conn)
        tally = await _report(conn, groups)
        logger.info(
            "plan: %d groups — rehome %d rows in %d groups, purge %d, manual %d",
            len(groups),
            tally["rehome_rows"],
            tally["rehome_groups"],
            tally["purge"],
            tally["manual"],
        )
        if not execute:
            logger.info("dry run — no changes written (pass --execute to apply)")
            return 0
        async with conn.transaction():
            stats = await apply_cleanup(conn, groups)
        logger.info(
            "executed: rehomed %d rows, purged %d, left %d for manual triage",
            stats["rehomed_rows"],
            stats["purged"],
            stats["manual"],
        )
    finally:
        await conn.close()
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database-url",
        default=os.environ.get("DATABASE_URL"),
        help="DSN to operate on (default: DATABASE_URL).",
    )
    parser.add_argument("--execute", action="store_true", help="Apply changes (default: dry run).")
    args = parser.parse_args()
    if not args.database_url:
        parser.error("no database URL: pass --database-url or set DATABASE_URL")

    configure_logging()
    sys.exit(asyncio.run(_run(args.database_url, execute=args.execute)))


if __name__ == "__main__":
    main()
