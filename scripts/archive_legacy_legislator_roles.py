"""Validate + archive legacy free-text WA legislator roles onto seat-Roles (#265).

Legacy legislator roles predate the #261 seat model: free-text titles
(``"Senator, District 19"``, ``"Representative"``), no ``role_type_id`` /
``jurisdiction_id`` / ``qualifier``, undated assignments. The USA-WA enrichment
now writes full-fidelity seat-assignments, making the legacy rows redundant —
*except* for ancillary data whose only copy hangs off the legacy assignment.

Per legacy assignment on the House / Senate / Legislature orgs:

1. **Parse** the title — staff/leadership rows (anything that isn't a bare
   ``Senator``/``Representative`` seat title) are excluded (→ #266).
2. **Match seat-level**: the same person must hold a typed seat-assignment on
   the matching chamber (and, when the title carries a district, on a seat
   whose jurisdiction is that LD). No match → kept, reported ``unmatched``.
3. **Migrate ancillary data** onto the matched typed assignment:
   links and contact_methods are re-pointed (deleted when the target already
   carries an identical row); field_confidence rows follow. ``role_wa_pdc``
   campaign-explorer URLs are rescued to the *person*: the alpha filer ID is
   parsed into a ``person_wa_pdc_filer`` identifier and the source URL is
   preserved as a ``wa_pdc`` link (decision in #265).
4. **Archive** the legacy assignment; a legacy role is archived once it has no
   active assignments left.

Coverage-gated and idempotent: unmatched/excluded rows are left untouched and
re-evaluated on the next run (e.g. after upstream backfills more history).

Usage:
    uv run python -m scripts.archive_legacy_legislator_roles            # dry run
    uv run python -m scripts.archive_legacy_legislator_roles --execute  # commit
"""

import argparse
import asyncio
import os
import re
from collections import Counter
from typing import Literal, NamedTuple, TypedDict
from urllib.parse import parse_qs, urlsplit

import asyncpg

from src.core.db import generate_id
from src.core.logging import configure_logging, get_logger

logger = get_logger(__name__)

Chamber = Literal["house", "senate"]


class SeatTitle(NamedTuple):
    """Chamber + optional district parsed from a legacy seat title."""

    chamber: Chamber
    district: int | None


# Anchored patterns for every seat-title convention observed in the #265 audit.
# Anything that doesn't match is staff/leadership → excluded.
_SEAT_TITLE_PATTERNS: tuple[tuple[re.Pattern[str], Chamber], ...] = (
    (re.compile(r"^Senator(?:, District (\d+))?$"), "senate"),
    (re.compile(r"^Representative(?:, District (\d+))?$"), "house"),
    (re.compile(r"^District (\d+) Representative$"), "house"),
    (re.compile(r"^(\d+)(?:st|nd|rd|th) District State Representative$"), "house"),
)


def parse_legacy_title(title: str) -> SeatTitle | None:
    """Parse a legacy role title into ``SeatTitle``, or None for staff/leadership."""
    stripped = title.strip()
    for pattern, chamber in _SEAT_TITLE_PATTERNS:
        m = pattern.match(stripped)
        if m:
            district = m.group(1)
            return SeatTitle(chamber, int(district) if district else None)
    return None


def filer_id_from_url(value: str) -> str | None:
    """Return the decoded ``filer_id`` from a PDC URL, or None if not parseable.

    Host-allowlisted to ``pdc.wa.gov`` (+ subdomains) so a stray non-PDC URL
    surfaces as ``conflict`` instead of minting a wrong filer identifier.
    """
    parts = urlsplit(value.strip())
    if parts.scheme not in ("http", "https"):
        return None
    if parts.netloc != "pdc.wa.gov" and not parts.netloc.endswith(".pdc.wa.gov"):
        return None
    filers = parse_qs(parts.query).get("filer_id", [])
    return filers[0] if filers else None


ActionStatus = Literal["archived", "planned", "unmatched", "excluded", "conflict"]


class AssignmentAction(TypedDict):
    """Outcome for one active legacy assignment."""

    assignment_id: str
    role_id: str
    person_id: str
    title: str
    status: ActionStatus
    target_assignment_id: str | None
    migrated: dict[str, int]  # links / contacts / fc / pdc moved or deduped


class Report(TypedDict):
    """Full run outcome: one action per legacy assignment + roles archived."""

    actions: list[AssignmentAction]
    archived_roles: list[str]


class _Context(NamedTuple):
    """Resolved IDs the queries key on."""

    org_ids: list[str]  # house + senate + legislature
    org_by_chamber: dict[Chamber, str]
    role_type_by_chamber: dict[Chamber, str]
    role_wa_pdc_type_id: str
    person_filer_type_id: str
    wa_pdc_link_type_id: str


_CHAMBER_ORG_SQL = """
SELECT i.value, i.entity_id
FROM identifiers i
JOIN entity_identifier_types t ON t.id = i.entity_identifier_type_id
WHERE t.slug = 'org_wa_legislature_chamber' AND i.value = ANY($1)
"""

_LEGISLATURE_ORG_SQL = """
SELECT i.entity_id
FROM identifiers i
JOIN entity_identifier_types t ON t.id = i.entity_identifier_type_id
WHERE t.slug = 'org_wa_legislature' AND i.value = 'usa_wa_legislature'
"""

_LEGACY_ASSIGNMENTS_SQL = """
SELECT ra.id AS assignment_id, ra.person_id, r.id AS role_id, r.title
FROM role_assignments ra
JOIN roles r ON r.id = ra.role_id
WHERE r.organization_id = ANY($1) AND r.role_type_id IS NULL
  AND ra.archived_at IS NULL AND r.archived_at IS NULL
ORDER BY r.title, ra.id
"""

_LEGACY_ROLES_SQL = """
SELECT r.id, r.title
FROM roles r
WHERE r.organization_id = ANY($1) AND r.role_type_id IS NULL AND r.archived_at IS NULL
ORDER BY r.title
"""

_MATCH_SEAT_SQL = """
SELECT ra.id
FROM role_assignments ra
JOIN roles r ON r.id = ra.role_id
JOIN jurisdictions j ON j.id = r.jurisdiction_id
WHERE ra.person_id = $1 AND r.role_type_id = $2 AND j.slug = $3
  AND r.organization_id = $4
  AND ra.archived_at IS NULL AND r.archived_at IS NULL
ORDER BY ra.is_current DESC, ra.start_date DESC NULLS LAST, ra.id
LIMIT 1
"""

_MATCH_CHAMBER_SQL = """
SELECT ra.id
FROM role_assignments ra
JOIN roles r ON r.id = ra.role_id
WHERE ra.person_id = $1 AND r.role_type_id = $2
  AND r.organization_id = $3
  AND ra.archived_at IS NULL AND r.archived_at IS NULL
ORDER BY ra.is_current DESC, ra.start_date DESC NULLS LAST, ra.id
LIMIT 1
"""

_PDC_ROWS_SQL = """
SELECT id, value FROM identifiers
WHERE entity_identifier_type_id = $1 AND entity_id = $2
ORDER BY created_at
"""

_PERSON_FILER_VALUES_SQL = """
SELECT value FROM identifiers
WHERE entity_identifier_type_id = $1 AND entity_id = $2
"""

_INSERT_IDENTIFIER_SQL = """
INSERT INTO identifiers (id, entity_id, entity_identifier_type_id, value)
VALUES ($1, $2, $3, $4)
"""

_INSERT_PERSON_LINK_SQL = """
INSERT INTO links (id, entity_type, entity_id, url, link_type_id)
VALUES ($1, 'person', $2, $3, $4)
ON CONFLICT (entity_type, entity_id, url, link_type_id) DO NOTHING
"""

_DELETE_IDENTIFIER_SQL = "DELETE FROM identifiers WHERE id = $1"

_LINKS_SQL = """
SELECT id, url, link_type_id FROM links
WHERE entity_type = 'role_assignment' AND entity_id = $1
"""

_LINK_ON_TARGET_SQL = """
SELECT 1 FROM links
WHERE entity_type = 'role_assignment' AND entity_id = $1
  AND url = $2 AND link_type_id = $3
"""

_REPOINT_LINK_SQL = "UPDATE links SET entity_id = $2 WHERE id = $1"
_DELETE_LINK_SQL = "DELETE FROM links WHERE id = $1"

_CONTACTS_SQL = """
SELECT id, contact_type, value FROM contact_methods
WHERE entity_type = 'role_assignment' AND entity_id = $1
"""

_CONTACT_ON_TARGET_SQL = """
SELECT 1 FROM contact_methods
WHERE entity_type = 'role_assignment' AND entity_id = $1
  AND contact_type = $2 AND value = $3
"""

_REPOINT_CONTACT_SQL = "UPDATE contact_methods SET entity_id = $2 WHERE id = $1"
_DELETE_CONTACT_SQL = "DELETE FROM contact_methods WHERE id = $1"

_FC_SQL = """
SELECT id, field_name, value_hash FROM field_confidence
WHERE entity_type = 'role_assignment' AND entity_id = $1
"""

_FC_ON_TARGET_SQL = """
SELECT 1 FROM field_confidence
WHERE entity_type = 'role_assignment' AND entity_id = $1
  AND field_name = $2 AND value_hash = $3
"""

_REPOINT_FC_SQL = "UPDATE field_confidence SET entity_id = $2 WHERE id = $1"
_DELETE_FC_SQL = "DELETE FROM field_confidence WHERE id = $1"

_RECORD_CHANGE_SQL = """
INSERT INTO entity_changes (entity_type, entity_id, change_kind)
VALUES ($1, $2, 'updated')
"""

_ARCHIVE_ASSIGNMENT_SQL = "UPDATE role_assignments SET archived_at = NOW() WHERE id = $1"
_ARCHIVE_ROLE_SQL = "UPDATE roles SET archived_at = NOW() WHERE id = $1"

_ACTIVE_ASSIGNMENTS_ON_ROLE_SQL = """
SELECT COUNT(*) FROM role_assignments WHERE role_id = $1 AND archived_at IS NULL
"""


async def _resolve_context(conn: asyncpg.Connection) -> _Context:
    """Resolve chamber orgs, role types, and identifier/link types; hard-fail if absent."""
    chamber_rows = await conn.fetch(_CHAMBER_ORG_SQL, ["usa_wa_house", "usa_wa_senate"])
    orgs_by_value: dict[str, list[str]] = {}
    for row in chamber_rows:
        orgs_by_value.setdefault(row["value"], []).append(row["entity_id"])
    legislature_rows = [r["entity_id"] for r in await conn.fetch(_LEGISLATURE_ORG_SQL)]
    for value, ids in {**orgs_by_value, "org_wa_legislature": legislature_rows}.items():
        if len(ids) != 1:
            raise RuntimeError(f"expected exactly one org for {value}, found {len(ids)}")

    role_types = {
        row["slug"]: row["id"]
        for row in await conn.fetch(
            "SELECT slug, id FROM role_types"
            " WHERE slug IN ('state_representative', 'state_senator')"
        )
    }
    identifier_types = {
        row["slug"]: row["id"]
        for row in await conn.fetch(
            "SELECT slug, id FROM entity_identifier_types"
            " WHERE slug IN ('role_wa_pdc', 'person_wa_pdc_filer')"
        )
    }
    wa_pdc_link_type_id = await conn.fetchval("SELECT id FROM link_types WHERE slug = 'wa_pdc'")
    missing = (
        ({"state_representative", "state_senator"} - role_types.keys())
        | ({"role_wa_pdc", "person_wa_pdc_filer"} - identifier_types.keys())
        | ({"wa_pdc"} if wa_pdc_link_type_id is None else set())
    )
    if missing:
        raise RuntimeError(f"missing catalog rows {missing} — run apply_schema first")

    return _Context(
        org_ids=[
            orgs_by_value["usa_wa_house"][0],
            orgs_by_value["usa_wa_senate"][0],
            legislature_rows[0],
        ],
        org_by_chamber={
            "house": orgs_by_value["usa_wa_house"][0],
            "senate": orgs_by_value["usa_wa_senate"][0],
        },
        role_type_by_chamber={
            "house": role_types["state_representative"],
            "senate": role_types["state_senator"],
        },
        role_wa_pdc_type_id=identifier_types["role_wa_pdc"],
        person_filer_type_id=identifier_types["person_wa_pdc_filer"],
        wa_pdc_link_type_id=wa_pdc_link_type_id,
    )


async def _find_target(
    conn: asyncpg.Connection, ctx: _Context, person_id: str, seat: SeatTitle
) -> str | None:
    """Best typed seat-assignment covering this person, seat-level when district known.

    Both matches are restricted to the WA chamber org — the role_types catalog
    is generic, so a same-typed seat on another org must never validate a row.
    """
    role_type_id = ctx.role_type_by_chamber[seat.chamber]
    org_id = ctx.org_by_chamber[seat.chamber]
    if seat.district is not None:
        return await conn.fetchval(
            _MATCH_SEAT_SQL, person_id, role_type_id, f"usa-wa-ld-{seat.district}", org_id
        )
    return await conn.fetchval(_MATCH_CHAMBER_SQL, person_id, role_type_id, org_id)


async def _rescue_pdc(
    conn: asyncpg.Connection,
    ctx: _Context,
    person_id: str,
    pdc_rows: list[asyncpg.Record],
) -> int:
    """Move role_wa_pdc URLs to the person: filer identifier + wa_pdc link."""
    existing = {
        row["value"]
        for row in await conn.fetch(_PERSON_FILER_VALUES_SQL, ctx.person_filer_type_id, person_id)
    }
    rescued = 0
    for row in pdc_rows:
        filer = filer_id_from_url(row["value"])
        if filer not in existing:
            await conn.execute(
                _INSERT_IDENTIFIER_SQL, generate_id(), person_id, ctx.person_filer_type_id, filer
            )
            existing.add(filer)
        await conn.execute(
            _INSERT_PERSON_LINK_SQL,
            generate_id(),
            person_id,
            row["value"],
            ctx.wa_pdc_link_type_id,
        )
        await conn.execute(_DELETE_IDENTIFIER_SQL, row["id"])
        rescued += 1
    return rescued


async def _migrate_rows(
    conn: asyncpg.Connection,
    target: str,
    rows_sql: str,
    on_target_sql: str,
    repoint_sql: str,
    delete_sql: str,
    source: str,
    key_fields: tuple[str, str],
    *,
    execute: bool,
) -> tuple[int, int]:
    """Re-point ancillary rows from ``source`` to ``target``; delete exact duplicates.

    With ``execute=False`` only classifies (moved, deduped) — no mutation. Dry-run
    classification reads the target's *current* state, so when two planned
    assignments carry an identical row toward the same target, both count as
    moved (execute would move one and dedupe the other) — totals may overstate
    ``moved`` by the overlap; archive/keep decisions are unaffected.
    """
    moved = deduped = 0
    for row in await conn.fetch(rows_sql, source):
        exists = await conn.fetchval(on_target_sql, target, row[key_fields[0]], row[key_fields[1]])
        if exists:
            if execute:
                await conn.execute(delete_sql, row["id"])
            deduped += 1
        else:
            if execute:
                await conn.execute(repoint_sql, row["id"], target)
            moved += 1
    return moved, deduped


async def archive_legacy_legislator_roles(conn: asyncpg.Connection, *, execute: bool) -> Report:
    """Validate every active legacy legislator assignment; migrate + archive matches.

    Returns one ``AssignmentAction`` per active legacy assignment (``archived`` /
    ``planned`` / ``unmatched`` / ``excluded`` / ``conflict``) plus the legacy
    roles archived (or, in a dry run, that would be). Only ``archived`` mutates.
    """
    ctx = await _resolve_context(conn)

    actions: list[AssignmentAction] = []
    for row in await conn.fetch(_LEGACY_ASSIGNMENTS_SQL, ctx.org_ids):
        action: AssignmentAction = {
            "assignment_id": row["assignment_id"],
            "role_id": row["role_id"],
            "person_id": row["person_id"],
            "title": row["title"],
            "status": "excluded",
            "target_assignment_id": None,
            "migrated": {},
        }
        actions.append(action)

        seat = parse_legacy_title(row["title"])
        if seat is None:
            continue

        target = await _find_target(conn, ctx, row["person_id"], seat)
        if target is None:
            action["status"] = "unmatched"
            continue
        action["target_assignment_id"] = target

        # PDC parseability gates the whole assignment — all-or-nothing.
        pdc_rows = await conn.fetch(_PDC_ROWS_SQL, ctx.role_wa_pdc_type_id, row["assignment_id"])
        if any(filer_id_from_url(r["value"]) is None for r in pdc_rows):
            logger.warning(
                "%s (%s): unparseable role_wa_pdc value — skipping",
                row["title"],
                row["assignment_id"],
            )
            action["status"] = "conflict"
            continue

        migrated: dict[str, int] = {}
        if execute:
            migrated["pdc"] = await _rescue_pdc(conn, ctx, row["person_id"], pdc_rows)
        else:
            migrated["pdc"] = len(pdc_rows)
        for name, args in (
            (
                "links",
                (
                    _LINKS_SQL,
                    _LINK_ON_TARGET_SQL,
                    _REPOINT_LINK_SQL,
                    _DELETE_LINK_SQL,
                    ("url", "link_type_id"),
                ),
            ),
            (
                "contacts",
                (
                    _CONTACTS_SQL,
                    _CONTACT_ON_TARGET_SQL,
                    _REPOINT_CONTACT_SQL,
                    _DELETE_CONTACT_SQL,
                    ("contact_type", "value"),
                ),
            ),
            (
                "fc",
                (
                    _FC_SQL,
                    _FC_ON_TARGET_SQL,
                    _REPOINT_FC_SQL,
                    _DELETE_FC_SQL,
                    ("field_name", "value_hash"),
                ),
            ),
        ):
            rows_sql, on_target_sql, repoint_sql, delete_sql, keys = args
            moved, deduped = await _migrate_rows(
                conn,
                target,
                rows_sql,
                on_target_sql,
                repoint_sql,
                delete_sql,
                row["assignment_id"],
                keys,
                execute=execute,
            )
            migrated[name] = moved
            migrated[f"{name}_deduped"] = deduped
        action["migrated"] = {k: v for k, v in migrated.items() if v}

        if not execute:
            action["status"] = "planned"
            continue

        # Outbox events for the entities whose payloads changed (observation.py
        # convention): the target assignment when rows moved onto it, the person
        # when a PDC rescue touched them. The legacy assignment's own archive
        # UPDATE below is covered by trg_entity_changes_role_assignments.
        if migrated["links"] + migrated["contacts"] + migrated["fc"] > 0:
            await conn.execute(_RECORD_CHANGE_SQL, "role_assignment", target)
        if migrated["pdc"] > 0:
            await conn.execute(_RECORD_CHANGE_SQL, "person", row["person_id"])

        await conn.execute(_ARCHIVE_ASSIGNMENT_SQL, row["assignment_id"])
        action["status"] = "archived"
        logger.info("Archived %r assignment %s → %s", row["title"], row["assignment_id"], target)

    # Archive seat-shaped legacy roles with no active assignments left (real or planned).
    planned_away: dict[str, int] = Counter(
        a["role_id"] for a in actions if a["status"] == "planned"
    )
    archived_roles: list[str] = []
    for role in await conn.fetch(_LEGACY_ROLES_SQL, ctx.org_ids):
        if parse_legacy_title(role["title"]) is None:
            continue
        remaining = await conn.fetchval(_ACTIVE_ASSIGNMENTS_ON_ROLE_SQL, role["id"])
        if remaining - planned_away.get(role["id"], 0) > 0:
            continue
        if execute:
            await conn.execute(_ARCHIVE_ROLE_SQL, role["id"])
            logger.info("Archived legacy role %r (%s)", role["title"], role["id"])
        archived_roles.append(role["id"])

    return Report(actions=actions, archived_roles=archived_roles)


async def run(*, execute: bool) -> None:
    """Connect to DATABASE_URL and validate/archive the legacy legislator roles."""
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        raise RuntimeError("DATABASE_URL not set")

    conn = await asyncpg.connect(dsn)
    try:
        if execute:
            async with conn.transaction():
                report = await archive_legacy_legislator_roles(conn, execute=True)
        else:
            report = await archive_legacy_legislator_roles(conn, execute=False)

        counts = Counter(a["status"] for a in report["actions"])
        # conflict rows are already logged as warnings in the main loop
        for action in report["actions"]:
            if action["status"] in ("unmatched", "excluded"):
                logger.info(
                    "%s: %r assignment %s (person %s)",
                    action["status"],
                    action["title"],
                    action["assignment_id"],
                    action["person_id"],
                )
        breakdown = ", ".join(f"{status}={n}" for status, n in sorted(counts.items()))
        migrated_totals = Counter()
        for action in report["actions"]:
            migrated_totals.update(action["migrated"])
        totals = ", ".join(f"{k}={v}" for k, v in sorted(migrated_totals.items())) or "none"
        verb = "Archived" if execute else "Dry run — would archive"
        logger.info(
            "%s %d assignment(s) (%s), %d role(s); ancillary: %s",
            verb,
            counts["archived" if execute else "planned"],
            breakdown or "nothing to do",
            len(report["archived_roles"]),
            totals,
        )
        if not execute:
            logger.info("Pass --execute to commit")
    finally:
        await conn.close()


def main() -> None:
    configure_logging()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Commit changes (default is dry run)",
    )
    args = parser.parse_args()
    asyncio.run(run(execute=args.execute))


if __name__ == "__main__":
    main()
