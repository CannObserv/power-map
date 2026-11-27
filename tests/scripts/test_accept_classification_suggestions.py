"""Tests for scripts/accept_classification_suggestions.py (#877 phase 3).

The operator's bulk accept: dry run by default; ``--execute`` accepts through
``classification.record_decision`` as the named operator. It refuses to execute
while Jev still has orgs to answer (a rules kind can't be checked against an
answer that hasn't arrived), unless told to. The DB-backed tests need
TEST_DATABASE_URL.
"""

import logging
from collections import Counter

import pytest
import pytest_asyncio

from scripts import accept_classification_suggestions as acs
from src.core import classification as cls
from src.core.db import generate_id

MODEL = "jev-1.14.0"


def _item(org_id, source=None, kind=None, reason=None, rules=None, jev=None):
    return cls.BulkItem(
        org_id=org_id,
        rules=rules,
        jev=jev,
        confidence=None,
        source=source,
        kind=kind,
        model=None,
        reason=reason,
    )


# --- the summary: pure ---------------------------------------------------


def test_summary_counts_acceptances_and_the_queue():
    items = [
        _item("a", "rules", "legislature"),
        _item("b", "rules", "legislature"),
        _item("c", "jev", "business"),
        _item("d", reason="low"),
        _item("e", reason="disagree"),
        _item("f", reason="low"),
    ]
    to_accept, queue = acs.summarize(items)
    assert to_accept == Counter({("rules", "legislature"): 2, ("jev", "business"): 1})
    assert queue == Counter({"low": 2, "disagree": 1})


def test_the_skim_is_every_rules_acceptance_outside_the_legislature():
    items = [
        _item("a", "rules", "legislature"),
        _item("b", "rules", "business"),
        _item("c", "jev", "business"),
        _item("d", "rules", "political_party"),
        _item("e", reason="disagree", rules="business"),
    ]
    assert [i.org_id for i in acs.skim_items(items)] == ["b", "d"]


def test_report_lines_name_orgs_only_in_the_skim():
    report = acs.Report(
        items=[_item("01AAA", "rules", "business"), _item("01BBB", "jev", "business")],
        unanswered=0,
        skim=[acs.SkimRow("01AAA", "Acme Farms", "business", "business")],
    )
    text = "\n".join(acs.report_lines(report, execute=False))
    assert "Acme Farms" in text and "gold: business" in text
    assert "01BBB" not in text
    assert "dry run (org_kind)" in text


@pytest.mark.integration
async def test_a_field_without_a_bulk_accept_is_refused(db):
    """CR 6: --field reaches the run, and only org_kind has a bulk accept yet (#303 adds one)."""
    with pytest.raises(acs.UsageError, match="no bulk accept for 'role_type'"):
        await acs.accept(
            db,
            field_name="role_type",
            operator="x@test.com",
            execute=False,
            allow_unanswered=True,
            threshold=0.7,
            gold={},
        )


def test_a_run_quiets_the_per_decision_log_lines():
    """CR 5: ~1,400 INFO lines would bury the summary; the manual rows are the record."""
    logger = logging.getLogger("src.core.classification")
    before = logger.level
    try:
        acs.quiet_decision_logs()
        assert not logger.isEnabledFor(logging.INFO)
        assert logger.isEnabledFor(logging.WARNING)
    finally:
        logger.setLevel(before)


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


async def _org(db, name="Org") -> str:
    oid = generate_id()
    await db.execute("INSERT INTO organizations (id) VALUES ($1)", oid)
    await db.execute(
        "INSERT INTO organization_names (id, organization_id, name, is_canonical)"
        " VALUES ($1, $2, $3, true)",
        generate_id(),
        oid,
        name,
    )
    return oid


async def _user(db) -> str:
    uid = generate_id()
    await db.execute("INSERT INTO app_users (id, email) VALUES ($1,$2)", uid, f"{uid}@test.com")
    return f"{uid}@test.com"


async def _suggest(db, org_id, source, value, confidence=None):
    await db.execute(
        "INSERT INTO classification_suggestions"
        " (id, entity_type, entity_id, field, source, value, model, confidence)"
        " VALUES ($1,'organization',$2,'org_kind',$3,$4,$5,$6)",
        generate_id(),
        org_id,
        source,
        value,
        cls.RULES_VERSION if source == "rules" else MODEL,
        confidence,
    )


async def _kind(db, org_id):
    return await db.fetchval(
        "SELECT k.slug FROM organizations o LEFT JOIN org_kinds k ON k.id = o.org_kind_id"
        " WHERE o.id = $1",
        org_id,
    )


async def _run(db, email, **kw):
    args = {"execute": False, "allow_unanswered": True, "threshold": 0.7, "gold": {}} | kw
    return await acs.accept(db, operator=email, **args)


@pytest.mark.integration
async def test_dry_run_writes_nothing(db):
    org, email = await _org(db), await _user(db)
    await _suggest(db, org, "rules", "legislature")
    report = await _run(db, email)
    assert report.result is None
    assert any(i.org_id == org and i.source == "rules" for i in report.items)
    assert await _kind(db, org) is None


@pytest.mark.integration
async def test_execute_accepts_as_the_operator(db):
    org, email = await _org(db), await _user(db)
    await _suggest(db, org, "jev", "business", 0.93)
    report = await _run(db, email, execute=True)
    assert report.result.accepted[("jev", "business")] >= 1
    assert await _kind(db, org) == "business"
    by = await db.fetchval(
        "SELECT u.email FROM classification_suggestions s JOIN app_users u ON u.id = s.created_by"
        " WHERE s.entity_id = $1 AND s.source = 'manual'",
        org,
    )
    assert by == email


@pytest.mark.integration
async def test_an_unknown_operator_is_refused(db):
    with pytest.raises(acs.UsageError, match="no admin user"):
        await _run(db, "nobody-here@test.com")


@pytest.mark.integration
async def test_execute_waits_for_jev_unless_told_not_to(db):
    org, email = await _org(db), await _user(db)
    await _suggest(db, org, "rules", "legislature")  # Jev hasn't answered it
    with pytest.raises(acs.UsageError, match="still unanswered"):
        await _run(db, email, execute=True, allow_unanswered=False)
    assert await _kind(db, org) is None
    # The dry run reports the wait instead of refusing.
    report = await _run(db, email, allow_unanswered=False)
    assert report.unanswered >= 1


@pytest.mark.integration
async def test_the_skim_names_the_org_and_its_gold_label(db):
    org, email = await _org(db, name="Skim Farms LLC"), await _user(db)
    await _suggest(db, org, "rules", "business")
    report = await _run(db, email, gold={org: "tribe"})
    (row,) = [r for r in report.skim if r.org_id == org]
    assert row == acs.SkimRow(org, "Skim Farms LLC", "business", "tribe")


@pytest.mark.integration
async def test_execute_refuses_while_a_drain_runs(db, db_pool):
    org, email = await _org(db), await _user(db)
    await _suggest(db, org, "rules", "legislature")
    async with db_pool.acquire() as drainer:
        await drainer.execute("SELECT pg_advisory_lock($1)", cls.DRAIN_LOCK_KEY)
        try:
            with pytest.raises(acs.UsageError, match="drain is running"):
                await _run(db, email, execute=True)
        finally:
            await drainer.execute("SELECT pg_advisory_unlock($1)", cls.DRAIN_LOCK_KEY)
    assert await _kind(db, org) is None
