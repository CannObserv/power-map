"""Split Laurie Jinkins's Speaker tenure into Designate + formal roles (#314).

Follow-up to #266's classification pass. The legacy ``Speaker of the House
(2021-23)`` row collapsed two distinct offices into one open, dateless tenure:

* **Speaker Designate** — elected by the House Democratic Caucus 2019-07-31
  (effectively "acting"), pending formal election by the full House.
* **Speaker of the House** — formally sworn in 2020-01-13; still current.

This mirrors the ``Acting Chair`` / ``Chair`` pattern on WA House COG (#266):
two distinct role rows on one org, both coarse-typed ``chamber_leader``, the
free-text title carrying the distinction and the type carrying the aggregation.

Dates come from the WA House Democrats caucus record (cited in notes), not
invented (#307): the designate tenure closes the day before swearing-in, the
formal tenure opens on swearing-in and stays ``is_current=TRUE`` (open end).
The stale ``Tenure 2021-23`` breadcrumb on the formal-Speaker role is cleared.

Provenance is captured as human-readable citations in ``notes`` for now;
structured citation links (a ``source`` link_type + assignment-links UI) are
tracked separately — see the #314 follow-up issue.

Idempotent: the designate role/assignment are keyed on natural identity and the
formal-assignment update is a no-op once applied. Safe to re-run.

Usage:
    uv run python -m scripts.split_speaker_designate            # dry run
    uv run python -m scripts.split_speaker_designate --execute  # commit
"""

import argparse
import asyncio
import datetime
import os
from typing import Literal, TypedDict

import asyncpg

from src.core.db import generate_id
from src.core.logging import configure_logging, get_logger
from src.core.org_lifecycle import check_assignment_lifespan

logger = get_logger(__name__)

# --- Prod identity (see #314) ----------------------------------------------
WA_HOUSE_ORG_ID = "01KV6PQGA3Y269YY60KN2XSZAY"
SPEAKER_ROLE_ID = "01KV6PR8EW3TAGEA03R1BS039X"
SPEAKER_ASSIGNMENT_ID = "01KV6PR8EW3TAGEA03R1BS039Y"
JINKINS_PERSON_ID = "01KV6PQT3H3T8VKCVNVACG46DF"

DESIGNATE_TITLE = "Speaker Designate"

# Tenure bounds. Designate closes the day before swearing-in (strict adjacency,
# matching the COG Chair handoff 2019-07-31 / 2019-08-01); formal opens on it.
DESIGNATE_START = datetime.date(2019, 7, 31)
DESIGNATE_END = datetime.date(2020, 1, 12)
SPEAKER_START = datetime.date(2020, 1, 13)

# Citations (WA House Democrats caucus record).
DESIGNATE_URL = (
    "https://housedemocrats.wa.gov/jinkins/2019/07/31/"
    "democrats-elect-rep-laurie-jinkins-to-serve-as-next-speaker-of-the-house/"
)
SPEAKER_URL = (
    "https://housedemocrats.wa.gov/jinkins/2020/01/13/"
    "jinkins-sworn-in-as-washingtons-first-woman-first-out-lesbian-house-speaker/"
)

_DESIGNATE_ROLE_NOTES = (
    "Speaker-designate of the Washington House: elected by the House Democratic "
    "Caucus, pending formal election by the full House when the session convenes."
)
_DESIGNATE_ASSIGNMENT_NOTES = f"Elected Speaker Designate 2019-07-31. Source: {DESIGNATE_URL}"
_SPEAKER_ASSIGNMENT_NOTES = f"Sworn in as Speaker of the House 2020-01-13. Source: {SPEAKER_URL}"

# The stale tenure the #266 classifier parked on the formal-Speaker role. Clear
# only a note that carries it, so a re-run never erases unrelated notes.
_LEGACY_TENURE_MARKER = "2021-23"


class Action(TypedDict):
    """One planned or applied mutation."""

    kind: Literal["created", "updated", "skipped", "missing"]
    entity: str
    detail: str


class Report(TypedDict):
    """Full run outcome."""

    actions: list[Action]


async def _verify_identity(conn: asyncpg.Connection) -> None:
    """Fail loud if the hardcoded prod IDs no longer resolve to the expected entities.

    The script mutates literal ULIDs captured from prod; if the DB has since
    drifted (a merge re-pointed the assignment, the person was merged, the role
    re-homed) the writes would land on the wrong rows. Assert the linkage before
    any mutation on this irreversible path.
    """
    row = await conn.fetchrow(
        "SELECT ra.person_id, ra.role_id, r.organization_id"
        " FROM role_assignments ra JOIN roles r ON r.id = ra.role_id"
        " WHERE ra.id = $1 AND ra.archived_at IS NULL",
        SPEAKER_ASSIGNMENT_ID,
    )
    if row is None:
        raise RuntimeError(
            f"formal Speaker assignment {SPEAKER_ASSIGNMENT_ID} not found (or archived) —"
            " prod has drifted; refusing to run"
        )
    mismatches = []
    if row["person_id"] != JINKINS_PERSON_ID:
        mismatches.append(f"person_id={row['person_id']} (expected {JINKINS_PERSON_ID})")
    if row["role_id"] != SPEAKER_ROLE_ID:
        mismatches.append(f"role_id={row['role_id']} (expected {SPEAKER_ROLE_ID})")
    if row["organization_id"] != WA_HOUSE_ORG_ID:
        mismatches.append(f"organization_id={row['organization_id']} (expected {WA_HOUSE_ORG_ID})")
    if mismatches:
        raise RuntimeError(
            f"assignment {SPEAKER_ASSIGNMENT_ID} identity mismatch — refusing to run: "
            + "; ".join(mismatches)
        )


async def _chamber_leader_type_id(conn: asyncpg.Connection) -> str:
    tid = await conn.fetchval("SELECT id FROM role_types WHERE slug='chamber_leader'")
    if tid is None:
        raise RuntimeError("role_type 'chamber_leader' missing — run apply_schema first")
    return tid


async def _ensure_designate_role(
    conn: asyncpg.Connection, type_id: str, *, execute: bool
) -> tuple[str | None, Action]:
    """Create the Speaker Designate role if absent; return (role_id, action)."""
    existing = await conn.fetchval(
        "SELECT id FROM roles WHERE organization_id=$1 AND lower(title)=lower($2)"
        " AND archived_at IS NULL",
        WA_HOUSE_ORG_ID,
        DESIGNATE_TITLE,
    )
    if existing:
        return existing, {"kind": "skipped", "entity": "designate role", "detail": existing}
    role_id = generate_id()
    if execute:
        await conn.execute(
            "INSERT INTO roles (id, organization_id, title, role_type_id, notes)"
            " VALUES ($1,$2,$3,$4,$5)",
            role_id,
            WA_HOUSE_ORG_ID,
            DESIGNATE_TITLE,
            type_id,
            _DESIGNATE_ROLE_NOTES,
        )
    return (
        (role_id if execute else None),
        {
            "kind": "created",
            "entity": "designate role",
            "detail": f"{DESIGNATE_TITLE} (chamber_leader)",
        },
    )


async def _ensure_designate_assignment(
    conn: asyncpg.Connection, designate_role_id: str | None, *, execute: bool
) -> Action:
    """Create Jinkins's closed designate tenure if absent (dry run: role_id may be None)."""
    if designate_role_id is not None:
        existing = await conn.fetchval(
            "SELECT id FROM role_assignments WHERE person_id=$1 AND role_id=$2"
            " AND start_date=$3 AND archived_at IS NULL",
            JINKINS_PERSON_ID,
            designate_role_id,
            DESIGNATE_START,
        )
        if existing:
            return {"kind": "skipped", "entity": "designate assignment", "detail": existing}
    if execute:
        assert designate_role_id is not None
        await check_assignment_lifespan(
            conn,
            role_id=designate_role_id,
            start_date=DESIGNATE_START,
            end_date=DESIGNATE_END,
            is_current=False,
        )
        await conn.execute(
            "INSERT INTO role_assignments"
            " (id, person_id, role_id, is_current, start_date, end_date, notes)"
            " VALUES ($1,$2,$3,FALSE,$4,$5,$6)",
            generate_id(),
            JINKINS_PERSON_ID,
            designate_role_id,
            DESIGNATE_START,
            DESIGNATE_END,
            _DESIGNATE_ASSIGNMENT_NOTES,
        )
    return {
        "kind": "created",
        "entity": "designate assignment",
        "detail": f"{DESIGNATE_START} → {DESIGNATE_END}, is_current=FALSE",
    }


async def _update_formal_speaker(conn: asyncpg.Connection, *, execute: bool) -> list[Action]:
    """Set the formal-Speaker start/notes and clear the stale role breadcrumb."""
    actions: list[Action] = []
    row = await conn.fetchrow(
        "SELECT start_date, notes FROM role_assignments WHERE id=$1 AND archived_at IS NULL",
        SPEAKER_ASSIGNMENT_ID,
    )
    if row is None:
        return [{"kind": "missing", "entity": "formal assignment", "detail": SPEAKER_ASSIGNMENT_ID}]

    if row["start_date"] == SPEAKER_START and row["notes"] == _SPEAKER_ASSIGNMENT_NOTES:
        actions.append({"kind": "skipped", "entity": "formal assignment", "detail": "already set"})
    else:
        if execute:
            await check_assignment_lifespan(
                conn,
                role_id=SPEAKER_ROLE_ID,
                start_date=SPEAKER_START,
                end_date=None,
                is_current=True,
            )
            await conn.execute(
                "UPDATE role_assignments"
                " SET start_date=$2, end_date=NULL, is_current=TRUE, notes=$3 WHERE id=$1",
                SPEAKER_ASSIGNMENT_ID,
                SPEAKER_START,
                _SPEAKER_ASSIGNMENT_NOTES,
            )
        actions.append(
            {
                "kind": "updated",
                "entity": "formal assignment",
                "detail": f"start={SPEAKER_START}, is_current=TRUE, citation set",
            }
        )

    role_notes = await conn.fetchval("SELECT notes FROM roles WHERE id=$1", SPEAKER_ROLE_ID)
    if role_notes and _LEGACY_TENURE_MARKER in role_notes:
        if execute:
            await conn.execute("UPDATE roles SET notes=NULL WHERE id=$1", SPEAKER_ROLE_ID)
        actions.append(
            {
                "kind": "updated",
                "entity": "formal role notes",
                "detail": "stale 2021-23 breadcrumb cleared",
            }
        )
    else:
        actions.append(
            {"kind": "skipped", "entity": "formal role notes", "detail": "no legacy breadcrumb"}
        )
    return actions


async def split_speaker_designate(conn: asyncpg.Connection, *, execute: bool) -> Report:
    """Create the Designate role/tenure, set the formal-Speaker dates, clear the breadcrumb."""
    await _verify_identity(conn)
    type_id = await _chamber_leader_type_id(conn)
    actions: list[Action] = []

    role_id, role_action = await _ensure_designate_role(conn, type_id, execute=execute)
    actions.append(role_action)
    actions.append(await _ensure_designate_assignment(conn, role_id, execute=execute))
    actions += await _update_formal_speaker(conn, execute=execute)
    return Report(actions=actions)


async def run(*, execute: bool) -> None:
    """Connect to DATABASE_URL and apply the split."""
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        raise RuntimeError("DATABASE_URL not set")

    conn = await asyncpg.connect(dsn)
    try:
        if execute:
            async with conn.transaction():
                report = await split_speaker_designate(conn, execute=True)
        else:
            report = await split_speaker_designate(conn, execute=False)

        for action in report["actions"]:
            logger.info("%-8s %-20s %s", action["kind"], action["entity"], action["detail"])
        verb = "Applied" if execute else "Dry run — would apply"
        logger.info("%s %d action(s)", verb, len(report["actions"]))
        if not execute:
            logger.info("Pass --execute to commit")
    finally:
        await conn.close()


def main() -> None:
    configure_logging()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--execute", action="store_true", help="Commit changes (default is dry run)"
    )
    args = parser.parse_args()
    asyncio.run(run(execute=args.execute))


if __name__ == "__main__":
    main()
