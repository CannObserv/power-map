"""Tests for scripts/classify_suggestions.py (#877).

``--source rules`` writes a ``classification_suggestions`` row only when an
org's identifier-implied kind changed. ``--source all`` is the drain: rules,
then Jev (a fake here; nothing calls the API). Dry run by default;
``--execute`` writes. The DB-backed tests need TEST_DATABASE_URL.
"""

from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio

from scripts import classify_suggestions as cs
from scripts.classify_suggestions import classify
from src.core import classification as cls
from src.core import jev_client as jc
from src.core.db import generate_id
from src.core.retry import Backoff

NOW = datetime(2026, 11, 26, 12, tzinfo=UTC)


# --- the timer's verdict: pure ------------------------------------------


def _result(status="ok", failed=0, permanent=False):
    return cls.DrainResult(
        status=status, asked=2, answered=2 - failed, failed=failed, permanent=permanent
    )


@pytest.mark.parametrize("status", ["ok", "idle", "disabled", "busy"])
def test_verdict_quiet_runs_are_ok(status):
    assert cs.verdict(cls.DrainResult(status=status), None, NOW) == 0


def test_verdict_a_permanent_failure_alerts_at_once():
    assert cs.verdict(_result("partial", 1, permanent=True), NOW, NOW) == cs.EXIT_JEV_FAILED


def test_verdict_a_blip_with_a_recent_answer_is_ok():
    recent = NOW - timedelta(hours=3)
    assert cs.verdict(_result("failed", 2), recent, NOW) == 0


def test_verdict_failing_with_no_answer_for_two_days_alerts():
    stale = NOW - cs.STALE_AFTER - timedelta(minutes=1)
    assert cs.verdict(_result("failed", 2), stale, NOW) == cs.EXIT_JEV_FAILED
    assert cs.verdict(_result("failed", 2), None, NOW) == cs.EXIT_JEV_FAILED


def test_verdict_without_a_drain_is_ok():
    assert cs.verdict(None, None, NOW) == 0


async def test_timer_ask_retries_a_transient_failure_on_the_run_budget(monkeypatch):
    calls = []

    async def flaky(org, crit, **kw):
        calls.append(kw)
        if len(calls) == 1:
            raise jc.JevError("Jev: HTTP 503", status=503, transient=True)
        return "answer"

    monkeypatch.setattr(cs.jc, "ask_kind", flaky)
    ask = cs.bounded_ask(Backoff((0.0,)))
    assert await ask("org", {}, api_key="k", model="m", client=None) == "answer"
    assert len(calls) == 2
    assert calls[0]["timeout"] == cs.JEV_TIMEOUT
    assert calls[0]["max_attempts"] == cs.JEV_ATTEMPTS


async def test_timer_ask_does_not_retry_a_permanent_failure(monkeypatch):
    calls = []

    async def bad(org, crit, **kw):
        calls.append(1)
        raise jc.JevError("Jev: HTTP 400", status=400)

    monkeypatch.setattr(cs.jc, "ask_kind", bad)
    with pytest.raises(jc.JevError):
        await cs.bounded_ask(Backoff((0.0,)))("org", {}, api_key="k", model="m", client=None)
    assert calls == [1]


# --- DB-backed ------------------------------------------------------------


@pytest_asyncio.fixture(loop_scope="session")
async def db(db_pool):
    async with db_pool.acquire() as conn:
        tr = conn.transaction()
        await tr.start()
        try:
            yield conn
        finally:
            await tr.rollback()


async def _org(db, *slugs: str) -> str:
    oid = generate_id()
    await db.execute("INSERT INTO organizations (id) VALUES ($1)", oid)
    for slug in slugs:
        type_id = await db.fetchval("SELECT id FROM entity_identifier_types WHERE slug=$1", slug)
        await db.execute(
            "INSERT INTO identifiers (id, entity_id, entity_identifier_type_id, value)"
            " VALUES ($1,$2,$3,$4)",
            generate_id(),
            oid,
            type_id,
            f"v-{generate_id()[-8:]}",
        )
    return oid


async def _rules_rows(db, org_id) -> list[str]:
    rows = await db.fetch(
        "SELECT value FROM classification_suggestions"
        " WHERE entity_id=$1 AND source='rules' ORDER BY created_at, id",
        org_id,
    )
    return [r["value"] for r in rows]


@pytest.mark.integration
async def test_dry_run_reports_without_writing(db):
    party = await _org(db, "org_wa_party")
    report = await classify(db, source="rules", execute=False)
    assert party in {c.org_id for c in report.changes}
    assert report.counts["political_party"] >= 1
    assert report.written == 0
    assert await _rules_rows(db, party) == []


@pytest.mark.integration
async def test_execute_writes_once_and_reruns_clean(db):
    leg = await _org(db, "org_wa_legislature_committee_id")
    biz = await _org(db, "org_wslcb", "org_ubi")
    plain = await _org(db, "org_ubi")

    report = await classify(db, source="rules", execute=True)
    assert report.written == len(report.changes) >= 2
    assert await _rules_rows(db, leg) == ["legislature"]
    assert await _rules_rows(db, biz) == ["business"]
    assert await _rules_rows(db, plain) == []

    again = await classify(db, source="rules", execute=True)
    assert again.changes == []
    assert again.written == 0


@pytest.mark.integration
async def test_unknown_source_is_refused(db):
    with pytest.raises(ValueError, match="source"):
        await classify(db, source="magic", execute=False)


@pytest.fixture
def jev_on(monkeypatch):
    monkeypatch.setenv("JEV_SUGGESTIONS_ENABLED", "1")
    monkeypatch.setenv("TYPESAFE_API_KEY", "sk-test")
    monkeypatch.delenv("JEV_MODEL", raising=False)


def _fake_ask(asked):
    async def ask(org, crit, **kw):
        asked.append(org)
        return jc.JevAnswer("business", 0.9, {"business": 0.9}, kw["model"], 700)

    return ask


@pytest.mark.integration
async def test_all_dry_run_reports_the_backlog_without_asking(db, jev_on):
    await _org(db)
    await _org(db)
    asked: list = []
    report = await classify(db, source="all", execute=False, ask=_fake_ask(asked), limit=1)
    assert report.backlog >= 2
    assert report.would_ask == 1
    assert report.est_tokens == cs.EST_TOKENS_PER_ORG
    assert report.jev_enabled is True
    assert report.drain is None
    assert asked == []


@pytest.mark.integration
async def test_all_execute_drains(db, jev_on):
    oid = await _org(db, "org_wa_party")
    asked: list = []
    report = await classify(db, source="all", execute=True, ask=_fake_ask(asked), origin="timer")
    assert report.drain.status == "ok"
    assert report.drain.rules_written >= 1
    assert report.drain.answered == len(asked) >= 1
    run = await db.fetchrow(
        "SELECT origin FROM classification_runs ORDER BY finished_at DESC LIMIT 1"
    )
    assert run["origin"] == "timer"
    assert (
        await db.fetchval(
            "SELECT count(*) FROM classification_suggestions WHERE entity_id=$1 AND source='jev'",
            oid,
        )
        == 1
    )


@pytest.mark.integration
async def test_reask_ids_skip_decided_orgs(db, jev_on):
    decided = await _org(db)
    open_ = await _org(db)
    await cls.record_decision(db, org_id=decided, action="dismiss")
    asked: list = []
    dry = await classify(db, source="all", execute=False, reask_ids=[decided, open_])
    assert dry.would_ask == 1
    report = await classify(
        db, source="all", execute=True, ask=_fake_ask(asked), reask_ids=[decided, open_]
    )
    assert report.drain.answered == 1
    assert len(asked) == 1


@pytest.mark.integration
async def test_reask_ids_need_source_all(db):
    with pytest.raises(ValueError, match="--source all"):
        await classify(db, source="rules", execute=False, reask_ids=["x"])


async def _hold_lock(db_pool):
    other = await db_pool.acquire()
    assert await other.fetchval("SELECT pg_try_advisory_lock($1)", cls.DRAIN_LOCK_KEY)
    return other


async def _release_lock(db_pool, other):
    await other.execute("SELECT pg_advisory_unlock($1)", cls.DRAIN_LOCK_KEY)
    await db_pool.release(other)


@pytest.mark.integration
async def test_rules_execute_skips_while_a_drain_holds_the_lock(db, db_pool):
    party = await _org(db, "org_wa_party")
    other = await _hold_lock(db_pool)
    try:
        report = await classify(db, source="rules", execute=True)
    finally:
        await _release_lock(db_pool, other)
    assert report.busy is True
    assert report.written == 0
    assert await _rules_rows(db, party) == []


@pytest.mark.integration
async def test_reask_skips_rules_but_still_asks_while_the_lock_is_held(db, db_pool, jev_on):
    party = await _org(db, "org_wa_party")
    asked: list = []
    other = await _hold_lock(db_pool)
    try:
        report = await classify(
            db, source="all", execute=True, ask=_fake_ask(asked), reask_ids=[party]
        )
    finally:
        await _release_lock(db_pool, other)
    assert report.busy is True
    assert await _rules_rows(db, party) == []
    assert report.drain.answered == 1
