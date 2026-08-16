"""Seed the defunct WA historical party Organizations (#442).

usa-wa's roster backfill (CannObserv/usa-wa#219, phase #227) emits party spans
for an era naming parties that do not exist as Organizations today. #442 settled
the naming and lifecycle convention on the org-model owner; this script mints the
rows that convention describes.

**Five of seven.** ``Prog.`` (Progressive) and ``Cit.`` (Citizen) are deliberately
absent, pending CannObserv/usa-wa#233:

* ``Prog.`` — the label spans two nationally distinct parties (Roosevelt's
  1912 Bull Moose and La Follette's 1924 revival), so it may be *two* Orgs. A
  Power Map Org can be merged later but never split, so minting one now is the
  unrecoverable direction.
* ``Cit.`` — the 1907 Jefferson County members elected on the Citizen's Party
  ticket identified as a Republican and a Democrat once seated, which reframes it
  as a county ballot line rather than a state party.

Three #442 rulings are load-bearing here, and each has a test:

* **``active = false``, never archived.** The axes are orthogonal (#240) and an
  archived Org *rejects* subsequent ``active`` observations
  (``active_on_archived_org``), so archiving at birth would mint Orgs the
  producer cannot observe.
* **No ``dissolved`` / ``merged_with`` event.** With a year, either feeds
  ``v_org_lifespan.ended_on``, which gates ``role_assignment`` writes. Two live
  examples: a ``dissolved: 1916`` on Progressive would reject the Progressive
  senator seated in 1917; ``dissolved: 1924`` on Farmer-Labor would reject the
  Farm Laborite sitting in 1925. Every Org here is left with **no**
  ``v_org_lifespan`` row.
* **``founded`` only where the anchor is WA-scoped.** ``founded`` feeds no view
  and is always safe, but People's Party and Populist have only *national*
  founding dates. Asserting a national founding on an Org named "Washington
  State …" is the same scope error #442 rejected for the Silver Republicans'
  national 1901 dissolution, so those two get notes instead of an event.

Orgs are keyed by the existing ``org_wa_party`` identifier (#270) — a bare
lowercase party slug, no ``wa-`` prefix, because the identifier *type* already
scopes to WA. That is what lets a producer attach a party by stable key instead
of by name-match, and it is also what ``migrate_member_role_type`` reads to
classify a role as ``party_member``. An Org already carrying the value is
**adopted, never re-created and never modified**.

Idempotent: re-running reports every row as ``exists`` and writes nothing.

A party whose ``org_wa_party`` value resolves to **no live Org despite identifier
rows existing** (the Org was hard deleted — ``org_delete`` leaves identifiers
behind, and nothing reaps them) or to **more than one** live Org is reported
``blocked`` at WARNING and skipped, with a summary line naming every blocked
party. The remaining parties still seed. Silence is the one outcome that is never
acceptable here: the whole reason #442 exists is that 167 attested records were
being dropped without an error.

Usage:
    uv run python -m scripts.seed_442_historical_parties            # dry run
    uv run python -m scripts.seed_442_historical_parties --execute  # commit
"""

import argparse
import asyncio
import datetime
from dataclasses import dataclass, field
from typing import Literal, TypedDict

import asyncpg

from scripts._dsn import add_dsn_args, resolve_dsn
from src.core.db import generate_id
from src.core.logging import configure_logging, get_logger

logger = get_logger(__name__)

# Every link below is a Wikipedia article; the roster and the legislative history
# are attached as citations instead, where their revision/publication belongs.
LINK_TYPE_SLUG = "wikipedia"

PARTY_IDENTIFIER_SLUG = "org_wa_party"

# The source usa-wa parses. Revision date is part of the citation because the
# document is revised roughly every 12-24 months.
ROSTER_CITATION = {
    "title": "Members of the Legislature 1889-2025 (rev. 2025-06-05)",
    "url": "https://leg.wa.gov/media/s4gf4suc/members-of-the-legislature-1889-2025.pdf",
}

# The independent WA-specific cross-check found while researching #442. It gives
# per-session party composition by chamber, which is what surfaced the chamber
# discrepancies now tracked in CannObserv/usa-wa#233.
BRAZIER_CITATION = {
    "title": "Don Brazier, History of the Washington Legislature 1854-1963 (WA State Senate, 2000)",
    "url": "https://leg.wa.gov/media/taqpwinb/history-of-the-legislature-1854-1963.pdf",
}

CITATIONS = (ROSTER_CITATION, BRAZIER_CITATION)

# When these sources were read for #442. Recorded on every citation because the
# roster is revised roughly every 12-24 months: without it there is no way to
# tell a current citation from one that predates a revision. Fixed rather than
# ``now()`` so a re-run does not restate a claim nobody re-checked.
SOURCES_ACCESSED_AT = datetime.datetime(2026, 8, 14, tzinfo=datetime.UTC)

# "blocked" is a party the seed refuses to guess at: its org_wa_party value
# resolves to no live Org (a dangling identifier) or to more than one. Reported
# and counted, never silently skipped.
SeedStatus = Literal["created", "planned", "exists", "blocked"]


class SeedAction(TypedDict):
    """One outcome per party (see module docstring)."""

    party_value: str
    name: str
    org_id: str | None
    status: SeedStatus


@dataclass(frozen=True)
class HistoricalParty:
    """One defunct party Org and everything the seed writes for it.

    ``founded_year`` is None when the only available founding date is national —
    see the module docstring. ``acronym`` is the source file's own token, which
    #442 adopted as the canonical acronym, so the admin displays "Name (Token)".
    """

    party_value: str
    name: str
    acronym: str
    notes: str
    founded_year: int | None = None
    founded_month: int | None = None
    links: tuple[str, ...] = field(default_factory=tuple)


_WIKI_PEOPLES = "https://en.wikipedia.org/wiki/People%27s_Party_(United_States)"

PARTIES: tuple[HistoricalParty, ...] = (
    HistoricalParty(
        party_value="peoples",
        name="Washington State People's Party",
        acronym="P.P.",
        notes=(
            "Defunct. Party label as recorded in Members of the Legislature 1889-2025 "
            "(rev. 2025-06-05), source token 'P.P.', 1891-1899. Minted per power-map#442. "
            "Held separate from the Populist Org because the roster's own 1897 Senate "
            "division table lists 'People's Party' and 'Populist' as distinct lines; "
            "Brazier's History of the Washington Legislature declines that distinction "
            "and treats them as one movement. Kept separate because two Orgs can be "
            "merged later but one can never be split. No founded event: the only "
            "available dates are national (Cincinnati, 1891-05-19; Omaha platform, "
            "1892-07-04), and asserting those on a Washington State Org would overstate "
            "their scope."
        ),
        links=(_WIKI_PEOPLES,),
    ),
    HistoricalParty(
        party_value="populist",
        name="Washington State Populist Party",
        acronym="Pop.",
        notes=(
            "Defunct. Party label as recorded in Members of the Legislature 1889-2025 "
            "(rev. 2025-06-05), source token 'Pop.', 1897. Minted per power-map#442. "
            "See the People's Party Org for the shared-identity question: the roster "
            "separates them, Brazier does not. No founded event — national dates only "
            "(see People's Party notes)."
        ),
        links=(_WIKI_PEOPLES,),
    ),
    HistoricalParty(
        party_value="silver-republican",
        name="Washington State Silver Republican Party",
        acronym="Silver Rep.",
        notes=(
            "Defunct. Party label as recorded in Members of the Legislature 1889-2025 "
            "(rev. 2025-06-05), source token 'Silver Rep.', 1897. Minted per "
            "power-map#442. Founded event anchored on the WA organisation: pro-silver "
            "Republicans who left the party over bimetallism held their own nominating "
            "convention at Ellensburg in early August 1896, one of the three that "
            "produced the Fusion slate. The national party dissolved in 1901 (its "
            "members in Congress urged union with the Democrats that March) and was "
            "later known as the Lincoln Republican Party; no dissolved event is "
            "recorded, because that is the national body's date and because a lifespan "
            "bound would gate assignment writes (power-map#442)."
        ),
        founded_year=1896,
        links=("https://en.wikipedia.org/wiki/Silver_Republican_Party",),
    ),
    HistoricalParty(
        party_value="farmer-labor",
        name="Washington State Farmer-Labor Party",
        acronym="F.L.",
        notes=(
            "Defunct. Party label as recorded in Members of the Legislature 1889-2025 "
            "(rev. 2025-06-05), source token 'F.L.', 1921-1923 per the roster. Minted "
            "per power-map#442. Grew out of the Triple Alliance, founded June 1919 by "
            "the Washington State Grange, the Washington State Federation of Labor and "
            "the Railroad Brotherhoods. Brazier also writes it 'Farm Labor party' and "
            "records a Farm Laborite still sitting in the 1925 Senate, past the "
            "roster's stated range — one reason no dissolved event is recorded "
            "(a 1924 bound would reject that member's assignment)."
        ),
        founded_year=1920,
        links=("https://en.wikipedia.org/wiki/Farmer%E2%80%93Labor_Party",),
    ),
    HistoricalParty(
        party_value="socialist",
        name="Socialist Party of Washington",
        acronym="S",
        notes=(
            "Defunct. Party label as recorded in Members of the Legislature 1889-2025 "
            "(rev. 2025-06-05), source token 'S', 1913. Minted per power-map#442. Named "
            "for the actual organisation rather than the generated 'Washington State ...' "
            "pattern, because this one has an attested name: the state section of the "
            "Socialist Party of America, chartered September 1901. Declined through the "
            "1920s, losing most members to the Farmer-Labor Party around 1920 and "
            "failing to name a ticket in 1920 and 1922; no dissolved event, as no "
            "dissolution date is sourced. Note the roster places its single 1913 member "
            "in the Senate while Brazier places the lone 1913 Socialist in the House "
            "(the Senate's third-party member that session was an Independent) — "
            "tracked in CannObserv/usa-wa#233."
        ),
        founded_year=1901,
        founded_month=9,
        links=("https://en.wikipedia.org/wiki/Socialist_Party_of_Washington",),
    ),
)


# The JOIN onto organizations is load-bearing, not decorative. ``identifiers``
# has no FK to ``organizations`` (it is polymorphic — the entity type lives on
# ``entity_identifier_types``), ``idx_identifiers_lookup`` is a plain index rather
# than a unique one, and ``org_delete`` deletes an Org's names and acronyms but
# leaves its identifiers behind. ``audit_ancillary_orphans`` sweeps only the
# ``role`` / ``role_assignment`` scopes, so nothing reaps a dangling org
# identifier. Selecting ``i.entity_id`` alone would therefore report a
# hard-deleted party as "already present" and skip it in silence.
_FIND_BY_PARTY_VALUE_SQL = """
SELECT o.id
FROM identifiers i
JOIN entity_identifier_types t ON t.id = i.entity_identifier_type_id
JOIN organizations o ON o.id = i.entity_id
WHERE t.slug = $1 AND i.value = $2
"""

# Same reason: without the unique index, two Orgs can carry one party value, and
# a bare fetchval would pick an arbitrary one with no ORDER BY.
_COUNT_IDENTIFIER_ROWS_SQL = """
SELECT count(*)
FROM identifiers i
JOIN entity_identifier_types t ON t.id = i.entity_identifier_type_id
WHERE t.slug = $1 AND i.value = $2
"""

VocabularyTable = Literal["entity_identifier_types", "link_types", "entity_event_types"]


async def _vocabulary_id(conn: asyncpg.Connection, table: VocabularyTable, slug: str) -> str:
    """Resolve a seeded vocabulary row, failing loudly rather than writing an orphan.

    ``table`` is a ``Literal`` rather than a free string: the name is interpolated
    into SQL (asyncpg cannot parameterise an identifier), so the type is what keeps
    a future caller from passing something attacker-shaped.
    """
    row_id = await conn.fetchval(f"SELECT id FROM {table} WHERE slug = $1", slug)
    if row_id is None:
        raise RuntimeError(f"{table}.{slug} not found — run scripts/apply-schema.sh first")
    return row_id


async def _create_party(
    conn: asyncpg.Connection,
    party: HistoricalParty,
    *,
    identifier_type_id: str,
    link_type_id: str,
    founded_type_id: str,
) -> str:
    """Insert the Org and everything hanging off it. Returns the new Org id."""
    org_id = generate_id()

    # active=FALSE is the whole point: defunct is a domain fact, not an archive
    # state. archived_at stays NULL.
    await conn.execute(
        "INSERT INTO organizations (id, active, notes) VALUES ($1, FALSE, $2)",
        org_id,
        party.notes,
    )

    await conn.execute(
        """INSERT INTO organization_names (id, organization_id, name, name_type, is_canonical)
           VALUES ($1, $2, $3, 'legal', TRUE)""",
        generate_id(),
        org_id,
        party.name,
    )

    await conn.execute(
        """INSERT INTO organization_acronyms (id, organization_id, acronym, is_canonical)
           VALUES ($1, $2, $3, TRUE)""",
        generate_id(),
        org_id,
        party.acronym,
    )

    await conn.execute(
        """INSERT INTO identifiers (id, entity_identifier_type_id, entity_id, value)
           VALUES ($1, $2, $3, $4)""",
        generate_id(),
        identifier_type_id,
        org_id,
        party.party_value,
    )

    if party.founded_year is not None:
        await conn.execute(
            """INSERT INTO entity_events
                   (id, entity_type, entity_id, event_type_id, event_year, event_month)
               VALUES ($1, 'organization', $2, $3, $4, $5)""",
            generate_id(),
            org_id,
            founded_type_id,
            party.founded_year,
            party.founded_month,
        )

    # No ON CONFLICT on either loop below: org_id was generated moments ago, so
    # no conflict is reachable, and a bare DO NOTHING would additionally swallow
    # a genuine primary-key collision. Idempotency lives in the caller's
    # existence check, not here.
    for url in party.links:
        await conn.execute(
            """INSERT INTO links (id, entity_type, entity_id, url, link_type_id)
               VALUES ($1, 'organization', $2, $3, $4)""",
            generate_id(),
            org_id,
            url,
            link_type_id,
        )

    for citation in CITATIONS:
        await conn.execute(
            """INSERT INTO citations (id, entity_type, entity_id, url, title, accessed_at)
               VALUES ($1, 'organization', $2, $3, $4, $5)""",
            generate_id(),
            org_id,
            citation["url"],
            citation["title"],
            SOURCES_ACCESSED_AT,
        )

    return org_id


async def _resolve_existing(
    conn: asyncpg.Connection, party: HistoricalParty
) -> tuple[str | None, str | None]:
    """Resolve a party's live Org, or say why it cannot be resolved.

    Returns ``(org_id, block_reason)`` with exactly one side populated. A party
    is blocked when its ``org_wa_party`` value resolves to no live Org while
    identifier rows exist for it (``dangling_identifier`` — the Org was hard
    deleted and its identifier outlived it), or to more than one live Org
    (``ambiguous_identifier``). Both are conditions the seed refuses to guess at:
    creating a second Org alongside a dangling row, or adopting an arbitrary one
    of two, would each be a quiet wrong answer.
    """
    live = [
        r["id"]
        for r in await conn.fetch(
            _FIND_BY_PARTY_VALUE_SQL, PARTY_IDENTIFIER_SLUG, party.party_value
        )
    ]
    if len(live) > 1:
        return None, f"ambiguous_identifier ({len(live)} live Orgs: {', '.join(sorted(live))})"
    if live:
        return live[0], None

    orphaned = await conn.fetchval(
        _COUNT_IDENTIFIER_ROWS_SQL, PARTY_IDENTIFIER_SLUG, party.party_value
    )
    if orphaned:
        return None, f"dangling_identifier ({orphaned} row(s) point at no live Org)"
    return None, None


async def seed_parties(conn: asyncpg.Connection, execute: bool) -> list[SeedAction]:
    """Mint the five historical party Orgs. Returns one action per party.

    An Org already carrying the party's ``org_wa_party`` value is adopted and
    left **completely untouched** — the seed never edits a row it did not create,
    so a curated Org cannot be clobbered by a re-run.

    A party whose identifier space cannot be resolved is reported ``blocked`` and
    skipped; the remaining parties still seed, so one bad row does not hold up the
    other four. Blocked is sticky by design — a re-run reports it again rather
    than quietly creating a duplicate.
    """
    identifier_type_id = await _vocabulary_id(
        conn, "entity_identifier_types", PARTY_IDENTIFIER_SLUG
    )
    link_type_id = await _vocabulary_id(conn, "link_types", LINK_TYPE_SLUG)
    founded_type_id = await _vocabulary_id(conn, "entity_event_types", "founded")

    actions: list[SeedAction] = []
    for party in PARTIES:
        existing, block_reason = await _resolve_existing(conn, party)

        if block_reason is not None:
            logger.warning(
                "Skipping %r: %s=%r is %s. Resolve the identifier rows, then re-run.",
                party.name,
                PARTY_IDENTIFIER_SLUG,
                party.party_value,
                block_reason,
            )
            actions.append(
                {
                    "party_value": party.party_value,
                    "name": party.name,
                    "org_id": None,
                    "status": "blocked",
                }
            )
            continue

        if existing is not None:
            logger.info(
                "Org %s already carries %s=%r (%r) — leaving untouched",
                existing,
                PARTY_IDENTIFIER_SLUG,
                party.party_value,
                party.name,
            )
            actions.append(
                {
                    "party_value": party.party_value,
                    "name": party.name,
                    "org_id": existing,
                    "status": "exists",
                }
            )
            continue

        if not execute:
            logger.info(
                "Would create %r (%s=%r, acronym %r, active=FALSE)",
                party.name,
                PARTY_IDENTIFIER_SLUG,
                party.party_value,
                party.acronym,
            )
            actions.append(
                {
                    "party_value": party.party_value,
                    "name": party.name,
                    "org_id": None,
                    "status": "planned",
                }
            )
            continue

        org_id = await _create_party(
            conn,
            party,
            identifier_type_id=identifier_type_id,
            link_type_id=link_type_id,
            founded_type_id=founded_type_id,
        )
        logger.info(
            "Created %r as Org %s (%s=%r, active=FALSE)",
            party.name,
            org_id,
            PARTY_IDENTIFIER_SLUG,
            party.party_value,
        )
        actions.append(
            {
                "party_value": party.party_value,
                "name": party.name,
                "org_id": org_id,
                "status": "created",
            }
        )

    return actions


async def main(dsn: str, execute: bool) -> None:
    """Run the seed inside one transaction so a failure leaves no half-built Org."""
    conn = await asyncpg.connect(dsn)
    try:
        async with conn.transaction():
            actions = await seed_parties(conn, execute)
    finally:
        await conn.close()

    created = sum(1 for a in actions if a["status"] == "created")
    planned = sum(1 for a in actions if a["status"] == "planned")
    exists = sum(1 for a in actions if a["status"] == "exists")
    blocked = [a for a in actions if a["status"] == "blocked"]

    # Surfaced after the per-party lines and at WARNING, so a blocked party is
    # not something an operator has to scroll back through the log to notice.
    if blocked:
        logger.warning(
            "%d of %d part(ies) BLOCKED and not seeded: %s. See the warnings above "
            "for each one's identifier state.",
            len(blocked),
            len(PARTIES),
            ", ".join(a["party_value"] for a in blocked),
        )

    if execute:
        logger.info("Seeded %d party Org(s); %d already present", created, exists)
    else:
        logger.info(
            "Dry run: would seed %d party Org(s); %d already present. "
            "Re-run with --execute to commit.",
            planned,
            exists,
        )


if __name__ == "__main__":
    configure_logging()
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    add_dsn_args(parser)
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Commit the seed (default: dry run, reports what would be created)",
    )
    args = parser.parse_args()
    asyncio.run(main(resolve_dsn(args, parser), args.execute))
