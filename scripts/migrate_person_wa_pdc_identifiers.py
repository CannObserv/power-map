"""Migrate the legacy URL-form person_wa_pdc identifiers to numeric person_id.

Issue #293: usa-wa's PDC adapter propagates ``person_wa_pdc`` using PDC's
numeric ``person_id`` (the person-stable key from the Campaign Finance Summary
SODA dataset ``3h9x-7bvm``). Some people carry a pre-existing value in an older
convention — a campaign-explorer URL keyed on ``filer_id`` + ``election_year``
— which the enrich-path conflict guard correctly refused to overwrite. This
script replaces each URL-form value with the verified numeric ``person_id`` and
preserves the URL's ``filer_id`` under the distinct ``person_wa_pdc_filer``
identifier type (seeded in schema.sql, #293).

Two batches: the 12 WA legislators from the #293 issue table (values verified
against the seated-winner cohorts), plus 19 more from the post-migration audit
(#293 batch-2 comment; ``person_id`` resolved from the SODA dataset by
``filer_id`` and name-checked against PM). Michelle Caldier is deliberately
excluded from batch 2 — her target numeric value already sits on a different
PM person (a duplicate-person pair; merge first). The 60 accesshub lobbyist
URLs are a different vocabulary entirely — tracked in #295, not here.

Match safety: a person's current value is only replaced when its extracted
filer_id(s) resolve to exactly the filer_id recorded in the issue table for
that person (Strom Peterson's value holds two URLs with the same filer_id —
they collapse to one). Any other value — a different filer, a non-URL string,
no identifier at all, multiple person_wa_pdc rows, or a target numeric value
already present on a different person — is reported and skipped, never touched.

Idempotent — a person whose value already equals the target numeric person_id
is left untouched (``exists``).

Usage:
    uv run python -m scripts.migrate_person_wa_pdc_identifiers            # dry run
    uv run python -m scripts.migrate_person_wa_pdc_identifiers --execute  # commit
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


class PdcMigration(TypedDict):
    """One row of the issue #293 migration table."""

    name: str
    person_id: str  # PM person ULID
    filer_id: str  # expected filer_id decoded from the legacy URL value
    pdc_person_id: str  # PDC's numeric person_id — the new value


# Batch 1: the #293 issue table, verified against the seated-winner cohorts.
MIGRATIONS: tuple[PdcMigration, ...] = (
    {
        "name": "Alex Ramel",
        "person_id": "01KV6PQKAP9K6VZE80RDMCKB25",
        "filer_id": "RAMEA  109",
        "pdc_person_id": "30420",
    },
    {
        "name": "Debra Entenman",
        "person_id": "01KV6PQP6DVNXCF8BH2XJD7Z24",
        "filer_id": "ENTED  031",
        "pdc_person_id": "1083",
    },
    {
        "name": "Drew MacEwen",
        "person_id": "01KV6PQPG17NJV05JJH07ZGEWT",
        "filer_id": "MACED  592",
        "pdc_person_id": "821",
    },
    {
        "name": "Jeremie Dufault",
        "person_id": "01KV6PQR4WMJXCW9Z3C00P4969",
        "filer_id": "DUFAJ  942",
        "pdc_person_id": "324",
    },
    {
        "name": "June Robinson",
        "person_id": "01KV6PQS4PKDT9YN0MPBW4K8NC",
        "filer_id": "ROBIJ  206",
        "pdc_person_id": "48586",
    },
    {
        "name": "Kristine Reeves",
        "person_id": "01KV6PQSW40X3Z8JJQVVHBNM5B",
        "filer_id": "REEVK  093",
        "pdc_person_id": "636",
    },
    {
        "name": "Lauren Davis",
        "person_id": "01KV6PQT308PX4S0HPMYEWRDWD",
        "filer_id": "DAVIL  109",
        "pdc_person_id": "30396",
    },
    {
        "name": "Mark Schoesler",
        "person_id": "01KV6PQTRNYBY9GDMY0DRM9T89",
        "filer_id": "SCHOM  169",
        "pdc_person_id": "1093",
    },
    {
        "name": "Skyler Rude",
        "person_id": "01KV6PQYX52PPTS70M0D6ZM3D2",
        "filer_id": "RUDES  504",
        "pdc_person_id": "25246",
    },
    {
        "name": "Strom Peterson",
        "person_id": "01KV6PQZEAPR4PS3VNPRWF6N5S",
        "filer_id": "PETES  026",
        "pdc_person_id": "159",
    },
    {
        "name": "Tana Senn",
        "person_id": "01KV6PQZR90RT5DX4E3RQPJ5AV",
        "filer_id": "SENNT  040",
        "pdc_person_id": "514",
    },
    {
        "name": "Timm Ormsby",
        "person_id": "01KV6PR02KDJV03ZXHBS8N81T3",
        "filer_id": "ORMST  210",
        "pdc_person_id": "230",
    },
    # Batch 2 (#293 comment): resolved from SODA 3h9x-7bvm by filer_id,
    # name-checked against PM display names. Michelle Caldier excluded — see
    # module docstring.
    {
        "name": "Adam Cornell",
        "person_id": "01KV6PQK53FHRWADSGVHEXJY0Y",
        "filer_id": "CORNA  020",
        "pdc_person_id": "158",
    },
    {
        "name": "Andy Hover",
        "person_id": "01KV6PQKMA50ZMHKYY3R7YJ455",
        "filer_id": "HOVEA  862",
        "pdc_person_id": "3807",
    },
    {
        "name": "Bill Jenkin",
        "person_id": "01KV6PQM7E96E4A09HEQ8B0ACM",
        "filer_id": "JENKW  350",
        "pdc_person_id": "29579",
    },
    {
        "name": "Bob Bugert",
        "person_id": "01KV6PQM8JBNP0PGXCD0S7WRJB",
        "filer_id": "BUGER  801",
        "pdc_person_id": "363",
    },
    {
        "name": "Brad Klippert",
        "person_id": "01KV6PQMDKTNAFA261Q0VYF6VM",
        "filer_id": "KLIPB  336",
        "pdc_person_id": "356",
    },
    {
        "name": "Brandon Vick",
        "person_id": "01KV6PQMG60JEGZGQET0C1A8QZ",
        "filer_id": "VICKB  687",
        "pdc_person_id": "440",
    },
    {
        "name": "Brian Blake",
        "person_id": "01KV6PQMHCWRH7GFYQ94E979TE",
        "filer_id": "BLAKB  504",
        "pdc_person_id": "140",
    },
    {
        "name": "Chris Branch",
        "person_id": "01KV6PQN6RAA859TMN8H4VRDT8",
        "filer_id": "BRANC  841",
        "pdc_person_id": "3978",
    },
    {
        "name": "Eileen Cody",
        "person_id": "01KV6PQPP6FTR161QXWKAT7CSY",
        "filer_id": "CODYE  126",
        "pdc_person_id": "463",
    },
    {
        "name": "Jenny Durkan",
        "person_id": "01KV6PQR4PNW2XJ86AH96C64TA",
        "filer_id": "DURKJ  144",
        "pdc_person_id": "3296",
    },
    {
        "name": "Jim DeTro",
        "person_id": "01KV6PQRDH00F9CPTEXYNY51HV",
        "filer_id": "DETRJ  855",
        "pdc_person_id": "791",
    },
    {
        "name": "John C. Skinder",
        "person_id": "01KV6PQRN6HAZAHYD120NC3ETR",
        "filer_id": "SKINJ  508",
        "pdc_person_id": "4191",
    },
    {
        "name": "John Snaza",
        "person_id": "01KV6PQRRDKNN8F0SP5678DTME",
        "filer_id": "SNAZJ  503",
        "pdc_person_id": "65",
    },
    {
        "name": "Kelly Chambers",
        "person_id": "01KV6PQSK3JTP16B4WGFK2V4NQ",
        "filer_id": "CHAMK  371",
        "pdc_person_id": "11",
    },
    {
        "name": "Kevin Overbay",
        "person_id": "01KV6PQSQ7C8TTHZ8KVY9HA1QK",
        "filer_id": "OVERK  807",
        "pdc_person_id": "4088",
    },
    {
        "name": "Mary Sue Wilson",
        "person_id": "01KV6PQTZE5R81N28ZYFAPSFR4",
        "filer_id": "WILSM  502",
        "pdc_person_id": "4160",
    },
    {
        "name": "Matt Shea",
        "person_id": "01KV6PQV2GEKH72582NZFPXNVG",
        "filer_id": "SHEAM  228",
        "pdc_person_id": "252",
    },
    {
        "name": "Robert Sutherland",
        "person_id": "01KV6PQXMDAPXW5W28R0E0AG90",
        "filer_id": "SUTHR  252",
        "pdc_person_id": "180",
    },
    {
        "name": "Sam Hunt",
        "person_id": "01KV6PQY43BPYAJNHX8VXM7S48",
        "filer_id": "HUNTS  506",
        "pdc_person_id": "26204",
    },
)

PdcMigrationStatus = Literal[
    "applied", "planned", "exists", "conflict", "missing", "ambiguous", "collision"
]


class PdcMigrationAction(TypedDict):
    """One migration outcome per person in MIGRATIONS (see module docstring)."""

    name: str
    person_id: str
    pdc_person_id: str
    status: PdcMigrationStatus


_TYPE_ID_SQL = "SELECT id FROM entity_identifier_types WHERE slug = $1"

_CURRENT_ROWS_SQL = """
SELECT i.id, i.value
FROM identifiers i
WHERE i.entity_identifier_type_id = $1 AND i.entity_id = $2
ORDER BY i.created_at
"""

_FILER_VALUES_SQL = """
SELECT i.value
FROM identifiers i
WHERE i.entity_identifier_type_id = $1 AND i.entity_id = $2
"""

_INSERT_SQL = """
INSERT INTO identifiers (id, entity_id, entity_identifier_type_id, value)
VALUES ($1, $2, $3, $4)
"""

_UPDATE_SQL = "UPDATE identifiers SET value = $2 WHERE id = $1"

_VALUE_ON_OTHER_PERSON_SQL = """
SELECT i.entity_id
FROM identifiers i
WHERE i.entity_identifier_type_id = $1 AND i.value = $2 AND i.entity_id <> $3
"""


def extract_filer_ids(value: str) -> list[str]:
    """Extract distinct decoded filer_id values from a URL-form identifier value.

    The legacy value is one or more campaign-explorer URLs separated by
    whitespace (spaces inside a URL are percent-encoded, so splitting on
    whitespace is safe). Order is preserved; duplicates collapse. A non-URL
    value, or a URL without a filer_id query param, contributes nothing.
    """
    filer_ids: list[str] = []
    for token in value.split():
        parts = urlsplit(token)
        if parts.scheme not in ("http", "https"):
            continue
        for filer_id in parse_qs(parts.query).get("filer_id", []):
            if filer_id not in filer_ids:
                filer_ids.append(filer_id)
    return filer_ids


async def migrate_pdc_identifiers(
    conn: asyncpg.Connection, *, execute: bool
) -> list[PdcMigrationAction]:
    """Replace URL-form person_wa_pdc values with numeric PDC person_ids.

    Returns one ``PdcMigrationAction`` per person in MIGRATIONS, whose
    ``status`` is one of ``applied`` (migrated), ``planned`` (dry run, would
    migrate), ``exists`` (already numeric), ``conflict`` (unexpected value —
    skipped), ``missing`` (no person_wa_pdc identifier — skipped),
    ``ambiguous`` (multiple person_wa_pdc rows — skipped), or ``collision``
    (target numeric value already on a different person, i.e. a duplicate
    person pair — skipped; merge the people first). Only ``applied`` mutates:
    the filer_id is preserved as a ``person_wa_pdc_filer`` identifier (unless
    already present) and the person_wa_pdc row is updated in place.
    """
    pdc_type_id = await conn.fetchval(_TYPE_ID_SQL, "person_wa_pdc")
    filer_type_id = await conn.fetchval(_TYPE_ID_SQL, "person_wa_pdc_filer")
    if pdc_type_id is None or filer_type_id is None:
        raise RuntimeError(
            "person_wa_pdc / person_wa_pdc_filer identifier type not found — run apply_schema first"
        )

    actions: list[PdcMigrationAction] = []
    for m in MIGRATIONS:
        action: PdcMigrationAction = {
            "name": m["name"],
            "person_id": m["person_id"],
            "pdc_person_id": m["pdc_person_id"],
            "status": "missing",
        }
        actions.append(action)

        rows = await conn.fetch(_CURRENT_ROWS_SQL, pdc_type_id, m["person_id"])
        if not rows:
            logger.warning(
                "%s (%s): no person_wa_pdc identifier — skipping", m["name"], m["person_id"]
            )
            continue
        if len(rows) > 1:
            logger.warning(
                "%s (%s): %d person_wa_pdc rows — ambiguous, skipping",
                m["name"],
                m["person_id"],
                len(rows),
            )
            action["status"] = "ambiguous"
            continue

        row = rows[0]
        if row["value"] == m["pdc_person_id"]:
            action["status"] = "exists"
            continue

        filer_ids = extract_filer_ids(row["value"])
        if filer_ids != [m["filer_id"]]:
            logger.warning(
                "%s (%s): value %r resolves to filer_ids %r, expected [%r] — skipping",
                m["name"],
                m["person_id"],
                row["value"],
                filer_ids,
                m["filer_id"],
            )
            action["status"] = "conflict"
            continue

        holders = await conn.fetch(
            _VALUE_ON_OTHER_PERSON_SQL, pdc_type_id, m["pdc_person_id"], m["person_id"]
        )
        if holders:
            logger.warning(
                "%s (%s): person_wa_pdc=%s already on person %s — duplicate pair, "
                "merge first; skipping",
                m["name"],
                m["person_id"],
                m["pdc_person_id"],
                holders[0]["entity_id"],
            )
            action["status"] = "collision"
            continue

        if not execute:
            logger.info(
                "Would set person_wa_pdc=%s and preserve filer_id=%r for %s (%s)",
                m["pdc_person_id"],
                m["filer_id"],
                m["name"],
                m["person_id"],
            )
            action["status"] = "planned"
            continue

        existing_filers = {
            r["value"] for r in await conn.fetch(_FILER_VALUES_SQL, filer_type_id, m["person_id"])
        }
        if m["filer_id"] not in existing_filers:
            await conn.execute(
                _INSERT_SQL, generate_id(), m["person_id"], filer_type_id, m["filer_id"]
            )
        await conn.execute(_UPDATE_SQL, row["id"], m["pdc_person_id"])
        logger.info(
            "Set person_wa_pdc=%s and preserved filer_id=%r for %s (%s)",
            m["pdc_person_id"],
            m["filer_id"],
            m["name"],
            m["person_id"],
        )
        action["status"] = "applied"

    return actions


async def run(*, execute: bool) -> None:
    """Connect to DATABASE_URL and migrate the legacy identifiers."""
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        raise RuntimeError("DATABASE_URL not set")

    conn = await asyncpg.connect(dsn)
    try:
        if execute:
            async with conn.transaction():
                actions = await migrate_pdc_identifiers(conn, execute=True)
        else:
            actions = await migrate_pdc_identifiers(conn, execute=False)

        # Surface every outcome, not just applied — a conflict/missing person
        # shows up here so a partial run is never mistaken for "all done".
        counts = Counter(a["status"] for a in actions)
        breakdown = ", ".join(f"{status}={n}" for status, n in sorted(counts.items()))
        if not execute:
            logger.info(
                "Dry run — %d identifier(s) would be migrated (%s); pass --execute",
                counts["planned"],
                breakdown,
            )
        else:
            logger.info(
                "Migrated %d person_wa_pdc identifier(s) (%s)", counts["applied"], breakdown
            )
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
