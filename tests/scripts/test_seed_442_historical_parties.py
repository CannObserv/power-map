"""Tests for the #442 historical-party Organization seed.

The assertions that matter are the two #442 rulings that are easy to get wrong
and expensive to unwind:

* **``active = false``, never archived.** The axes are orthogonal (#240), and an
  archived Org *rejects* subsequent ``active`` observations
  (``active_on_archived_org``), so archiving at birth would mint five Orgs the
  producer cannot observe.
* **No lifespan-bounding event.** ``dissolved`` / ``merged_with`` with a year
  feed ``v_org_lifespan.ended_on``, which gates ``role_assignment`` writes. A
  dissolution year taken from a party's last legislative appearance would reject
  the very backfill these Orgs exist to enable, so the seed must leave every one
  of them with **no ``v_org_lifespan`` row at all**.

``founded`` is the safe counterpart — it feeds no view — and is written only for
the three parties with a WA-scoped anchor. People's Party and Populist have only
*national* founding dates, and asserting a national founding on an Org named
"Washington State …" is the same scope error #442 rejected for the Silver
Republicans' national 1901 dissolution.
"""

import pytest
import pytest_asyncio

from scripts import seed_442_historical_parties as seed
from src.core.db import generate_id

pytestmark = [pytest.mark.integration]

# The two parties whose only founding evidence is national, so they get no event.
NATIONAL_ONLY = {"peoples", "populist"}


@pytest_asyncio.fixture(loop_scope="session")
async def db(db_pool):
    async with db_pool.acquire() as conn:
        tr = conn.transaction()
        await tr.start()
        try:
            yield conn
        finally:
            await tr.rollback()


async def _org_id_for(db, party_value):
    return await db.fetchval(
        """SELECT i.entity_id
           FROM identifiers i
           JOIN entity_identifier_types t ON t.id = i.entity_identifier_type_id
           WHERE t.slug = 'org_wa_party' AND i.value = $1""",
        party_value,
    )


async def _seeded_org_ids(db):
    return {p.party_value: await _org_id_for(db, p.party_value) for p in seed.PARTIES}


# --------------------------------------------------------------------------
# Dry run
# --------------------------------------------------------------------------


async def test_dry_run_writes_nothing(db):
    actions = await seed.seed_parties(db, execute=False)

    assert {a["status"] for a in actions} == {"planned"}
    for party_value, org_id in (await _seeded_org_ids(db)).items():
        assert org_id is None, f"dry run created an Org for {party_value}"


# --------------------------------------------------------------------------
# The five rows
# --------------------------------------------------------------------------


async def test_execute_creates_five_inactive_orgs(db):
    actions = await seed.seed_parties(db, execute=True)

    assert len(actions) == 5
    assert {a["status"] for a in actions} == {"created"}

    for party in seed.PARTIES:
        org_id = await _org_id_for(db, party.party_value)
        assert org_id is not None, f"no Org for {party.party_value}"

        row = await db.fetchrow(
            "SELECT active, archived_at, notes FROM organizations WHERE id = $1", org_id
        )
        assert row["active"] is False, f"{party.party_value} must be inactive, not archived"
        assert row["archived_at"] is None, (
            f"{party.party_value} was archived at birth — an archived Org rejects "
            "subsequent active observations (#442)"
        )
        assert row["notes"], f"{party.party_value} carries no notes"


async def test_canonical_name_and_source_token_acronym(db):
    await seed.seed_parties(db, execute=True)

    for party in seed.PARTIES:
        org_id = await _org_id_for(db, party.party_value)

        name = await db.fetchrow(
            """SELECT name, name_type, is_canonical FROM organization_names
               WHERE organization_id = $1""",
            org_id,
        )
        assert name["name"] == party.name
        assert name["is_canonical"] is True

        acronym = await db.fetchrow(
            """SELECT acronym, is_canonical FROM organization_acronyms
               WHERE organization_id = $1""",
            org_id,
        )
        assert acronym["acronym"] == party.acronym, "acronym must be the source token (#442)"
        assert acronym["is_canonical"] is True


async def test_display_name_composes_name_and_token(db):
    """The acronym decision is a visible one: the admin renders "Name (Token)"."""
    await seed.seed_parties(db, execute=True)

    org_id = await _org_id_for(db, "peoples")
    display = await db.fetchval(
        "SELECT display_name FROM v_org_display_names WHERE organization_id = $1", org_id
    )
    assert display == "Washington State People's Party (P.P.)"


# --------------------------------------------------------------------------
# Lifecycle — the #442 ruling with teeth
# --------------------------------------------------------------------------


async def test_no_seeded_party_has_a_lifespan_bound(db):
    """The invariant that protects usa-wa#228: assignments must not be gated.

    A ``dissolved``/``merged_with`` event with a year would populate
    ``v_org_lifespan.ended_on`` and reject any assignment ending after it.
    """
    await seed.seed_parties(db, execute=True)

    for party in seed.PARTIES:
        org_id = await _org_id_for(db, party.party_value)
        ended_on = await db.fetchval(
            "SELECT ended_on FROM v_org_lifespan WHERE organization_id = $1", org_id
        )
        assert ended_on is None, (
            f"{party.party_value} carries a lifespan bound ({ended_on}) — this would "
            "reject the pre-1991 party assignments (#442)"
        )


async def test_no_dissolved_or_merged_events_written(db):
    await seed.seed_parties(db, execute=True)

    for party in seed.PARTIES:
        org_id = await _org_id_for(db, party.party_value)
        slugs = {
            r["slug"]
            for r in await db.fetch(
                """SELECT t.slug FROM entity_events ev
                   JOIN entity_event_types t ON t.id = ev.event_type_id
                   WHERE ev.entity_type = 'organization' AND ev.entity_id = $1""",
                org_id,
            )
        }
        assert not (slugs & {"dissolved", "merged_with", "succeeded_by"}), (
            f"{party.party_value} got a lifespan-bounding event: {slugs}"
        )


async def test_founded_events_only_where_wa_scoped(db):
    await seed.seed_parties(db, execute=True)

    for party in seed.PARTIES:
        org_id = await _org_id_for(db, party.party_value)
        event = await db.fetchrow(
            """SELECT ev.event_year, ev.event_month, ev.event_day
               FROM entity_events ev
               JOIN entity_event_types t ON t.id = ev.event_type_id
               WHERE ev.entity_type = 'organization' AND ev.entity_id = $1
                 AND t.slug = 'founded'""",
            org_id,
        )

        if party.party_value in NATIONAL_ONLY:
            assert event is None, (
                f"{party.party_value} has only a national founding date — asserting it "
                "on a Washington State Org is a scope error (#442)"
            )
            continue

        assert event is not None, f"{party.party_value} lost its WA-scoped founding"
        assert event["event_year"] == party.founded_year
        assert event["event_month"] == party.founded_month
        assert event["event_day"] is None, "no source gives day precision"


async def test_socialist_founding_carries_month_precision(db):
    """September 1901 is the SPA charter date — month precision is real here."""
    await seed.seed_parties(db, execute=True)

    org_id = await _org_id_for(db, "socialist")
    row = await db.fetchrow(
        """SELECT ev.event_year, ev.event_month FROM entity_events ev
           JOIN entity_event_types t ON t.id = ev.event_type_id
           WHERE ev.entity_id = $1 AND t.slug = 'founded'""",
        org_id,
    )
    assert (row["event_year"], row["event_month"]) == (1901, 9)


# --------------------------------------------------------------------------
# Attachments
# --------------------------------------------------------------------------


async def test_links_and_citations_attached(db):
    await seed.seed_parties(db, execute=True)

    for party in seed.PARTIES:
        org_id = await _org_id_for(db, party.party_value)

        urls = {
            r["url"]
            for r in await db.fetch(
                "SELECT url FROM links WHERE entity_type = 'organization' AND entity_id = $1",
                org_id,
            )
        }
        assert set(party.links) <= urls

        citations = await db.fetch(
            """SELECT title, url FROM citations
               WHERE entity_type = 'organization' AND entity_id = $1""",
            org_id,
        )
        titles = {c["title"] for c in citations}
        assert seed.ROSTER_CITATION["title"] in titles, "roster citation missing"
        assert seed.BRAZIER_CITATION["title"] in titles, "Brazier citation missing"


# --------------------------------------------------------------------------
# Idempotency and collision safety
# --------------------------------------------------------------------------


async def test_rerun_is_idempotent(db):
    await seed.seed_parties(db, execute=True)
    before = await _seeded_org_ids(db)

    actions = await seed.seed_parties(db, execute=True)

    assert {a["status"] for a in actions} == {"exists"}
    assert await _seeded_org_ids(db) == before

    for org_id in before.values():
        for table, where in (
            ("organization_names", "organization_id"),
            ("organization_acronyms", "organization_id"),
        ):
            count = await db.fetchval(f"SELECT count(*) FROM {table} WHERE {where} = $1", org_id)
            assert count == 1, f"{table} duplicated on re-run"


async def test_existing_party_org_is_never_duplicated(db):
    """An Org already carrying the org_wa_party value is adopted, not re-created."""
    existing = generate_id()
    await db.execute("INSERT INTO organizations (id, active) VALUES ($1, TRUE)", existing)
    type_id = await db.fetchval(
        "SELECT id FROM entity_identifier_types WHERE slug = 'org_wa_party'"
    )
    await db.execute(
        """INSERT INTO identifiers (id, entity_identifier_type_id, entity_id, value)
           VALUES ($1, $2, $3, 'populist')""",
        generate_id(),
        type_id,
        existing,
    )

    actions = await seed.seed_parties(db, execute=True)

    populist = next(a for a in actions if a["party_value"] == "populist")
    assert populist["status"] == "exists"
    assert populist["org_id"] == existing
    assert await _org_id_for(db, "populist") == existing
    # An Org that predates the seed keeps its own flag — the seed does not
    # deactivate rows it did not create.
    assert await db.fetchval("SELECT active FROM organizations WHERE id = $1", existing) is True


async def test_party_values_are_bare_lowercase_slugs(db):
    """#270's value convention: no ``wa-`` prefix; the type already scopes to WA."""
    for party in seed.PARTIES:
        assert party.party_value == party.party_value.lower()
        assert not party.party_value.startswith("wa-")
        assert " " not in party.party_value


# --------------------------------------------------------------------------
# The identifier space is not trustworthy on its own (CR round 1, finding 1)
# --------------------------------------------------------------------------
#
# ``identifiers`` has no FK to ``organizations`` and ``idx_identifiers_lookup``
# is a plain, non-unique index. ``org_delete`` removes an Org's names and
# acronyms but leaves its identifiers behind, and the ancillary-orphans audit
# sweeps only ``role`` / ``role_assignment`` scopes — so a hard-deleted party Org
# leaves a live-looking ``org_wa_party`` row pointing at nothing. Treating that as
# "already present" would silently skip the party, which is the exact failure
# class #442 exists to eliminate.


async def _party_identifier(db, value, entity_id):
    type_id = await db.fetchval(
        "SELECT id FROM entity_identifier_types WHERE slug = 'org_wa_party'"
    )
    await db.execute(
        """INSERT INTO identifiers (id, entity_identifier_type_id, entity_id, value)
           VALUES ($1, $2, $3, $4)""",
        generate_id(),
        type_id,
        entity_id,
        value,
    )


async def _live_org_count_for(db, value):
    return await db.fetchval(
        """SELECT count(*)
           FROM identifiers i
           JOIN entity_identifier_types t ON t.id = i.entity_identifier_type_id
           JOIN organizations o ON o.id = i.entity_id
           WHERE t.slug = 'org_wa_party' AND i.value = $1""",
        value,
    )


async def test_dangling_identifier_is_blocked_not_silently_skipped(db):
    """An identifier pointing at a deleted Org must not read as "already present"."""
    await _party_identifier(db, "populist", generate_id())  # no organizations row

    actions = await seed.seed_parties(db, execute=True)

    populist = next(a for a in actions if a["party_value"] == "populist")
    assert populist["status"] == "blocked", (
        "a dangling org_wa_party row was treated as an existing Org — the party "
        "would be silently skipped (#442 CR1)"
    )
    assert populist["org_id"] is None
    assert await _live_org_count_for(db, "populist") == 0

    # One blocked party must not stop the other four.
    assert sum(1 for a in actions if a["status"] == "created") == 4


async def test_ambiguous_identifier_rows_are_blocked(db):
    """Two Orgs sharing a party value: pick neither, report it."""
    for _ in range(2):
        org_id = generate_id()
        await db.execute("INSERT INTO organizations (id, active) VALUES ($1, TRUE)", org_id)
        await _party_identifier(db, "socialist", org_id)

    actions = await seed.seed_parties(db, execute=True)

    socialist = next(a for a in actions if a["party_value"] == "socialist")
    assert socialist["status"] == "blocked", (
        "an ambiguous org_wa_party value resolved to an arbitrary Org (#442 CR1)"
    )
    assert socialist["org_id"] is None


async def test_blocked_party_is_not_created_on_a_later_run(db):
    """Blocking is not a transient state the next run papers over."""
    await _party_identifier(db, "peoples", generate_id())

    await seed.seed_parties(db, execute=True)
    actions = await seed.seed_parties(db, execute=True)

    peoples = next(a for a in actions if a["party_value"] == "peoples")
    assert peoples["status"] == "blocked"
    assert await _live_org_count_for(db, "peoples") == 0


# --------------------------------------------------------------------------
# Citation provenance (CR round 1, finding 2)
# --------------------------------------------------------------------------


async def test_citations_record_when_the_source_was_read(db):
    """The roster is revised every 12-24 months, so "when we read it" is the
    field that makes a stale citation detectable."""
    await seed.seed_parties(db, execute=True)

    for party in seed.PARTIES:
        org_id = await _org_id_for(db, party.party_value)
        rows = await db.fetch(
            """SELECT title, accessed_at FROM citations
               WHERE entity_type = 'organization' AND entity_id = $1""",
            org_id,
        )
        assert rows, f"{party.party_value} has no citations"
        for row in rows:
            assert row["accessed_at"] is not None, (
                f"citation {row['title']!r} records no accessed_at"
            )
            assert row["accessed_at"].tzinfo is not None, "accessed_at must be tz-aware UTC"
