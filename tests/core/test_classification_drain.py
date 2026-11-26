"""Integration tests: the classification drain — rules, then Jev (#877 phase 2).

Jev is replaced by a fake ``ask`` keyed on the org's name; nothing here calls
the API. Each test runs in a rolled-back transaction, so the backlog is the
orgs the test creates.
"""

import pytest
import pytest_asyncio

from src.core import classification as cls
from src.core import jev_client as jc
from src.core.db import generate_id

pytestmark = [pytest.mark.integration]

MODEL = "jev-1.14.0"


@pytest.fixture(autouse=True)
def _jev_env(monkeypatch):
    monkeypatch.setenv("JEV_SUGGESTIONS_ENABLED", "1")
    monkeypatch.setenv("TYPESAFE_API_KEY", "sk-test")
    monkeypatch.delenv("JEV_MODEL", raising=False)
    monkeypatch.delenv("JEV_MAX_PER_RUN", raising=False)


@pytest_asyncio.fixture(loop_scope="session")
async def db(db_pool):
    async with db_pool.acquire() as conn:
        tr = conn.transaction()
        await tr.start()
        try:
            yield conn
        finally:
            await tr.rollback()


async def _org(db, name: str, *, archived: bool = False) -> str:
    oid = generate_id()
    await db.execute(
        "INSERT INTO organizations (id, archived_at) VALUES ($1, CASE WHEN $2 THEN NOW() END)",
        oid,
        archived,
    )
    await db.execute(
        "INSERT INTO organization_names (id, organization_id, name, is_canonical)"
        " VALUES ($1,$2,$3,TRUE)",
        generate_id(),
        oid,
        name,
    )
    return oid


def _answer(kind="business", confidence=0.9, tokens=700, model=MODEL):
    return jc.JevAnswer(
        kind=kind,
        confidence=confidence,
        probabilities={kind: confidence},
        model=model,
        input_tokens=tokens,
    )


class FakeJev:
    """Answers by org name: a JevAnswer, or an exception to raise."""

    def __init__(self, by_name=None, default=None):
        self.by_name = by_name or {}
        self.default = default if default is not None else _answer()
        self.asked: list[str] = []

    async def __call__(self, org, crit, **kw):
        self.asked.append(org.name)
        assert "none" in crit and "business" in crit
        assert kw["api_key"] == "sk-test"
        result = self.by_name.get(org.name, self.default)
        if isinstance(result, Exception):
            raise result
        return result


async def _jev_rows(db, org_id):
    return await db.fetch(
        "SELECT value, confidence, probabilities, model, input_tokens, ignored_at"
        " FROM classification_suggestions"
        " WHERE entity_id=$1 AND source='jev' ORDER BY created_at, id",
        org_id,
    )


async def _runs(db):
    return await db.fetch("SELECT * FROM classification_runs ORDER BY finished_at, id")


async def test_drain_asks_the_backlog_and_stores_answers(db):
    a = await _org(db, "Drain Alpha")
    b = await _org(db, "Drain Beta")
    await _org(db, "Drain Archived", archived=True)
    fake = FakeJev({"Drain Beta": _answer("nonprofit", 0.55, 640)})

    result = await cls.drain(db, origin="tick", ask=fake)

    assert sorted(fake.asked) == ["Drain Alpha", "Drain Beta"]
    assert (result.status, result.asked, result.answered, result.failed) == ("ok", 2, 2, 0)
    assert result.input_tokens == 1340
    (row,) = await _jev_rows(db, b)
    assert (row["value"], row["confidence"], row["model"], row["input_tokens"]) == (
        "nonprofit",
        0.55,
        MODEL,
        640,
    )
    assert row["probabilities"] is not None
    assert len(await _jev_rows(db, a)) == 1
    (run,) = await _runs(db)
    assert (run["origin"], run["status"], run["asked"], run["input_tokens"]) == (
        "tick",
        "ok",
        2,
        1340,
    )


async def test_drain_asks_each_org_once_per_model(db):
    await _org(db, "Once Only")
    fake = FakeJev()
    await cls.drain(db, origin="tick", ask=fake)
    second = await cls.drain(db, origin="tick", ask=fake)
    assert fake.asked == ["Once Only"]
    assert second.status == "idle"
    assert len(await _runs(db)) == 1  # an idle drain writes no run row


async def test_a_new_model_has_its_own_backlog(db, monkeypatch):
    oid = await _org(db, "Model Bump")
    await cls.drain(db, origin="tick", ask=FakeJev())
    monkeypatch.setenv("JEV_MODEL", "jev-2.0.0")
    await cls.drain(db, origin="tick", ask=FakeJev(default=_answer(model="jev-2.0.0")))
    assert [r["model"] for r in await _jev_rows(db, oid)] == [MODEL, "jev-2.0.0"]


async def test_flag_off_runs_rules_only(db, monkeypatch):
    monkeypatch.setenv("JEV_SUGGESTIONS_ENABLED", "0")
    oid = await _org(db, "Flag Off")
    type_id = await db.fetchval("SELECT id FROM entity_identifier_types WHERE slug='org_wa_party'")
    await db.execute(
        "INSERT INTO identifiers (id, entity_id, entity_identifier_type_id, value)"
        " VALUES ($1,$2,$3,'p')",
        generate_id(),
        oid,
        type_id,
    )
    fake = FakeJev()

    result = await cls.drain(db, origin="tick", ask=fake)

    assert result.status == "disabled"
    assert result.rules_written == 1
    assert fake.asked == []
    assert await _runs(db) == []


async def test_a_failed_org_stores_nothing_and_is_asked_next_time(db):
    ok = await _org(db, "Fine Org")
    bad = await _org(db, "Flaky Org")
    first = await cls.drain(
        db, origin="tick", ask=FakeJev({"Flaky Org": jc.JevError("Jev: HTTP 500", status=500)})
    )
    assert (first.status, first.answered, first.failed) == ("partial", 1, 1)
    assert await _jev_rows(db, bad) == []
    (run,) = await _runs(db)
    assert run["status"] == "partial"
    assert run["error"] == "Jev: HTTP 500"

    fake = FakeJev()
    await cls.drain(db, origin="tick", ask=fake)
    assert fake.asked == ["Flaky Org"]
    assert len(await _jev_rows(db, ok)) == 1


async def test_an_auth_failure_stops_the_run(db):
    for i in range(12):
        await _org(db, f"Auth Org {i:02d}")
    fake = FakeJev(default=jc.JevError("Jev: HTTP 401", status=401))
    result = await cls.drain(db, origin="timer", ask=fake)
    assert result.status == "failed"
    assert result.answered == 0
    assert len(fake.asked) < 12
    (run,) = await _runs(db)
    assert (run["status"], run["origin"], run["error"]) == ("failed", "timer", "Jev: HTTP 401")


async def test_a_missing_key_is_a_failed_run_without_a_call(db, monkeypatch):
    monkeypatch.setenv("TYPESAFE_API_KEY", "")
    await _org(db, "No Key")
    fake = FakeJev()
    result = await cls.drain(db, origin="tick", ask=fake)
    assert result.status == "failed"
    assert fake.asked == []
    (run,) = await _runs(db)
    assert "TYPESAFE_API_KEY" in run["error"]


async def test_limit_caps_the_orgs_asked(db):
    for i in range(5):
        await _org(db, f"Capped {i}")
    fake = FakeJev()
    result = await cls.drain(db, origin="wake", ask=fake, limit=3)
    assert result.asked == 3
    assert await cls.jev_backlog_count(db, MODEL) == 2


async def test_the_lock_keeps_a_second_drain_out(db, db_pool):
    await _org(db, "Locked Out")
    async with db_pool.acquire() as other:
        assert await other.fetchval("SELECT pg_try_advisory_lock($1)", cls.DRAIN_LOCK_KEY)
        try:
            fake = FakeJev()
            result = await cls.drain(db, origin="tick", ask=fake)
        finally:
            await other.execute("SELECT pg_advisory_unlock($1)", cls.DRAIN_LOCK_KEY)
    assert result.status == "busy"
    assert fake.asked == []
    # The drain releases its own lock: the next one runs.
    assert (await cls.drain(db, origin="tick", ask=FakeJev())).status == "ok"


async def test_reask_replaces_the_live_answer(db):
    oid = await _org(db, "Ask Again")
    await cls.drain(db, origin="tick", ask=FakeJev())
    result = await cls.reask(
        db, [oid], origin="reask", ask=FakeJev(default=_answer("coalition", 0.8))
    )
    assert result.answered == 1
    rows = await _jev_rows(db, oid)
    assert [(r["value"], r["ignored_at"] is None) for r in rows] == [
        ("business", False),
        ("coalition", True),
    ]


async def test_reask_never_changes_a_decided_kind(db):
    oid = await _org(db, "Decided Already")
    await cls.record_decision(db, org_id=oid, action="set", kind="association")
    await cls.reask(db, [oid], origin="reask", ask=FakeJev(default=_answer("business")))
    kind = await db.fetchval(
        "SELECT k.slug FROM organizations o JOIN org_kinds k ON k.id=o.org_kind_id WHERE o.id=$1",
        oid,
    )
    assert kind == "association"


async def test_refresh_can_skip_decided_orgs(db):
    decided = await _org(db, "Refresh Decided")
    open_ = await _org(db, "Refresh Open")
    await cls.record_decision(db, org_id=decided, action="dismiss")
    fake = FakeJev()
    await cls.reask(db, [decided, open_], origin="cli", ask=fake, skip_decided=True)
    assert fake.asked == ["Refresh Open"]


async def test_a_jev_none_is_stored_as_none(db):
    oid = await _org(db, "Not Really An Org")
    await cls.drain(db, origin="tick", ask=FakeJev(default=_answer("none", 0.8)))
    (row,) = await _jev_rows(db, oid)
    assert row["value"] == "none"


async def test_an_answer_from_another_model_is_a_failure(db):
    oid = await _org(db, "Model Drift")
    result = await cls.drain(db, origin="tick", ask=FakeJev(default=_answer(model="jev-9.9.9")))
    assert (result.status, result.failed) == ("failed", 1)
    assert await _jev_rows(db, oid) == []
    (run,) = await _runs(db)
    assert "jev-9.9.9" in run["error"]
