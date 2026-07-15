"""Resolve the 7 non-node org_wa_pdc remnants left by the #296 retype.

``scripts/retype_org_wa_pdc_identifiers`` retyped the 37 accesshub node URLs to
bare numeric lobbyist filer_ids and reported 7 values it must not guess at. This
script clears those 7, by the two treatments agreed in #296:

- ``committee`` (3): a campaign-explorer **committee** URL keys a campaign-finance
  PAC — a different PDC subsystem from the lobbyist ``org_wa_pdc``. Retype to the
  bare committee filer_id under ``org_wa_pdc_committee`` (seeded in schema.sql),
  preserve the URL as a ``wa_pdc`` link, delete the org_wa_pdc row.
- ``i502_note`` (4): an ``I-502 …`` string is a WSLCB cannabis-license *type*
  (not PDC data, not even an identifier). Move the license type to the org's
  ``notes`` and delete the org_wa_pdc row. Modeling real WSLCB license *numbers*
  under ``org_wslcb`` is future work — only the coarse type is known here.

Match safety (per #293/#295): an org is only touched when its single org_wa_pdc
value exactly equals the audited value below; anything else — a different value,
no row, or multiple rows — is reported and skipped. Idempotent: a resolved org
(row gone, end-state present) reports ``exists``.

Usage:
    uv run python -m scripts.cleanup_org_wa_pdc_remnants            # dry run
    uv run python -m scripts.cleanup_org_wa_pdc_remnants --execute  # commit
"""

import argparse
import asyncio
import os
from collections import Counter
from typing import Literal, TypedDict
from urllib.parse import parse_qs, urlsplit

import asyncpg

from src.core.db import generate_id
from src.core.logging import configure_logging, get_logger

logger = get_logger(__name__)

RemnantTreatment = Literal["committee", "i502_note"]


class Remnant(TypedDict):
    """One #296 org_wa_pdc remnant and its agreed treatment."""

    name: str
    org_id: str  # PM organization ULID
    treatment: RemnantTreatment
    value: str  # exact current org_wa_pdc value to match
    committee_filer_id: str | None  # committee: bare filer_id to mint
    note: str | None  # i502_note: the note text to append


# The 7 remnants, reviewed in #296. Committee filer_ids are the URL-decoded,
# space-padded PDC keys (mirroring person_wa_pdc_filer).
REMNANTS: tuple[Remnant, ...] = (
    {
        "name": "Laboratory Guild",
        "org_id": "01KV6PPCFBDCV42CE8HCSQYEXY",
        "treatment": "committee",
        "value": "https://www.pdc.wa.gov/browse/campaign-explorer/committee?filer_id=LABORG%20503&election_year=2018",
        "committee_filer_id": "LABORG 503",
        "note": None,
    },
    {
        "name": "Snohomish Ebony PAC",
        "org_id": "01KV6PQ0XKABEMAHDNXNGGCTTB",
        "treatment": "committee",
        "value": "https://www.pdc.wa.gov/browse/campaign-explorer/committee?filer_id=SNOHO--087&election_year=2021",
        "committee_filer_id": "SNOHO--087",
        "note": None,
    },
    {
        "name": "VIPER PAC",
        "org_id": "01KV6PQCDSS74XGJFQCCW5N4C5",
        "treatment": "committee",
        "value": "https://www.pdc.wa.gov/browse/campaign-explorer/committee?filer_id=VIPEP%20%20102&election_year=2018",
        "committee_filer_id": "VIPEP  102",
        "note": None,
    },
    {
        "name": "Royal Tree Gardens",
        "org_id": "01KV6PPXKPC1Z6EMV3X8XE657W",
        "treatment": "i502_note",
        "value": "I-502 P/P",
        "committee_filer_id": None,
        "note": "WSLCB I-502 license type: Producer/Processor",
    },
    {
        "name": "Sapphire Meadows",
        "org_id": "01KV6PPY2QG71CA23W7K0RJFNF",
        "treatment": "i502_note",
        "value": "I-502 P/P",
        "committee_filer_id": None,
        "note": "WSLCB I-502 license type: Producer/Processor",
    },
    {
        "name": "Saturn Group",
        "org_id": "01KV6PPY3614CZYKK8FCXJGVS6",
        "treatment": "i502_note",
        "value": "I-502 processor",
        "committee_filer_id": None,
        "note": "WSLCB I-502 license type: Processor",
    },
    {
        "name": "Satori",
        "org_id": "01KV6PPY2W7TJ1Q14B93YDMSZC",
        "treatment": "i502_note",
        "value": "I-502 Retailer",
        "committee_filer_id": None,
        "note": "WSLCB I-502 license type: Retailer",
    },
)

RemnantStatus = Literal["applied", "planned", "exists", "conflict", "missing"]


class RemnantAction(TypedDict):
    """One outcome per org in REMNANTS."""

    name: str
    org_id: str
    treatment: RemnantTreatment
    status: RemnantStatus


_TYPE_ID_SQL = "SELECT id FROM entity_identifier_types WHERE slug = $1"
_LINK_TYPE_ID_SQL = "SELECT id FROM link_types WHERE slug = 'wa_pdc'"

_ROWS_SQL = """
SELECT i.id, i.value
FROM identifiers i
WHERE i.entity_identifier_type_id = $1 AND i.entity_id = $2
ORDER BY i.created_at
"""

_COMMITTEE_VALUES_SQL = """
SELECT i.value
FROM identifiers i
WHERE i.entity_identifier_type_id = $1 AND i.entity_id = $2
"""

_INSERT_IDENTIFIER_SQL = """
INSERT INTO identifiers (id, entity_id, entity_identifier_type_id, value)
VALUES ($1, $2, $3, $4)
"""

_INSERT_LINK_SQL = """
INSERT INTO links (id, entity_type, entity_id, url, link_type_id)
VALUES ($1, 'organization', $2, $3, $4)
ON CONFLICT (entity_type, entity_id, url, link_type_id) DO NOTHING
"""

_DELETE_IDENTIFIER_SQL = "DELETE FROM identifiers WHERE id = $1"

_NOTES_SQL = "SELECT notes FROM organizations WHERE id = $1"
_UPDATE_NOTES_SQL = "UPDATE organizations SET notes = $2 WHERE id = $1"


def _committee_filer_from_url(value: str) -> str | None:
    """Return the decoded committee ``filer_id`` from a campaign-explorer URL."""
    parts = urlsplit(value.strip())
    if parts.scheme not in ("http", "https"):
        return None
    filers = parse_qs(parts.query).get("filer_id", [])
    return filers[0] if filers else None


def _appended_notes(current: str | None, note: str) -> str:
    """Append ``note`` to ``current`` notes, idempotently."""
    current = (current or "").rstrip()
    if not current:
        return note
    if note in current:
        return current
    return f"{current}\n{note}"


async def _end_state_present(conn: asyncpg.Connection, r: Remnant, committee_type_id: str) -> bool:
    """True when a prior run already resolved this org."""
    if r["treatment"] == "committee":
        present = {
            row["value"]
            for row in await conn.fetch(_COMMITTEE_VALUES_SQL, committee_type_id, r["org_id"])
        }
        return r["committee_filer_id"] in present
    notes = await conn.fetchval(_NOTES_SQL, r["org_id"])
    return notes is not None and r["note"] in notes


async def cleanup_org_wa_pdc_remnants(
    conn: asyncpg.Connection, *, execute: bool
) -> list[RemnantAction]:
    """Resolve the 7 non-node org_wa_pdc remnants per the #296 treatments.

    Returns one ``RemnantAction`` per org. ``status`` is ``applied`` /
    ``planned`` (dry run) / ``exists`` (already resolved) / ``conflict``
    (value != audited — skipped) / ``missing`` (no matching org_wa_pdc row and
    no prior end-state — skipped). Only ``applied`` mutates.
    """
    pdc_type_id = await conn.fetchval(_TYPE_ID_SQL, "org_wa_pdc")
    committee_type_id = await conn.fetchval(_TYPE_ID_SQL, "org_wa_pdc_committee")
    link_type_id = await conn.fetchval(_LINK_TYPE_ID_SQL)
    if pdc_type_id is None or committee_type_id is None or link_type_id is None:
        raise RuntimeError(
            "org_wa_pdc / org_wa_pdc_committee identifier type or wa_pdc link type not "
            "found — run apply_schema first"
        )

    actions: list[RemnantAction] = []
    for r in REMNANTS:
        action: RemnantAction = {
            "name": r["name"],
            "org_id": r["org_id"],
            "treatment": r["treatment"],
            "status": "missing",
        }
        actions.append(action)

        rows = await conn.fetch(_ROWS_SQL, pdc_type_id, r["org_id"])
        if not rows:
            if await _end_state_present(conn, r, committee_type_id):
                action["status"] = "exists"
            else:
                logger.warning("%s (%s): no org_wa_pdc row — skipping", r["name"], r["org_id"])
            continue
        if len(rows) != 1 or rows[0]["value"] != r["value"]:
            logger.warning(
                "%s (%s): values %r != audited %r — skipping",
                r["name"],
                r["org_id"],
                [row["value"] for row in rows],
                r["value"],
            )
            action["status"] = "conflict"
            continue

        if not execute:
            logger.info(
                "Would apply %s treatment to %s (%s)", r["treatment"], r["name"], r["org_id"]
            )
            action["status"] = "planned"
            continue

        if r["treatment"] == "committee":
            # Cross-check the audited filer_id against the URL before minting.
            if _committee_filer_from_url(r["value"]) != r["committee_filer_id"]:
                logger.warning(
                    "%s (%s): URL filer_id != audited %r — skipping",
                    r["name"],
                    r["org_id"],
                    r["committee_filer_id"],
                )
                action["status"] = "conflict"
                continue
            existing = {
                row["value"]
                for row in await conn.fetch(_COMMITTEE_VALUES_SQL, committee_type_id, r["org_id"])
            }
            if r["committee_filer_id"] not in existing:
                await conn.execute(
                    _INSERT_IDENTIFIER_SQL,
                    generate_id(),
                    r["org_id"],
                    committee_type_id,
                    r["committee_filer_id"],
                )
            await conn.execute(
                _INSERT_LINK_SQL, generate_id(), r["org_id"], r["value"], link_type_id
            )
        else:
            notes = await conn.fetchval(_NOTES_SQL, r["org_id"])
            await conn.execute(_UPDATE_NOTES_SQL, r["org_id"], _appended_notes(notes, r["note"]))

        await conn.execute(_DELETE_IDENTIFIER_SQL, rows[0]["id"])
        logger.info("Applied %s treatment to %s (%s)", r["treatment"], r["name"], r["org_id"])
        action["status"] = "applied"

    return actions


async def run(*, execute: bool) -> None:
    """Connect to DATABASE_URL and resolve the remnants."""
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        raise RuntimeError("DATABASE_URL not set")

    conn = await asyncpg.connect(dsn)
    try:
        if execute:
            async with conn.transaction():
                actions = await cleanup_org_wa_pdc_remnants(conn, execute=True)
        else:
            actions = await cleanup_org_wa_pdc_remnants(conn, execute=False)

        counts = Counter(a["status"] for a in actions)
        breakdown = ", ".join(f"{status}={n}" for status, n in sorted(counts.items()))
        if not execute:
            logger.info(
                "Dry run — %d remnant(s) would be resolved (%s); pass --execute",
                counts["planned"],
                breakdown,
            )
        else:
            logger.info("Resolved %d org_wa_pdc remnant(s) (%s)", counts["applied"], breakdown)
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
