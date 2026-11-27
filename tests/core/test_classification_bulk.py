"""Bulk accept: which suggestions an operator's run accepts, and which wait for review (#877).

Phase 3, design D7 as amended: a rules kind is accepted by definition
(decision C, 2026-11-25); a Jev kind only at or above the threshold, on a kind
trusted for the answering model (the gold set, 2026-11-27). A disagreement, a
conflict, a low or untrusted answer, a `none`, or no answer yet all go to the
review queue. An org with any manual row is the operator's and is never touched.
"""

import pytest
import pytest_asyncio

from src.core import classification as cls
from src.core.db import generate_id

T = 0.7
TRUSTED = "jev-1.14.0"


def _jev(value, confidence=0.9, model=TRUSTED):
    return cls.JevView(value=value, confidence=confidence, model=model)


# --- the trust set ---------------------------------------------------------


def test_the_trust_set_is_the_approved_one_and_keyed_by_model():
    assert cls.TRUSTED_JEV_KINDS == {
        "jev-1.14.0": frozenset({"association", "business", "government", "legislature"})
    }


# --- bulk_outcome: pure ------------------------------------------------------


@pytest.mark.parametrize(
    ("rules", "jev", "expected"),
    [
        # rules by definition, unless Jev says otherwise
        (("legislature", None), None, ("rules", "legislature")),
        (("legislature", None), _jev("legislature"), ("rules", "legislature")),
        (("business", None), _jev("tribe", 0.95), ("disagree", None)),
        (("business", None), _jev("none"), ("disagree", None)),
        (("business", None), _jev("tribe", 0.2), ("disagree", None)),
        # rules found conflicting identifiers
        (("none", "conflict: a (x), b (y)"), _jev("business"), ("conflict", None)),
        # no rule: Jev, if trusted and sure enough
        (None, _jev("government", 0.7), ("jev", "government")),
        (("none", "no rule applies"), _jev("business"), ("jev", "business")),
        (None, _jev("government", 0.69), ("low", None)),
        (None, _jev("nonprofit", 0.99), ("untrusted", None)),
        (None, _jev("business", 0.99, model="jev-9.9.9"), ("untrusted", None)),
        (None, _jev("none", 0.99), ("none", None)),
        (None, None, ("unanswered", None)),
    ],
)
def test_bulk_outcome(rules, jev, expected):
    assert cls.bulk_outcome(rules, jev, threshold=T) == expected


# --- DB-backed -------------------------------------------------------------


@pytest_asyncio.fixture(loop_scope="session")
async def db(db_pool):
    async with db_pool.acquire() as conn:
        tr = conn.transaction()
        await tr.start()
        try:
            yield conn
        finally:
            await tr.rollback()


async def _org(db, archived=False) -> str:
    oid = generate_id()
    await db.execute(
        "INSERT INTO organizations (id, archived_at) VALUES ($1, CASE WHEN $2 THEN NOW() END)",
        oid,
        archived,
    )
    return oid


async def _user(db) -> str:
    uid = generate_id()
    await db.execute("INSERT INTO app_users (id, email) VALUES ($1,$2)", uid, f"{uid}@test.com")
    return uid


async def _suggest(db, org_id, source, value, confidence=None, model=None, note=None):
    await db.execute(
        "INSERT INTO classification_suggestions"
        " (id, entity_type, entity_id, field, source, value, model, confidence, note)"
        " VALUES ($1,'organization',$2,'org_kind',$3,$4,$5,$6,$7)",
        generate_id(),
        org_id,
        source,
        value,
        model or (cls.RULES_VERSION if source == "rules" else TRUSTED),
        confidence,
        note,
    )


async def _kind(db, org_id):
    return await db.fetchval(
        "SELECT k.slug FROM organizations o LEFT JOIN org_kinds k ON k.id = o.org_kind_id"
        " WHERE o.id = $1",
        org_id,
    )


def _mine(items, *ids):
    return {i.org_id: i for i in items if i.org_id in ids}


@pytest.mark.integration
async def test_plan_reads_each_orgs_latest_live_answers(db):
    by_rules, by_jev, disputed, waiting = [await _org(db) for _ in range(4)]
    await _suggest(db, by_rules, "rules", "legislature")
    await _suggest(db, by_jev, "rules", "business")
    await _suggest(db, by_jev, "rules", "none", note="no rule applies")  # superseded
    await _suggest(db, by_jev, "jev", "government", 0.88)
    await _suggest(db, disputed, "rules", "business")
    await _suggest(db, disputed, "jev", "tribe", 0.97)

    got = _mine(await cls.plan_bulk_accept(db, threshold=T), by_rules, by_jev, disputed, waiting)

    assert {o: (i.reason, i.source, i.kind) for o, i in got.items()} == {
        by_rules: (None, "rules", "legislature"),
        by_jev: (None, "jev", "government"),
        disputed: ("disagree", None, None),
        waiting: ("unanswered", None, None),
    }
    assert got[by_rules].model == cls.RULES_VERSION
    assert (got[by_jev].model, got[by_jev].confidence) == (TRUSTED, 0.88)
    assert (got[disputed].rules, got[disputed].jev) == ("business", "tribe")


@pytest.mark.integration
async def test_plan_skips_an_ignored_jev_answer(db):
    org = await _org(db)
    await _suggest(db, org, "jev", "business", 0.9)
    await db.execute(
        "UPDATE classification_suggestions SET ignored_at = NOW() WHERE entity_id = $1", org
    )
    (item,) = _mine(await cls.plan_bulk_accept(db, threshold=T), org).values()
    assert item.reason == "unanswered"


@pytest.mark.integration
async def test_plan_leaves_archived_and_operator_touched_orgs_alone(db):
    archived, decided, cleared = await _org(db, archived=True), await _org(db), await _org(db)
    for org in (archived, decided, cleared):
        await _suggest(db, org, "rules", "legislature")
    await cls.record_decision(db, org_id=decided, action="dismiss")
    await cls.record_decision(db, org_id=cleared, action="clear")
    assert _mine(await cls.plan_bulk_accept(db, threshold=T), archived, decided, cleared) == {}


@pytest.mark.integration
async def test_apply_accepts_through_record_decision(db):
    org, user = await _org(db), await _user(db)
    await _suggest(db, org, "jev", "government", 0.9)
    items = list(_mine(await cls.plan_bulk_accept(db, threshold=T), org).values())

    result = await cls.apply_bulk_accept(db, items, user_id=user)

    assert result.accepted == {("jev", "government"): 1}
    assert result.skipped == []
    assert await _kind(db, org) == "government"
    row = await db.fetchrow(
        "SELECT manual_action, accepted_source, created_by, note FROM classification_suggestions"
        " WHERE entity_id = $1 AND source = 'manual'",
        org,
    )
    assert dict(row) == {
        "manual_action": "accept",
        "accepted_source": "jev",
        "created_by": user,
        "note": f"bulk accept {TRUSTED}",
    }


@pytest.mark.integration
async def test_apply_skips_an_org_an_operator_reached_first(db):
    """Planned, then decided in the panel before the run reached it: left as the operator set it."""
    org, user = await _org(db), await _user(db)
    await _suggest(db, org, "rules", "legislature")
    items = list(_mine(await cls.plan_bulk_accept(db, threshold=T), org).values())
    await cls.record_decision(db, org_id=org, action="set", kind="government")

    result = await cls.apply_bulk_accept(db, items, user_id=user)

    assert result.accepted == {}
    assert [(o, why) for o, why in result.skipped] == [(org, "an operator has acted on it")]
    assert await _kind(db, org) == "government"


@pytest.mark.integration
async def test_apply_never_accepts_a_queued_item(db):
    org, user = await _org(db), await _user(db)
    await _suggest(db, org, "jev", "nonprofit", 0.99)
    items = list(_mine(await cls.plan_bulk_accept(db, threshold=T), org).values())
    result = await cls.apply_bulk_accept(db, items, user_id=user)
    assert result.accepted == {} and result.skipped == []
    assert await _kind(db, org) is None


@pytest.mark.integration
async def test_untouched_only_refuses_an_org_with_any_manual_row(db):
    org = await _org(db)
    await _suggest(db, org, "rules", "legislature")
    await cls.record_decision(db, org_id=org, action="clear")
    with pytest.raises(cls.DecisionError, match="an operator has acted on it"):
        await cls.record_decision(
            db, org_id=org, action="accept", kind="legislature", source="rules", untouched_only=True
        )
    assert await _kind(db, org) is None


@pytest.mark.integration
async def test_a_run_waits_for_no_drain(db, db_pool):
    """Plan and apply hold the drain lock: no rules or Jev row lands between them (CR 2)."""
    org, user = await _org(db), await _user(db)
    await _suggest(db, org, "rules", "legislature")
    async with db_pool.acquire() as drainer:
        await drainer.execute("SELECT pg_advisory_lock($1)", cls.DRAIN_LOCK_KEY)
        try:
            assert await cls.run_bulk_accept(db, threshold=T, user_id=user) is None
        finally:
            await drainer.execute("SELECT pg_advisory_unlock($1)", cls.DRAIN_LOCK_KEY)
    assert await _kind(db, org) is None

    items, result = await cls.run_bulk_accept(db, threshold=T, user_id=user)
    assert any(i.org_id == org for i in items)
    assert result.accepted[("rules", "legislature")] >= 1
    assert await _kind(db, org) == "legislature"
    # The lock is released after the run.
    assert await db.fetchval("SELECT pg_try_advisory_lock($1)", cls.DRAIN_LOCK_KEY)
    await db.execute("SELECT pg_advisory_unlock($1)", cls.DRAIN_LOCK_KEY)
