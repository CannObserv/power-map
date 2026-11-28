"""Tests for src/core/classification.py — org-kind rules and operator decisions (#877).

The resolved kind (``organizations.org_kind_id``) is written only by
``record_decision``; every source's answer is a row in
``classification_suggestions``. Unit tests cover the pure rule table; the rest
run against the test DB inside a rolled-back transaction.
"""

import pytest
import pytest_asyncio

from src.core import classification as cls
from src.core.db import generate_id

# --- rules: pure ---------------------------------------------------------
# rules_kind takes (kind, identifier type) pairs; which types imply which kind
# is reference data in schema.sql (see test_identifier_types_imply_kinds below).


def test_rules_kind_one_kind():
    assert cls.rules_kind([("legislature", "org_wa_legislature")]) == ("legislature", None)


def test_rules_kind_agreeing_identifiers_are_one_kind():
    pairs = [("legislature", "org_wa_legislature"), ("legislature", "org_wa_legislature_chamber")]
    assert cls.rules_kind(pairs) == ("legislature", None)


def test_rules_kind_says_nothing_without_a_rule():
    assert cls.rules_kind([]) == (None, None)


def test_rules_kind_conflict_is_none_with_a_note():
    kind, note = cls.rules_kind([("political_party", "org_wa_party"), ("business", "org_wslcb")])
    assert kind == "none"
    assert note == "conflict: business (org_wslcb), political_party (org_wa_party)"


# --- DB-backed -----------------------------------------------------------


@pytest_asyncio.fixture(loop_scope="session")
async def db(db_pool):
    async with db_pool.acquire() as conn:
        tr = conn.transaction()
        await tr.start()
        try:
            yield conn
        finally:
            await tr.rollback()


async def _org(db) -> str:
    oid = generate_id()
    await db.execute("INSERT INTO organizations (id) VALUES ($1)", oid)
    return oid


async def _user(db) -> str:
    uid = generate_id()
    await db.execute("INSERT INTO app_users (id, email) VALUES ($1,$2)", uid, f"{uid}@test.com")
    return uid


async def _identify(db, org_id: str, slug: str) -> None:
    type_id = await db.fetchval("SELECT id FROM entity_identifier_types WHERE slug=$1", slug)
    await db.execute(
        "INSERT INTO identifiers (id, entity_id, entity_identifier_type_id, value)"
        " VALUES ($1,$2,$3,$4)",
        generate_id(),
        org_id,
        type_id,
        f"v-{generate_id()[-8:]}",
    )


async def _kind_slug(db, org_id: str) -> str | None:
    return await db.fetchval(
        "SELECT k.slug FROM organizations o LEFT JOIN org_kinds k ON k.id=o.org_kind_id"
        " WHERE o.id=$1",
        org_id,
    )


async def _suggest(db, org_id, source, value, **extra):
    await db.execute(
        "INSERT INTO classification_suggestions"
        " (id, entity_type, entity_id, field, source, value, model, confidence)"
        " VALUES ($1,'organization',$2,'org_kind',$3,$4,$5,$6)",
        generate_id(),
        org_id,
        source,
        value,
        extra.get("model", "rules-1" if source == "rules" else "jev-1.14.0"),
        extra.get("confidence"),
    )


@pytest.mark.integration
async def test_identifier_types_imply_kinds(db):
    """The rule table is reference data; UBI and PDC registrations imply nothing.

    Nor does a PDC campaign-finance committee filer ID (rule retired
    2026-11-27, #877): associations and others register as committees too.
    """
    rows = await db.fetch(
        "SELECT t.slug, k.slug AS kind FROM entity_identifier_types t"
        " JOIN org_kinds k ON k.id = t.implies_org_kind_id"
    )
    assert {r["slug"]: r["kind"] for r in rows} == {
        "org_wa_legislature": "legislature",
        "org_wa_legislature_chamber": "legislature",
        "org_wa_legislature_committee_id": "legislature",
        "org_wa_party": "political_party",
        "org_wslcb": "business",
    }


@pytest.mark.integration
async def test_plan_rules_flags_conflicting_identifiers(db):
    org = await _org(db)
    await _identify(db, org, "org_wslcb")
    await _identify(db, org, "org_wa_party")
    change = next(c for c in await cls.plan_rules(db) if c.org_id == org)
    assert change.value == "none"
    assert change.note.startswith("conflict: ")


@pytest.mark.integration
async def test_a_retired_rule_supersedes_its_answer_under_the_new_version(db):
    """rules-1 said campaign_committee from the PDC committee ID; rules-2 has no such rule."""
    org = await _org(db)
    await _identify(db, org, "org_wa_pdc_committee")
    await db.execute(
        "INSERT INTO classification_suggestions"
        " (id, entity_type, entity_id, field, source, value, model)"
        " VALUES ($1, 'organization', $2, 'org_kind', 'rules', 'campaign_committee', 'rules-1')",
        generate_id(),
        org,
    )
    change = next(c for c in await cls.plan_rules(db) if c.org_id == org)
    assert (change.value, change.note) == ("none", "no rule applies")
    await cls.apply_rules(db, [change])
    model = await db.fetchval(
        "SELECT model FROM classification_suggestions"
        " WHERE entity_id = $1 AND source = 'rules' AND value = 'none'",
        org,
    )
    assert model == cls.RULES_VERSION == "rules-2"


@pytest.mark.integration
async def test_set_records_a_manual_row_and_the_kind(db):
    org, user = await _org(db), await _user(db)
    await cls.record_decision(db, org_id=org, action="set", kind="nonprofit", user_id=user)
    assert await _kind_slug(db, org) == "nonprofit"
    row = await db.fetchrow(
        "SELECT source, value, manual_action, created_by FROM classification_suggestions"
        " WHERE entity_id=$1",
        org,
    )
    assert dict(row) == {
        "source": "manual",
        "value": "nonprofit",
        "manual_action": "set",
        "created_by": user,
    }
    assert await cls.is_decided(db, org)


@pytest.mark.integration
async def test_accept_takes_a_sources_current_answer(db):
    org = await _org(db)
    await _suggest(db, org, "rules", "legislature")
    await cls.record_decision(db, org_id=org, action="accept", kind="legislature", source="rules")
    assert await _kind_slug(db, org) == "legislature"
    note = await db.fetchval(
        "SELECT note FROM classification_suggestions WHERE entity_id=$1 AND source='manual'", org
    )
    assert note == "accepted rules"


@pytest.mark.integration
async def test_accept_records_which_source_it_took(db):
    """accepted_source, not the note, says where an accepted kind came from (#877 phase 3)."""
    accepted, chosen = await _org(db), await _org(db)
    await _suggest(db, accepted, "jev", "coalition", confidence=0.9)
    await cls.record_decision(
        db, org_id=accepted, action="accept", kind="coalition", source="jev", note="checked"
    )
    await cls.record_decision(db, org_id=chosen, action="set", kind="coalition")
    rows = await db.fetch(
        "SELECT entity_id, accepted_source, note FROM classification_suggestions"
        " WHERE source = 'manual' AND entity_id = ANY($1::text[])",
        [accepted, chosen],
    )
    got = {r["entity_id"]: (r["accepted_source"], r["note"]) for r in rows}
    assert got == {accepted: ("jev", "checked"), chosen: (None, None)}


@pytest.mark.integration
async def test_accept_refuses_a_kind_the_source_did_not_answer(db):
    org = await _org(db)
    await _suggest(db, org, "rules", "legislature")
    with pytest.raises(cls.DecisionError, match="stale"):
        await cls.record_decision(db, org_id=org, action="accept", kind="business", source="rules")
    assert await _kind_slug(db, org) is None


@pytest.mark.integration
async def test_unknown_kind_is_refused(db):
    org = await _org(db)
    with pytest.raises(cls.DecisionError, match="unknown kind"):
        await cls.record_decision(db, org_id=org, action="set", kind="cooperative")


@pytest.mark.integration
async def test_dismiss_decides_with_no_kind_and_clear_undecides(db):
    org = await _org(db)
    await cls.record_decision(db, org_id=org, action="set", kind="business")
    await cls.record_decision(db, org_id=org, action="dismiss")
    assert await _kind_slug(db, org) is None
    assert await cls.is_decided(db, org)
    await cls.record_decision(db, org_id=org, action="clear")
    assert await _kind_slug(db, org) is None
    assert not await cls.is_decided(db, org)


@pytest.mark.integration
async def test_decision_refuses_an_archived_or_missing_org(db):
    org = await _org(db)
    await db.execute("UPDATE organizations SET archived_at=NOW() WHERE id=$1", org)
    with pytest.raises(cls.DecisionError, match="not found or archived"):
        await cls.record_decision(db, org_id=org, action="set", kind="business")
    with pytest.raises(cls.DecisionError, match="not found or archived"):
        await cls.record_decision(db, org_id=generate_id(), action="set", kind="business")


@pytest.mark.integration
async def test_latest_answers_one_per_source_skipping_ignored(db):
    org = await _org(db)
    await _suggest(db, org, "rules", "business")
    await _suggest(db, org, "rules", "none")
    await _suggest(db, org, "jev", "nonprofit", confidence=0.8)
    await db.execute(
        "UPDATE classification_suggestions SET ignored_at=NOW()"
        " WHERE entity_id=$1 AND source='jev'",
        org,
    )
    await _suggest(db, org, "jev", "association", model="jev-1.15.0", confidence=0.93)
    answers = await cls.latest_answers(db, org)
    assert answers["rules"]["value"] == "none"
    assert answers["jev"]["value"] == "association"
    assert answers["jev"]["confidence"] == pytest.approx(0.93)
    assert "manual" not in answers


@pytest.mark.integration
async def test_plan_rules_inserts_only_changes(db):
    org_leg, org_none, org_gone = await _org(db), await _org(db), await _org(db)
    await _identify(db, org_leg, "org_wa_legislature_committee_id")
    await _identify(db, org_none, "org_ubi")
    # org_gone had a rules answer, then lost the identifier behind it.
    await _suggest(db, org_gone, "rules", "business")
    ids = {org_leg, org_none, org_gone}

    changes = [c for c in await cls.plan_rules(db) if c.org_id in ids]
    by_org = {c.org_id: c for c in changes}
    assert by_org[org_leg].value == "legislature"
    assert org_none not in by_org  # no rule and no earlier answer: nothing to write
    assert by_org[org_gone].value == "none"
    assert by_org[org_gone].note == "no rule applies"

    await cls.apply_rules(db, changes)
    again = [c for c in await cls.plan_rules(db) if c.org_id in ids]
    assert again == []


@pytest.mark.integration
async def test_plan_rules_skips_archived_orgs(db):
    org = await _org(db)
    await _identify(db, org, "org_wa_party")
    await db.execute("UPDATE organizations SET archived_at=NOW() WHERE id=$1", org)
    assert org not in {c.org_id for c in await cls.plan_rules(db)}


@pytest.mark.integration
async def test_latest_decision_is_the_last_written_within_one_transaction(db):
    """CR 1: rows written in one transaction must still order by write, not by ULID luck."""
    org = await _org(db)
    for _ in range(5):
        await cls.record_decision(db, org_id=org, action="set", kind="business")
        await cls.record_decision(db, org_id=org, action="clear")
        assert not await cls.is_decided(db, org)
        await cls.record_decision(db, org_id=org, action="dismiss")
        assert await cls.is_decided(db, org)


@pytest.mark.integration
async def test_merge_carries_a_decision_over_a_survivors_clear(db):
    """CR 2: a survivor undecided by a newer `clear` takes the loser's decision cleanly."""
    survivor, loser = await _org(db), await _org(db)
    await cls.record_decision(db, org_id=loser, action="set", kind="tribe")
    await cls.record_decision(db, org_id=survivor, action="set", kind="business")
    await cls.record_decision(db, org_id=survivor, action="clear")
    await cls.carry_kind_on_merge(db, loser_id=loser, survivor_id=survivor)
    assert await _kind_slug(db, survivor) == "tribe"
    assert await cls.is_decided(db, survivor)
    latest = (await cls.latest_answers(db, survivor))["manual"]
    assert (latest["manual_action"], latest["value"]) == ("set", "tribe")
    assert latest["note"] == f"carried from merged {loser}"


# --- the public wire's org_kind_source (#877 phase 3c, D9) -----------------


@pytest.mark.parametrize(
    ("action", "accepted_source", "expected"),
    [
        ("accept", "rules", "rules"),
        ("accept", "jev", "ai"),  # vendor-neutral, as on the admin panel
        ("set", None, "operator"),
        ("dismiss", None, None),
        ("clear", None, None),
        (None, None, None),
    ],
)
def test_kind_source_names_who_decided_in_wire_terms(action, accepted_source, expected):
    assert cls.kind_source(action, accepted_source) == expected


def test_kind_sources_are_the_wire_values():
    assert cls.KIND_SOURCES == ("rules", "ai", "operator")
