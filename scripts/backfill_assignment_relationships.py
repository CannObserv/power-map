"""Backfill the 3 staffer->principal relationship edges descoped from #266 (#301).

The staffer/principal link currently survives only as free text inside the staff
role title (e.g. "Legislative Aide, Senator June Robinson"). This one-off resolves
each of the three known rows to an edge:

  staffer's active assignment  --staff_of-->  principal's seat assignment

**Heuristic, supervised.** Resolution is deterministic but fuzzy (principal matched
by display name, seat by role type + window overlap). Any staffer without exactly
one assignment, any principal name that resolves to 0 or >1 people, or any principal
without exactly one overlapping seat assignment is REPORTED, never guessed — the
dry-run surfaces every miss so a human decides. ``notes`` on the staff role/assignment
are left untouched; the operator cleans the free-text principal reference afterwards
from the emitted list.

Usage:
    uv run python -m scripts.backfill_assignment_relationships            # dry-run
    uv run python -m scripts.backfill_assignment_relationships --execute  # mint edges
"""

import argparse
import asyncio
import datetime
from dataclasses import dataclass

import asyncpg

from scripts._dsn import add_dsn_args, resolve_dsn
from src.core.assignment_relationships import (
    RelationshipClaim,
    RelationshipDisposition,
    apply_relationship_observations,
)
from src.core.logging import configure_logging, get_logger

logger = get_logger(__name__)

SEAT_ROLE_TYPES = ("state_senator", "state_representative")


@dataclass(frozen=True)
class Target:
    """One staff role → principal mapping (the principal parsed from #266's titles)."""

    staffer_role_id: str
    principal_name: str


# The three concrete rows from #301 (descoped from #266's staff dimension).
TARGETS: tuple[Target, ...] = (
    Target("01KV6PR793RH66X1AF3TR8DV70", "June Robinson"),  # staffer Kate Armstrong
    Target("01KV6PR6SSMT8FGNJFCGFRMA9P", "Shelley Kloba"),  # staffer Joren Clowers
    Target("01KV6PR3S4T3GGQSEJ4W40NB15", "Saldaña"),  # staffer Coco Chang
)


@dataclass
class Resolution:
    """The outcome of resolving one target — either a mintable pair or a miss."""

    target: Target
    staffer_assignment_id: str | None = None
    principal_assignment_id: str | None = None
    valid_from: datetime.date | None = None
    valid_until: datetime.date | None = None
    problem: str | None = None


async def _resolve_one(conn: asyncpg.Connection, target: Target) -> Resolution:
    res = Resolution(target=target)

    staffers = await conn.fetch(
        "SELECT id, start_date, end_date FROM role_assignments"
        " WHERE role_id=$1 AND archived_at IS NULL",
        target.staffer_role_id,
    )
    if len(staffers) != 1:
        res.problem = f"staffer role has {len(staffers)} active assignments (need 1)"
        return res
    staffer = staffers[0]
    res.staffer_assignment_id = staffer["id"]

    people = await conn.fetch(
        "SELECT person_id FROM v_person_display_names WHERE display_name ILIKE $1",
        f"%{target.principal_name}%",
    )
    if len(people) != 1:
        res.problem = f"principal {target.principal_name!r} matched {len(people)} people (need 1)"
        return res
    principal_person = people[0]["person_id"]

    # Seat assignment(s) of the principal, overlapping the staffer's window.
    seats = await conn.fetch(
        """SELECT ra.id, ra.start_date, ra.end_date
           FROM role_assignments ra
           JOIN roles r ON r.id = ra.role_id
           JOIN role_types rt ON rt.id = r.role_type_id
           WHERE ra.person_id = $1 AND ra.archived_at IS NULL
             AND rt.slug = ANY($2::text[])
             AND (ra.end_date   IS NULL OR $3::date IS NULL OR ra.end_date   >= $3)
             AND (ra.start_date IS NULL OR $4::date IS NULL OR ra.start_date <= $4)""",
        principal_person,
        list(SEAT_ROLE_TYPES),
        staffer["start_date"],
        staffer["end_date"],
    )
    if len(seats) != 1:
        res.problem = f"principal has {len(seats)} overlapping seat assignments (need 1)"
        return res
    seat = seats[0]
    res.principal_assignment_id = seat["id"]

    # Window = intersection; unknown (NULL) start stays unknown (#307).
    starts = [d for d in (staffer["start_date"], seat["start_date"]) if d is not None]
    ends = [d for d in (staffer["end_date"], seat["end_date"]) if d is not None]
    res.valid_from = max(starts) if starts else None
    res.valid_until = min(ends) if ends else None
    return res


async def resolve(conn: asyncpg.Connection, targets=TARGETS) -> list[Resolution]:
    """Resolve every target (report-only); never mutates."""
    return [await _resolve_one(conn, t) for t in targets]


async def run_backfill(
    conn: asyncpg.Connection, *, execute: bool, targets=TARGETS
) -> list[Resolution]:
    """Resolve, and with ``execute`` mint the edges for cleanly-resolved targets."""
    resolutions = await resolve(conn, targets)
    if execute:
        claims = [
            RelationshipClaim(
                from_pm_assignment_id=r.staffer_assignment_id,
                to_pm_assignment_id=r.principal_assignment_id,
                valid_from=r.valid_from,
                valid_until=r.valid_until,
            )
            for r in resolutions
            if r.problem is None
        ]
        if claims:
            results = await apply_relationship_observations(conn, None, claims)
            for claim, result in zip(claims, results, strict=True):
                logger.info(
                    "mint %s->%s: %s",
                    claim.from_pm_assignment_id,
                    claim.to_pm_assignment_id,
                    result.disposition.value + (f" ({result.reason})" if result.reason else ""),
                )
                if result.disposition is RelationshipDisposition.REJECTED:
                    logger.warning("edge rejected: %s", result.reason)
    return resolutions


def _log_report(resolutions: list[Resolution], *, execute: bool) -> None:
    ok = [r for r in resolutions if r.problem is None]
    misses = [r for r in resolutions if r.problem is not None]
    for r in ok:
        logger.info(
            "%s role %s (principal %r): %s -> %s  window [%s, %s]",
            "MINTED" if execute else "READY",
            r.target.staffer_role_id,
            r.target.principal_name,
            r.staffer_assignment_id,
            r.principal_assignment_id,
            r.valid_from,
            r.valid_until,
        )
    for r in misses:
        logger.warning(
            "SKIP role %s (principal %r): %s",
            r.target.staffer_role_id,
            r.target.principal_name,
            r.problem,
        )
    logger.info(
        "%d resolved, %d unresolved%s. Operator: clear the free-text principal from "
        "the %d resolved staff role/assignment title(s) once verified.",
        len(ok),
        len(misses),
        "" if execute else " (dry-run — pass --execute to mint)",
        len(ok),
    )


async def run(dsn: str, *, execute: bool) -> None:
    conn = await asyncpg.connect(dsn)
    try:
        if execute:
            async with conn.transaction():
                resolutions = await run_backfill(conn, execute=True)
        else:
            resolutions = await run_backfill(conn, execute=False)
        _log_report(resolutions, execute=execute)
    finally:
        await conn.close()


def main() -> None:
    configure_logging()
    parser = argparse.ArgumentParser(description=__doc__)
    add_dsn_args(parser)
    parser.add_argument("--execute", action="store_true", help="Mint edges (default is dry-run)")
    args = parser.parse_args()
    dsn = resolve_dsn(args, parser)
    asyncio.run(run(dsn, execute=args.execute))


if __name__ == "__main__":
    main()
