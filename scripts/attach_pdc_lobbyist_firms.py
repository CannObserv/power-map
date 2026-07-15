"""Attach verified PDC lobbyist-firm keys at org grain (#296).

The #295 audit established that the legacy accesshub node IDs were
lobbyist-**firm** keys: in most cases the node equals the firm's ``filer_id``
in PDC's Lobbyist Agents SODA dataset (``bp5b-jrti``). #295 resolved the
person side (``person_wa_pdc_lobbyist_agent`` = ``agent_id``) and preserved the
firm keys only as raw ``wa_pdc`` links on the people. This script promotes the
verified firm keys to org grain:

1. Find-or-create the firm Organization and attach ``org_wa_pdc`` = ``filer_id``.
2. Add a person->firm affiliation: a plain "Lobbyist" role at the firm (shared
   by every agent of that firm) plus a ``role_assignment`` bounded by the firm's
   ``bp5b-jrti`` employment years (``min(year)-01-01`` .. ``max(year)-12-31``).

Scope is Tier A — the 21 distinct-*named* firms. Self-named solo registrations
(firm name == person name, e.g. "Nancy Sapiro", "Neil Beaver") are deliberately
excluded: a one-person org whose name is the person duplicates the human as both
a person and an org node. Their ``filer_id`` stays as person-level provenance
(the #295 ``wa_pdc`` link); uniform org modeling, if wanted, is a separate pass.

Employment years and every ``agent_id`` -> name mapping were verified against a
live ``bp5b-jrti`` pull (2026-07-15); the crosswalk below is the reviewed table.
Strategies 360's Jack Goldberg is a pre-2016 lobbyist absent from the dataset —
the firm org is created but no datable affiliation is minted for him.

Safety — the same skip-on-anything-unexpected discipline as #293/#295:

- **Org**: matched by ``org_wa_pdc = filer_id`` (numeric) or its legacy accesshub
  node URL — so a firm already in PM under a punctuation/abbreviation name variant
  is reused, never duplicated. A same-named org carrying **no** org_wa_pdc is
  ``adopted`` (the key is stamped on it). A same-named org that already carries a
  different org_wa_pdc is a ``name_conflict`` — skipped for manual review. Only
  when nothing matches is a fresh org ``created``. Run
  ``scripts.retype_org_wa_pdc_identifiers`` first so keys are numeric.
- **Affiliation**: minted only for a person who exists (not archived) AND already
  carries the firm's ``person_wa_pdc_lobbyist_agent`` identifier from #295 — this
  ties every affiliation back to the verified crosswalk and catches person drift.
  A person missing the identifier is reported ``agent_missing`` and skipped; the
  org is still created.

Idempotent: a second run reports ``exists`` for orgs/affiliations already present.

Usage:
    uv run python -m scripts.attach_pdc_lobbyist_firms            # dry run
    uv run python -m scripts.attach_pdc_lobbyist_firms --execute  # commit
"""

import argparse
import asyncio
import os
from collections import Counter
from datetime import date
from typing import Literal, TypedDict

import asyncpg

from src.core.db import generate_id
from src.core.logging import configure_logging, get_logger
from src.core.observation import Disposition, resolve_role

logger = get_logger(__name__)

LOBBYIST_ROLE_TITLE = "Lobbyist"


class FirmMember(TypedDict):
    """One PM person affiliated to a firm, with the firm's employment window."""

    name: str
    person_id: str  # PM person ULID (from the #295 crosswalk)
    agent_ids: tuple[str, ...]  # verified agent_ids AT THIS firm; () = org-only
    year_min: int | None  # min bp5b-jrti employment_year at the firm
    year_max: int | None  # max bp5b-jrti employment_year at the firm


class LobbyistFirm(TypedDict):
    """One Tier-A firm: the org to find-or-create plus its affiliated people."""

    filer_id: str  # PDC firm filer_id -> org_wa_pdc value
    name: str  # human-curated canonical org name (from #296)
    members: tuple[FirmMember, ...]


# The #296 Tier-A crosswalk. Names/years verified against a live bp5b-jrti pull
# (2026-07-15); firm names are the human-curated forms from the issue table.
FIRMS: tuple[LobbyistFirm, ...] = (
    {
        "filer_id": "17348",
        "name": "Christophersen Inc",
        "members": (
            {
                "name": "Vicki Christophersen",
                "person_id": "01KV6PR0H8TEKREAK1ZS82DT0C",
                "agent_ids": ("33",),
                "year_min": 2016,
                "year_max": 2026,
            },
        ),
    },
    {
        "filer_id": "17398",
        "name": "Boswell Consulting",
        "members": (
            {
                "name": "Brad Boswell",
                "person_id": "01KV6PQMD3M2GQMBH9QBG7NMYP",
                "agent_ids": ("110",),
                "year_min": 2016,
                "year_max": 2026,
            },
        ),
    },
    {
        "filer_id": "17496",
        "name": "Doty & Assoc Inc",
        "members": (
            {
                "name": "J. Dylan Doty",
                "person_id": "01KV6PQQHGTVCNSTV8NHWNAJNB",
                "agent_ids": ("266",),
                "year_min": 2016,
                "year_max": 2026,
            },
        ),
    },
    {
        "filer_id": "17581",
        "name": "Gordon Thomas Honeywell Gov Affairs",
        "members": (
            {
                "name": "Briahna Murray",
                "person_id": "01KV6PQMH78YMSCWE7EFPNMJ5D",
                "agent_ids": ("387",),
                "year_min": 2016,
                "year_max": 2026,
            },
            {
                "name": "Diana Carlen",
                "person_id": "01KV6PQP9Y1Z5BS10J5QEBG5KG",
                "agent_ids": ("390",),
                "year_min": 2016,
                "year_max": 2026,
            },
        ),
    },
    {
        "filer_id": "17589",
        "name": "Government Relations Services",
        "members": (
            {
                # Same human as Doty & Assoc (agent 266); agent 429 is his row
                # at this firm (2016-2021).
                "name": "J. Dylan Doty",
                "person_id": "01KV6PQQHGTVCNSTV8NHWNAJNB",
                "agent_ids": ("429",),
                "year_min": 2016,
                "year_max": 2021,
            },
        ),
    },
    {
        "filer_id": "17659",
        "name": "HPC Advocacy",
        "members": (
            {
                "name": "Holly Chisa",
                "person_id": "01KV6PQQE2G0XK95TGG9QZMG3A",
                "agent_ids": ("520",),
                "year_min": 2016,
                "year_max": 2026,
            },
        ),
    },
    {
        "filer_id": "17848",
        "name": "The Nexus Group LLC",
        "members": (
            {
                "name": "Fred Yancey",
                "person_id": "01KV6PQQ2JC2TP03XPGSRZV7Q8",
                "agent_ids": ("793", "794", "795"),
                "year_min": 2016,
                "year_max": 2026,
            },
        ),
    },
    {
        "filer_id": "17870",
        "name": "Outcomes by Levy LLC",
        "members": (
            {
                "name": "Doug Levy",
                "person_id": "01KV6PQPERGMYPZ8E75ERAGEX5",
                "agent_ids": ("824", "825"),
                "year_min": 2016,
                "year_max": 2026,
            },
        ),
    },
    {
        "filer_id": "17872",
        "name": "Oyster Bay Public Affairs",
        "members": (
            {
                "name": "Amy Brackenbury",
                "person_id": "01KV6PQKFD3ZD16NDFASV8XF5S",
                "agent_ids": ("828", "829"),
                "year_min": 2016,
                "year_max": 2026,
            },
        ),
    },
    {
        "filer_id": "17873",
        "name": "Pacific NW Regional Strategies LLC",
        "members": (
            {
                "name": "Joshua Estes",
                "person_id": "01KV6PQS0HAZGAMZFGZ9KB1TPD",
                "agent_ids": ("830", "831"),
                "year_min": 2016,
                "year_max": 2026,
            },
            {
                "name": "Sean O'Sullivan",
                "person_id": "01KV6PQYFYW9PB69SDBY2D61JQ",
                "agent_ids": ("832", "833", "834"),
                "year_min": 2016,
                "year_max": 2026,
            },
        ),
    },
    {
        "filer_id": "17897",
        "name": "Potts & Assoc",
        "members": (
            {
                "name": "James W. Potts",
                "person_id": "01KV6PQQNP5JXQASG2YJBQ6K7S",
                "agent_ids": ("867", "868"),
                "year_min": 2016,
                "year_max": 2023,
            },
        ),
    },
    {
        "filer_id": "17905",
        "name": "T K Bentler/Public Affairs Assoc",
        "members": (
            {
                "name": "T.K. Bentler",
                "person_id": "01KV6PQZJZ8NEBSFJ5G72AF5G8",
                "agent_ids": ("884", "885"),
                "year_min": 2016,
                "year_max": 2025,
            },
        ),
    },
    {
        "filer_id": "17996",
        "name": "Strategies 360",
        "members": (
            {
                # Pre-2016 lobbyist absent from bp5b-jrti (link_only in #295):
                # the org is created, no datable affiliation is minted.
                "name": "Jack Goldberg",
                "person_id": "01KV6PQQHQGFBFKF8P9NV1AYR8",
                "agent_ids": (),
                "year_min": None,
                "year_max": None,
            },
        ),
    },
    {
        "filer_id": "18028",
        "name": "Thompson Consulting Group",
        "members": (
            {
                "name": "Tim Thompson",
                "person_id": "01KV6PR02ES2Q2HTKD4SCJ1DJW",
                "agent_ids": ("1085", "1086"),
                "year_min": 2016,
                "year_max": 2023,
            },
        ),
    },
    {
        "filer_id": "26001",
        "name": "BMcConsulting",
        "members": (
            {
                "name": "Bryan McConaughy",
                "person_id": "01KV6PQMPNQADP43HC0VBFKV2W",
                "agent_ids": ("1242", "1243"),
                "year_min": 2016,
                "year_max": 2026,
            },
        ),
    },
    {
        "filer_id": "27257",
        "name": "Paribello Public Affairs",
        "members": (
            {
                "name": "James Paribello",
                "person_id": "01KV6PQQNDAK8NY7ZEQN32XK88",
                "agent_ids": ("1252", "1253"),
                "year_min": 2016,
                "year_max": 2026,
            },
        ),
    },
    {
        "filer_id": "38463",
        "name": "Cadena Consulting",
        "members": (
            {
                "name": "Lyset Cadena",
                "person_id": "01KV6PQTGJ5F7SC1ZED9J444P2",
                "agent_ids": ("1561",),
                "year_min": 2017,
                "year_max": 2026,
            },
        ),
    },
    {
        "filer_id": "47356",
        "name": "Streuli Public Affairs",
        "members": (
            {
                # Agent 1744 is his row at THIS firm; his other agent_ids
                # (1044, 1376) are at other firms, out of Tier A.
                "name": "Mark Streuli",
                "person_id": "01KV6PQTSDNA8G533BE1AY1N2C",
                "agent_ids": ("1744",),
                "year_min": 2018,
                "year_max": 2026,
            },
        ),
    },
    {
        "filer_id": "48956",
        "name": "B.E. Davies Consulting",
        "members": (
            {
                "name": "Brooke Davies",
                "person_id": "01KV6PQMMFYWXW3HDX2YJ7A2X6",
                "agent_ids": ("1788",),
                "year_min": 2018,
                "year_max": 2026,
            },
        ),
    },
    {
        "filer_id": "62867",
        "name": "Sunrise Consulting Group",
        "members": (
            {
                "name": "Ezra Eickmeyer",
                "person_id": "01KV6PQQ1E32B54VZCF313GFKF",
                "agent_ids": ("2208", "2209"),
                "year_min": 2020,
                "year_max": 2026,
            },
        ),
    },
    {
        "filer_id": "67155",
        "name": "FMS Global Strategies",
        "members": (
            {
                "name": "Albert Sardinas",
                "person_id": "01KV6PQK8VJDYT67MJ9JET6FDF",
                "agent_ids": ("2250",),
                "year_min": 2021,
                "year_max": 2024,
            },
            {
                "name": "Philip Singleton",
                "person_id": "01KV6PQWV9H6QSRC2KEKJ2RPQX",
                "agent_ids": ("2261",),
                "year_min": 2021,
                "year_max": 2021,
            },
        ),
    },
)


OrgStatus = Literal["created", "adopted", "exists", "planned", "name_conflict"]
MemberStatus = Literal[
    "applied",
    "planned",
    "exists",
    "no_agent",
    "person_missing",
    "agent_missing",
    "skipped_org",
]


class MemberAction(TypedDict):
    """Per-person affiliation outcome within a firm."""

    name: str
    person_id: str
    status: MemberStatus


class FirmAction(TypedDict):
    """Per-firm outcome: the org disposition plus each member's affiliation."""

    filer_id: str
    name: str
    org_status: OrgStatus
    org_id: str | None
    members: list[MemberAction]


_ORG_TYPE_ID_SQL = "SELECT id FROM entity_identifier_types WHERE slug = 'org_wa_pdc'"
_AGENT_TYPE_ID_SQL = (
    "SELECT id FROM entity_identifier_types WHERE slug = 'person_wa_pdc_lobbyist_agent'"
)

_ORG_BY_KEY_SQL = """
SELECT entity_id FROM identifiers
WHERE entity_identifier_type_id = $1 AND value = $2
"""

_ORG_BY_NAME_SQL = """
SELECT o.id
FROM organizations o
JOIN organization_names n ON n.organization_id = o.id AND n.is_canonical = TRUE
WHERE o.archived_at IS NULL AND lower(n.name) = lower($1)
"""

_ORG_HAS_ANY_KEY_SQL = """
SELECT 1 FROM identifiers
WHERE entity_identifier_type_id = $1 AND entity_id = $2
LIMIT 1
"""

# The firm key may still be in the legacy accesshub node-URL form if the
# org-key retype (scripts/retype_org_wa_pdc_identifiers) has not run yet; for the
# Tier-A firms node == filer_id, so this catches those orgs regardless of order.
_LEGACY_NODE_URL = "https://accesshub.pdc.wa.gov/node/{filer_id}"

_PERSON_ALIVE_SQL = "SELECT 1 FROM people WHERE id = $1 AND archived_at IS NULL"

_PERSON_HAS_AGENT_SQL = """
SELECT 1 FROM identifiers
WHERE entity_identifier_type_id = $1 AND entity_id = $2 AND value = ANY($3::text[])
LIMIT 1
"""

_ROLE_BY_TITLE_SQL = """
SELECT id FROM roles
WHERE organization_id = $1 AND lower(title) = lower($2)
  AND jurisdiction_id IS NULL AND archived_at IS NULL
"""

_ASSIGNMENT_EXISTS_SQL = """
SELECT 1 FROM role_assignments
WHERE person_id = $1 AND role_id = $2 AND archived_at IS NULL
LIMIT 1
"""

_INSERT_ORG_SQL = "INSERT INTO organizations (id) VALUES ($1)"

_INSERT_ORG_NAME_SQL = """
INSERT INTO organization_names (id, organization_id, name, name_type, is_canonical)
VALUES ($1, $2, $3, 'legal', TRUE)
"""

_INSERT_ORG_KEY_SQL = """
INSERT INTO identifiers (id, entity_id, entity_identifier_type_id, value)
VALUES ($1, $2, $3, $4)
"""

_INSERT_ASSIGNMENT_SQL = """
INSERT INTO role_assignments (id, person_id, role_id, is_current, start_date, end_date, notes)
VALUES ($1, $2, $3, FALSE, $4, $5, $6)
"""


async def _resolve_org(
    conn: asyncpg.Connection, firm: LobbyistFirm, org_type_id: str, *, execute: bool
) -> tuple[OrgStatus, str | None]:
    """Find-or-create the firm org.

    Matched (``exists``) by ``org_wa_pdc`` = ``filer_id`` numeric key, or its
    legacy accesshub node URL (so variant-named firms already in PM are reused,
    never duplicated). Otherwise a same-named org **with no org_wa_pdc at all**
    is ``adopted`` — the numeric key is stamped onto it (high-confidence exact
    name match on a distinctive firm name). A same-named org that already carries
    some org_wa_pdc key is a real ``name_conflict`` — skipped. Only when nothing
    matches is a fresh org ``created``.
    """
    org_id = await conn.fetchval(_ORG_BY_KEY_SQL, org_type_id, firm["filer_id"])
    if org_id is not None:
        return "exists", org_id
    legacy = await conn.fetchval(
        _ORG_BY_KEY_SQL, org_type_id, _LEGACY_NODE_URL.format(filer_id=firm["filer_id"])
    )
    if legacy is not None:
        return "exists", legacy

    name_match = await conn.fetchval(_ORG_BY_NAME_SQL, firm["name"])
    if name_match is not None:
        if await conn.fetchval(_ORG_HAS_ANY_KEY_SQL, org_type_id, name_match):
            logger.warning(
                "%s (filer_id=%s): org %s named %r already carries a different org_wa_pdc — "
                "name_conflict, skipping (resolve manually)",
                firm["name"],
                firm["filer_id"],
                name_match,
                firm["name"],
            )
            return "name_conflict", None
        if not execute:
            return "planned", name_match
        await conn.execute(
            _INSERT_ORG_KEY_SQL, generate_id(), name_match, org_type_id, firm["filer_id"]
        )
        logger.info(
            "Adopted org %s %r — stamped org_wa_pdc=%s", name_match, firm["name"], firm["filer_id"]
        )
        return "adopted", name_match

    if not execute:
        return "planned", None

    org_id = generate_id()
    await conn.execute(_INSERT_ORG_SQL, org_id)
    await conn.execute(_INSERT_ORG_NAME_SQL, generate_id(), org_id, firm["name"])
    await conn.execute(_INSERT_ORG_KEY_SQL, generate_id(), org_id, org_type_id, firm["filer_id"])
    logger.info("Created org %s %r with org_wa_pdc=%s", org_id, firm["name"], firm["filer_id"])
    return "created", org_id


async def _affiliate_member(
    conn: asyncpg.Connection,
    firm: LobbyistFirm,
    member: FirmMember,
    org_status: OrgStatus,
    org_id: str | None,
    agent_type_id: str,
    *,
    execute: bool,
) -> MemberStatus:
    """Add (or plan/verify) one person->firm affiliation."""
    if org_status == "name_conflict":
        return "skipped_org"
    if not await conn.fetchval(_PERSON_ALIVE_SQL, member["person_id"]):
        logger.warning(
            "%s (%s): no active person row — skipping affiliation",
            member["name"],
            member["person_id"],
        )
        return "person_missing"
    if not member["agent_ids"]:
        return "no_agent"
    has_agent = await conn.fetchval(
        _PERSON_HAS_AGENT_SQL, agent_type_id, member["person_id"], list(member["agent_ids"])
    )
    if not has_agent:
        logger.warning(
            "%s (%s): missing person_wa_pdc_lobbyist_agent %r (run #295 first?) — skipping",
            member["name"],
            member["person_id"],
            list(member["agent_ids"]),
        )
        return "agent_missing"

    # Dry run, or the org would-be-created this run (org_id still None): report
    # intent without touching the DB.
    if not execute or org_id is None:
        if org_id is not None:
            role_id = await conn.fetchval(_ROLE_BY_TITLE_SQL, org_id, LOBBYIST_ROLE_TITLE)
            if role_id is not None and await conn.fetchval(
                _ASSIGNMENT_EXISTS_SQL, member["person_id"], role_id
            ):
                return "exists"
        return "planned"

    role_id, disposition, reason = await resolve_role(conn, org_id, LOBBYIST_ROLE_TITLE)
    if disposition is Disposition.REJECTED:
        # org was just created/confirmed alive, so this should not happen.
        logger.error("resolve_role rejected for %s at %s: %s", member["name"], org_id, reason)
        return "person_missing"

    if await conn.fetchval(_ASSIGNMENT_EXISTS_SQL, member["person_id"], role_id):
        return "exists"

    notes = (
        f"WA PDC lobbyist agent {', '.join(member['agent_ids'])} at firm filer_id "
        f"{firm['filer_id']}; employment years {member['year_min']}-{member['year_max']} "
        "(bp5b-jrti, year-granular). #296"
    )
    await conn.execute(
        _INSERT_ASSIGNMENT_SQL,
        generate_id(),
        member["person_id"],
        role_id,
        date(member["year_min"], 1, 1),
        date(member["year_max"], 12, 31),
        notes,
    )
    logger.info(
        "Affiliated %s (%s) to %r (%s-%s)",
        member["name"],
        member["person_id"],
        firm["name"],
        member["year_min"],
        member["year_max"],
    )
    return "applied"


async def attach_lobbyist_firms(conn: asyncpg.Connection, *, execute: bool) -> list[FirmAction]:
    """Create firm orgs + person->firm affiliations per the #296 Tier-A crosswalk.

    Returns one ``FirmAction`` per firm. ``org_status`` is ``created`` /
    ``adopted`` (key stamped on a same-named keyless org) / ``planned`` /
    ``exists`` (matched by ``org_wa_pdc`` numeric or legacy node URL) /
    ``name_conflict`` (same-named org with a different key — skipped). Each
    member's ``status`` is
    ``applied`` / ``planned`` / ``exists`` / ``no_agent`` (org-only person) /
    ``person_missing`` / ``agent_missing`` (person lacks the #295 identifier) /
    ``skipped_org`` (firm org was a name_conflict). Only ``created`` /
    ``applied`` mutate.
    """
    org_type_id = await conn.fetchval(_ORG_TYPE_ID_SQL)
    agent_type_id = await conn.fetchval(_AGENT_TYPE_ID_SQL)
    if org_type_id is None or agent_type_id is None:
        raise RuntimeError(
            "org_wa_pdc / person_wa_pdc_lobbyist_agent identifier type not found — "
            "run apply_schema first"
        )

    actions: list[FirmAction] = []
    for firm in FIRMS:
        org_status, org_id = await _resolve_org(conn, firm, org_type_id, execute=execute)
        members: list[MemberAction] = []
        for member in firm["members"]:
            status = await _affiliate_member(
                conn, firm, member, org_status, org_id, agent_type_id, execute=execute
            )
            members.append(
                {"name": member["name"], "person_id": member["person_id"], "status": status}
            )
        actions.append(
            {
                "filer_id": firm["filer_id"],
                "name": firm["name"],
                "org_status": org_status,
                "org_id": org_id,
                "members": members,
            }
        )
    return actions


async def run(*, execute: bool) -> None:
    """Connect to DATABASE_URL and attach the lobbyist-firm keys."""
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        raise RuntimeError("DATABASE_URL not set")

    conn = await asyncpg.connect(dsn)
    try:
        if execute:
            async with conn.transaction():
                actions = await attach_lobbyist_firms(conn, execute=True)
        else:
            actions = await attach_lobbyist_firms(conn, execute=False)

        org_counts = Counter(a["org_status"] for a in actions)
        member_counts = Counter(m["status"] for a in actions for m in a["members"])
        org_breakdown = ", ".join(f"{s}={n}" for s, n in sorted(org_counts.items()))
        member_breakdown = ", ".join(f"{s}={n}" for s, n in sorted(member_counts.items()))
        if not execute:
            logger.info(
                "Dry run — orgs: %s | affiliations: %s; pass --execute",
                org_breakdown,
                member_breakdown,
            )
        else:
            logger.info("Done — orgs: %s | affiliations: %s", org_breakdown, member_breakdown)
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
