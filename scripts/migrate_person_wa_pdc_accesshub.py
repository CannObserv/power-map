"""Retype the legacy accesshub (lobbyist) URL-form person_wa_pdc identifiers.

Issue #295: 60 people carry a ``person_wa_pdc`` value that is a PDC Lobbyist
Reporting (accesshub) URL — ``https://accesshub.pdc.wa.gov/node/NNNNN`` — not
a campaign-explorer URL (#293) and not PDC's numeric ``person_id``. The audit
showed the node ID keys the lobbyist **firm's** directory page, not the
person: in 49/56 cases it equals the firm's ``filer_id`` in PDC's Lobbyist
Agents SODA dataset (``bp5b-jrti``), and the two node values shared by two
people each are multi-agent firms (Gordon Thomas Honeywell, FMS Global). The
person-stable key in the modern vocabulary is the dataset's ``agent_id``.

Treatment per person (the verified crosswalk lives in the #295 comments):

- ``retype`` (55): insert one ``person_wa_pdc_lobbyist_agent`` identifier per
  verified ``agent_id`` (PDC keeps duplicate agent rows for name variants, so
  a person may get several), preserve the raw URL as a ``wa_pdc`` link, then
  delete the URL-form ``person_wa_pdc`` row.
- ``link_only`` (2): Jack Goldberg and Victor (Vic) Colman have no modern PDC
  key (pre-2016 lobbyists absent from the dataset) — preserve the URL as a
  ``wa_pdc`` link and delete the row; nothing is minted.
- ``deferred`` (3): never touched, only reported. "Dylan Doty" and
  "J. Dylan Doty" are a PM duplicate-person pair (merge first — same human,
  agents 266/429); Mike Moran's agent match is ambiguous (two distinct
  "Michael Moran" agent lineages; his node value matches neither).

Match safety: a person's row is only touched when its value exactly equals
the audited URL. Any other value, no row, multiple rows, or a target
``agent_id`` already on a different person is reported and skipped, never
touched. Idempotent — a person whose URL row is gone and whose expected
end-state (agent identifiers, or the link for ``link_only``) is present
reports ``exists``.

Usage:
    uv run python -m scripts.migrate_person_wa_pdc_accesshub            # dry run
    uv run python -m scripts.migrate_person_wa_pdc_accesshub --execute  # commit
"""

import argparse
import asyncio
import os
from collections import Counter
from typing import Literal, TypedDict

import asyncpg

from src.core.db import generate_id
from src.core.logging import configure_logging, get_logger

logger = get_logger(__name__)

AccesshubTreatment = Literal["retype", "link_only", "deferred"]


class AccesshubRetype(TypedDict):
    """One row of the #295 crosswalk (node -> firm -> verified agent_ids)."""

    name: str
    person_id: str  # PM person ULID
    url: str  # exact legacy person_wa_pdc value
    agent_ids: tuple[str, ...]  # verified PDC agent_ids; empty unless retype
    treatment: AccesshubTreatment


# The #295 crosswalk: node -> firm (filer_id) -> name-verified agent_id(s),
# built against SODA dataset bp5b-jrti and reviewed in the issue comments.
# Juliana Rowe is PDC's "Juliana Roe" (node==filer 24516 exact); Philip
# Singleton is PDC's "Phillip Singleton" (FMS roster).
RETYPES: tuple[AccesshubRetype, ...] = (
    {
        "name": "Albert Sardinas",
        "person_id": "01KV6PQK8VJDYT67MJ9JET6FDF",
        "url": "https://accesshub.pdc.wa.gov/node/67155",
        "agent_ids": ("2250",),
        "treatment": "retype",
    },
    {
        "name": "Alexandra Wehinger",
        "person_id": "01KV6PQKB9VS1KGGAKX6F3Y307",
        "url": "https://accesshub.pdc.wa.gov/node/59611",
        "agent_ids": ("2091", "2092", "2093"),
        "treatment": "retype",
    },
    {
        "name": "Amy Brackenbury",
        "person_id": "01KV6PQKFD3ZD16NDFASV8XF5S",
        "url": "https://accesshub.pdc.wa.gov/node/17872",
        "agent_ids": ("828", "829"),
        "treatment": "retype",
    },
    {
        "name": "Anne Lee",
        "person_id": "01KV6PQKQXE5ZFVP6NEAZ3J6RA",
        "url": "https://accesshub.pdc.wa.gov/node/17734",
        "agent_ids": ("626", "627"),
        "treatment": "retype",
    },
    {
        "name": "Annie McGrath",
        "person_id": "01KV6PQKRND62DGN6CN533WHJ4",
        "url": "https://accesshub.pdc.wa.gov/node/39750",
        "agent_ids": ("1619", "1620"),
        "treatment": "retype",
    },
    {
        "name": "Bailey Hirschburg",
        "person_id": "01KV6PQM15D7Q4V0T0QGP1102X",
        "url": "https://accesshub.pdc.wa.gov/node/28470",
        "agent_ids": ("1328",),
        "treatment": "retype",
    },
    {
        "name": "Bob Battles",
        "person_id": "01KV6PQM8ETT8YD196XCYXZ0YA",
        "url": "https://accesshub.pdc.wa.gov/node/17367",
        "agent_ids": ("60", "61"),
        "treatment": "retype",
    },
    {
        "name": "Brad Boswell",
        "person_id": "01KV6PQMD3M2GQMBH9QBG7NMYP",
        "url": "https://accesshub.pdc.wa.gov/node/17398",
        "agent_ids": ("110",),
        "treatment": "retype",
    },
    {
        "name": "Brenda Wiest",
        "person_id": "01KV6PQMGG9D2XVN50C98K6K1B",
        "url": "https://accesshub.pdc.wa.gov/node/18090",
        "agent_ids": ("1189",),
        "treatment": "retype",
    },
    {
        "name": "Briahna Murray",
        "person_id": "01KV6PQMH78YMSCWE7EFPNMJ5D",
        "url": "https://accesshub.pdc.wa.gov/node/17581",
        "agent_ids": ("387",),
        "treatment": "retype",
    },
    {
        "name": "Brooke Davies",
        "person_id": "01KV6PQMMFYWXW3HDX2YJ7A2X6",
        "url": "https://accesshub.pdc.wa.gov/node/48956",
        "agent_ids": ("1788",),
        "treatment": "retype",
    },
    {
        "name": "Bruce Beckett",
        "person_id": "01KV6PQMMSJHAWYC12ZH5344XF",
        "url": "https://accesshub.pdc.wa.gov/node/59875",
        "agent_ids": ("68", "69"),
        "treatment": "retype",
    },
    {
        "name": "Bryan McConaughy",
        "person_id": "01KV6PQMPNQADP43HC0VBFKV2W",
        "url": "https://accesshub.pdc.wa.gov/node/26001",
        "agent_ids": ("1242", "1243"),
        "treatment": "retype",
    },
    {
        "name": "Brynn Brady",
        "person_id": "01KV6PQMQ189FKZ3W8TN5T6MGF",
        "url": "https://accesshub.pdc.wa.gov/node/17401",
        "agent_ids": ("113", "114"),
        "treatment": "retype",
    },
    {
        "name": "Carolyn Logue",
        "person_id": "01KV6PQMXWHNS5AE2AM5NNVEW2",
        "url": "https://accesshub.pdc.wa.gov/node/17751",
        "agent_ids": ("649",),
        "treatment": "retype",
    },
    {
        "name": "Chris Marr",
        "person_id": "01KV6PQN7JGPN4DZTCYF87MP2V",
        "url": "https://accesshub.pdc.wa.gov/node/17773",
        "agent_ids": ("679",),
        "treatment": "retype",
    },
    {
        "name": "Chris Ramirez",
        "person_id": "01KV6PQN7Y29P4K4AZKDQZ59Z5",
        "url": "https://accesshub.pdc.wa.gov/node/96856",
        "agent_ids": ("2733",),
        "treatment": "retype",
    },
    {
        "name": "David Mendoza",
        "person_id": "01KV6PQP39D582ZE80QWTZNYCH",
        "url": "https://accesshub.pdc.wa.gov/node/40254",
        "agent_ids": ("1647", "1648"),
        "treatment": "retype",
    },
    {
        "name": "Deborah Herron",
        "person_id": "01KV6PQP67ZKVZSYR5PGSE2B1K",
        "url": "https://accesshub.pdc.wa.gov/node/17634",
        "agent_ids": ("489",),
        "treatment": "retype",
    },
    {
        "name": "Diana Carlen",
        "person_id": "01KV6PQP9Y1Z5BS10J5QEBG5KG",
        "url": "https://accesshub.pdc.wa.gov/node/17581",
        "agent_ids": ("390",),
        "treatment": "retype",
    },
    {
        "name": "Doug Levy",
        "person_id": "01KV6PQPERGMYPZ8E75ERAGEX5",
        "url": "https://accesshub.pdc.wa.gov/node/17870",
        "agent_ids": ("824", "825"),
        "treatment": "retype",
    },
    {
        "name": "Dylan Doty",
        "person_id": "01KV6PQPJTEBKM13599W3E3CC1",
        "url": "https://accesshub.pdc.wa.gov/node/17496",
        "agent_ids": (),
        "treatment": "deferred",
    },
    {
        "name": "Ezra Eickmeyer",
        "person_id": "01KV6PQQ1E32B54VZCF313GFKF",
        "url": "https://accesshub.pdc.wa.gov/node/62865",
        "agent_ids": ("839", "2208", "2209"),
        "treatment": "retype",
    },
    {
        "name": "Fred Yancey",
        "person_id": "01KV6PQQ2JC2TP03XPGSRZV7Q8",
        "url": "https://accesshub.pdc.wa.gov/node/17848",
        "agent_ids": ("793", "794", "795"),
        "treatment": "retype",
    },
    {
        "name": "Holly Chisa",
        "person_id": "01KV6PQQE2G0XK95TGG9QZMG3A",
        "url": "https://accesshub.pdc.wa.gov/node/17659",
        "agent_ids": ("520",),
        "treatment": "retype",
    },
    {
        "name": "Jack Goldberg",
        "person_id": "01KV6PQQHQGFBFKF8P9NV1AYR8",
        "url": "https://accesshub.pdc.wa.gov/node/17996",
        "agent_ids": (),
        "treatment": "link_only",
    },
    {
        "name": "Jaime Bodden",
        "person_id": "01KV6PQQJGZ6E28MDRZS5PE608",
        "url": "https://accesshub.pdc.wa.gov/node/38415",
        "agent_ids": ("1560",),
        "treatment": "retype",
    },
    {
        "name": "James Paribello",
        "person_id": "01KV6PQQNDAK8NY7ZEQN32XK88",
        "url": "https://accesshub.pdc.wa.gov/node/27257",
        "agent_ids": ("1252", "1253"),
        "treatment": "retype",
    },
    {
        "name": "James W. Potts",
        "person_id": "01KV6PQQNP5JXQASG2YJBQ6K7S",
        "url": "https://accesshub.pdc.wa.gov/node/17897",
        "agent_ids": ("867", "868"),
        "treatment": "retype",
    },
    {
        "name": "J. Dylan Doty",
        "person_id": "01KV6PQQHGTVCNSTV8NHWNAJNB",
        "url": "https://accesshub.pdc.wa.gov/reports/lobbyist_agent_picture.html?vid=29261&firstname=J%20DYLAN&middlename=&lastname=DOTY",
        "agent_ids": (),
        "treatment": "deferred",
    },
    {
        "name": "Jeff Warnke",
        "person_id": "01KV6PQR0TY6T893RN4A1Y0B57",
        "url": "https://accesshub.pdc.wa.gov/node/18065",
        "agent_ids": ("1141",),
        "treatment": "retype",
    },
    {
        "name": "John Traynor",
        "person_id": "01KV6PQRSN0SGYMM8QDVEBWBXT",
        "url": "https://accesshub.pdc.wa.gov/node/74267",
        "agent_ids": ("2393",),
        "treatment": "retype",
    },
    {
        "name": "Joshua Estes",
        "person_id": "01KV6PQS0HAZGAMZFGZ9KB1TPD",
        "url": "https://accesshub.pdc.wa.gov/node/50775",
        "agent_ids": ("830", "831"),
        "treatment": "retype",
    },
    {
        "name": "Josh Weiss",
        "person_id": "01KV6PQS06A92N26QVYXRMK3KE",
        "url": "https://accesshub.pdc.wa.gov/node/18076",
        "agent_ids": ("1171",),
        "treatment": "retype",
    },
    {
        "name": "Juliana Rowe",
        "person_id": "01KV6PQS3CEQNRV98VP3E7KMSY",
        "url": "https://accesshub.pdc.wa.gov/node/24516",
        "agent_ids": ("1219",),
        "treatment": "retype",
    },
    {
        "name": "Katie Kolan",
        "person_id": "01KV6PQSDGYJME64THQX84BYWM",
        "url": "https://accesshub.pdc.wa.gov/node/17712",
        "agent_ids": ("599",),
        "treatment": "retype",
    },
    {
        "name": "Larry Brown",
        "person_id": "01KV6PQT140P015B0G9F0JR2J7",
        "url": "https://accesshub.pdc.wa.gov/node/17411",
        "agent_ids": ("128",),
        "treatment": "retype",
    },
    {
        "name": "Laura Pierce",
        "person_id": "01KV6PQT2TVKJX56GY32T9881F",
        "url": "https://accesshub.pdc.wa.gov/node/17892",
        "agent_ids": ("861",),
        "treatment": "retype",
    },
    {
        "name": "Logan Bahr",
        "person_id": "01KV6PQTBZBYCDRFBK3JY60JZV",
        "url": "https://accesshub.pdc.wa.gov/node/17355",
        "agent_ids": ("41", "42"),
        "treatment": "retype",
    },
    {
        "name": "Lyset Cadena",
        "person_id": "01KV6PQTGJ5F7SC1ZED9J444P2",
        "url": "https://accesshub.pdc.wa.gov/node/38463",
        "agent_ids": ("1561",),
        "treatment": "retype",
    },
    {
        "name": "Mark Riker",
        "person_id": "01KV6PQVS035K03VJ8H94WCPPJ",
        "url": "https://accesshub.pdc.wa.gov/node/39996",
        "agent_ids": ("1640",),
        "treatment": "retype",
    },
    {
        "name": "Mark Streuli",
        "person_id": "01KV6PQTSDNA8G533BE1AY1N2C",
        "url": "https://accesshub.pdc.wa.gov/reports/lobbyist_agent_picture.html?vid=97309&firstname=Mark&middlename=&lastname=Streuli",
        "agent_ids": ("1044", "1376", "1744"),
        "treatment": "retype",
    },
    {
        "name": "Mary Catherine McAleer",
        "person_id": "01KV6PQTWER7WF1B3ECG8MBPX1",
        "url": "https://accesshub.pdc.wa.gov/node/29962",
        "agent_ids": ("1421", "1422", "1423"),
        "treatment": "retype",
    },
    {
        "name": "Matt Zuvich",
        "person_id": "01KV6PQV2QYK7YN2J2QABDSRZ1",
        "url": "https://accesshub.pdc.wa.gov/node/18111",
        "agent_ids": ("1215",),
        "treatment": "retype",
    },
    {
        "name": "Mike Moran",
        "person_id": "01KV6PQVRNHXC0H5B4W36TESY2",
        "url": "https://accesshub.pdc.wa.gov/node/19315",
        "agent_ids": (),
        "treatment": "deferred",
    },
    {
        "name": "Nancy Sapiro",
        "person_id": "01KV6PQVYM1RNFR4BH6RAJ6WH3",
        "url": "https://accesshub.pdc.wa.gov/node/17105",
        "agent_ids": ("933", "934"),
        "treatment": "retype",
    },
    {
        "name": "Neil Beaver",
        "person_id": "01KV6PQW0Z48YNHN8F1Z604S7F",
        "url": "https://accesshub.pdc.wa.gov/node/47746",
        "agent_ids": ("67",),
        "treatment": "retype",
    },
    {
        "name": "Paul Jewell",
        "person_id": "01KV6PQWNA8HKP8PG4GWWGD6MP",
        "url": "https://accesshub.pdc.wa.gov/node/45167",
        "agent_ids": ("1705",),
        "treatment": "retype",
    },
    {
        "name": "Philip Singleton",
        "person_id": "01KV6PQWV9H6QSRC2KEKJ2RPQX",
        "url": "https://accesshub.pdc.wa.gov/node/67155",
        "agent_ids": ("2261",),
        "treatment": "retype",
    },
    {
        "name": "Samantha Grad",
        "person_id": "01KV6PQY5WA9HQT0E7Q9NET9QJ",
        "url": "https://accesshub.pdc.wa.gov/node/32065",
        "agent_ids": ("1473", "1474"),
        "treatment": "retype",
    },
    {
        "name": "Sara Davenport-Smith",
        "person_id": "01KV6PQY71WN0BDW5PTDV77YM3",
        "url": "https://accesshub.pdc.wa.gov/node/17480",
        "agent_ids": ("242", "243"),
        "treatment": "retype",
    },
    {
        "name": "Sean O'Sullivan",
        "person_id": "01KV6PQYFYW9PB69SDBY2D61JQ",
        "url": "https://accesshub.pdc.wa.gov/node/17873",
        "agent_ids": ("832", "833", "834"),
        "treatment": "retype",
    },
    {
        "name": "Seth Dawson",
        "person_id": "01KV6PQYJ1C0Q7YTKYEVKX16PV",
        "url": "https://accesshub.pdc.wa.gov/node/17484",
        "agent_ids": ("248",),
        "treatment": "retype",
    },
    {
        "name": "Sharon Swanson",
        "person_id": "01KV6PQYNKYZY9944SKZCDPHCR",
        "url": "https://accesshub.pdc.wa.gov/node/48626",
        "agent_ids": ("1775", "1776"),
        "treatment": "retype",
    },
    {
        "name": "Sybill Hyppolite",
        "person_id": "01KV6PQZJSV83MQ6XYNBW8Z7NS",
        "url": "https://accesshub.pdc.wa.gov/node/17669",
        "agent_ids": ("535",),
        "treatment": "retype",
    },
    {
        "name": "Tim Thompson",
        "person_id": "01KV6PR02ES2Q2HTKD4SCJ1DJW",
        "url": "https://accesshub.pdc.wa.gov/node/18028",
        "agent_ids": ("1085", "1086"),
        "treatment": "retype",
    },
    {
        "name": "T.K. Bentler",
        "person_id": "01KV6PQZJZ8NEBSFJ5G72AF5G8",
        "url": "https://accesshub.pdc.wa.gov/node/17905",
        "agent_ids": ("884", "885"),
        "treatment": "retype",
    },
    {
        "name": "Tom Kwieciak",
        "person_id": "01KV6PR081D48XC10RFC3EHBZP",
        "url": "https://accesshub.pdc.wa.gov/node/17723",
        "agent_ids": ("613", "614"),
        "treatment": "retype",
    },
    {
        "name": "Vicki Christophersen",
        "person_id": "01KV6PR0H8TEKREAK1ZS82DT0C",
        "url": "https://accesshub.pdc.wa.gov/node/17348",
        "agent_ids": ("33",),
        "treatment": "retype",
    },
    {
        "name": "Victor (Vic) Colman",
        "person_id": "01KV6PR0HJCAJPWJQ63FC1YT7Q",
        "url": "https://accesshub.pdc.wa.gov/node/46208",
        "agent_ids": (),
        "treatment": "link_only",
    },
)


AccesshubStatus = Literal[
    "applied",
    "planned",
    "exists",
    "conflict",
    "missing",
    "ambiguous",
    "collision",
    "deferred",
]


class AccesshubAction(TypedDict):
    """One outcome per person in RETYPES (see module docstring)."""

    name: str
    person_id: str
    treatment: AccesshubTreatment
    status: AccesshubStatus


_TYPE_ID_SQL = "SELECT id FROM entity_identifier_types WHERE slug = $1"

_LINK_TYPE_ID_SQL = "SELECT id FROM link_types WHERE slug = 'wa_pdc'"

_CURRENT_ROWS_SQL = """
SELECT i.id, i.value
FROM identifiers i
WHERE i.entity_identifier_type_id = $1 AND i.entity_id = $2
ORDER BY i.created_at
"""

_AGENT_VALUES_SQL = """
SELECT i.value
FROM identifiers i
WHERE i.entity_identifier_type_id = $1 AND i.entity_id = $2
"""

_VALUE_ON_OTHER_PERSON_SQL = """
SELECT i.entity_id
FROM identifiers i
WHERE i.entity_identifier_type_id = $1 AND i.value = $2 AND i.entity_id <> $3
"""

_INSERT_IDENTIFIER_SQL = """
INSERT INTO identifiers (id, entity_id, entity_identifier_type_id, value)
VALUES ($1, $2, $3, $4)
"""

_LINK_EXISTS_SQL = """
SELECT 1 FROM links
WHERE entity_type = 'person' AND entity_id = $1 AND url = $2 AND link_type_id = $3
"""

_INSERT_LINK_SQL = """
INSERT INTO links (id, entity_type, entity_id, url, link_type_id)
VALUES ($1, 'person', $2, $3, $4)
ON CONFLICT (entity_type, entity_id, url, link_type_id) DO NOTHING
"""

_DELETE_IDENTIFIER_SQL = "DELETE FROM identifiers WHERE id = $1"


async def _end_state_present(
    conn: asyncpg.Connection,
    m: AccesshubRetype,
    agent_type_id: str,
    link_type_id: str,
) -> bool:
    """True when a previous run already produced this person's end-state."""
    if m["treatment"] == "retype":
        present = {
            r["value"] for r in await conn.fetch(_AGENT_VALUES_SQL, agent_type_id, m["person_id"])
        }
        return set(m["agent_ids"]) <= present
    return bool(await conn.fetchrow(_LINK_EXISTS_SQL, m["person_id"], m["url"], link_type_id))


async def migrate_accesshub_identifiers(
    conn: asyncpg.Connection, *, execute: bool
) -> list[AccesshubAction]:
    """Retype accesshub URL-form person_wa_pdc values per the #295 crosswalk.

    Returns one ``AccesshubAction`` per person in RETYPES, whose ``status`` is
    one of ``applied`` (retyped), ``planned`` (dry run, would retype),
    ``exists`` (already done), ``deferred`` (crosswalk says hands off),
    ``conflict`` (unexpected value — skipped), ``missing`` (no row and no
    prior end-state — skipped), ``ambiguous`` (multiple person_wa_pdc rows —
    skipped), or ``collision`` (a target agent_id already on a different
    person — skipped; merge the people first). Only ``applied`` mutates: the
    verified agent_ids are inserted (those not already present), the raw URL
    is preserved as a ``wa_pdc`` link, and the URL row is deleted.
    """
    pdc_type_id = await conn.fetchval(_TYPE_ID_SQL, "person_wa_pdc")
    agent_type_id = await conn.fetchval(_TYPE_ID_SQL, "person_wa_pdc_lobbyist_agent")
    link_type_id = await conn.fetchval(_LINK_TYPE_ID_SQL)
    if pdc_type_id is None or agent_type_id is None or link_type_id is None:
        raise RuntimeError(
            "person_wa_pdc / person_wa_pdc_lobbyist_agent identifier type or wa_pdc "
            "link type not found — run apply_schema first"
        )

    actions: list[AccesshubAction] = []
    for m in RETYPES:
        action: AccesshubAction = {
            "name": m["name"],
            "person_id": m["person_id"],
            "treatment": m["treatment"],
            "status": "missing",
        }
        actions.append(action)

        if m["treatment"] == "deferred":
            logger.info("%s (%s): deferred — see #295 crosswalk", m["name"], m["person_id"])
            action["status"] = "deferred"
            continue

        rows = await conn.fetch(_CURRENT_ROWS_SQL, pdc_type_id, m["person_id"])
        if not rows:
            if await _end_state_present(conn, m, agent_type_id, link_type_id):
                action["status"] = "exists"
            else:
                logger.warning(
                    "%s (%s): no person_wa_pdc identifier — skipping",
                    m["name"],
                    m["person_id"],
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
        if row["value"] != m["url"]:
            logger.warning(
                "%s (%s): value %r != audited URL %r — skipping",
                m["name"],
                m["person_id"],
                row["value"],
                m["url"],
            )
            action["status"] = "conflict"
            continue

        collided = False
        for agent_id in m["agent_ids"]:
            holders = await conn.fetch(
                _VALUE_ON_OTHER_PERSON_SQL, agent_type_id, agent_id, m["person_id"]
            )
            if holders:
                logger.warning(
                    "%s (%s): agent_id=%s already on person %s — duplicate pair, "
                    "merge first; skipping",
                    m["name"],
                    m["person_id"],
                    agent_id,
                    holders[0]["entity_id"],
                )
                collided = True
        if collided:
            action["status"] = "collision"
            continue

        if not execute:
            logger.info(
                "Would mint agent_ids=%r, link %s, and delete the URL row for %s (%s)",
                list(m["agent_ids"]),
                m["url"],
                m["name"],
                m["person_id"],
            )
            action["status"] = "planned"
            continue

        existing_agents = {
            r["value"] for r in await conn.fetch(_AGENT_VALUES_SQL, agent_type_id, m["person_id"])
        }
        for agent_id in m["agent_ids"]:
            if agent_id not in existing_agents:
                await conn.execute(
                    _INSERT_IDENTIFIER_SQL,
                    generate_id(),
                    m["person_id"],
                    agent_type_id,
                    agent_id,
                )
        await conn.execute(_INSERT_LINK_SQL, generate_id(), m["person_id"], m["url"], link_type_id)
        await conn.execute(_DELETE_IDENTIFIER_SQL, row["id"])
        logger.info(
            "Minted agent_ids=%r, linked %s, deleted the URL row for %s (%s)",
            list(m["agent_ids"]),
            m["url"],
            m["name"],
            m["person_id"],
        )
        action["status"] = "applied"

    return actions


async def run(*, execute: bool) -> None:
    """Connect to DATABASE_URL and retype the legacy identifiers."""
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        raise RuntimeError("DATABASE_URL not set")

    conn = await asyncpg.connect(dsn)
    try:
        if execute:
            async with conn.transaction():
                actions = await migrate_accesshub_identifiers(conn, execute=True)
        else:
            actions = await migrate_accesshub_identifiers(conn, execute=False)

        # Surface every outcome, not just applied — a conflict/missing person
        # shows up here so a partial run is never mistaken for "all done".
        counts = Counter(a["status"] for a in actions)
        breakdown = ", ".join(f"{status}={n}" for status, n in sorted(counts.items()))
        if not execute:
            logger.info(
                "Dry run — %d identifier(s) would be retyped (%s); pass --execute",
                counts["planned"],
                breakdown,
            )
        else:
            logger.info("Retyped %d person_wa_pdc identifier(s) (%s)", counts["applied"], breakdown)
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
