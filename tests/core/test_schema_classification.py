"""Integration tests: the org-kind vocabulary and the suggestion store (#877).

`org_kinds` is the seeded lookup of what an organization *is* (11 kinds, decided
by the operator 2026-11-24). `organizations.org_kind_id` is the resolved kind,
written only by an operator decision. `classification_suggestions` is the
append-only store every source's answer goes into — rules, Jev and manual —
shared with #303's role-type suggestions.
"""

import re
from pathlib import Path

import asyncpg
import pytest
import pytest_asyncio

from src.core.db import generate_id

pytestmark = [pytest.mark.integration]

ORG_KINDS = (
    "business",
    "nonprofit",
    "association",
    "coalition",
    "labor_union",
    "government",
    "legislature",
    "political_party",
    "campaign_committee",
    "tribe",
    "academic",
)


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


async def _suggest(db, **cols) -> str:
    row = {
        "id": generate_id(),
        "entity_type": "organization",
        "entity_id": generate_id(),
        "field": "org_kind",
        "source": "rules",
        "value": "business",
        "model": "rules-1",
    } | cols
    keys = list(row)
    await db.execute(
        f"INSERT INTO classification_suggestions ({', '.join(keys)})"
        f" VALUES ({', '.join(f'${i + 1}' for i in range(len(keys)))})",
        *row.values(),
    )
    return row["id"]


# --- org_kinds ---


async def test_org_kinds_seeded(db):
    rows = await db.fetch("SELECT slug, display_name, description FROM org_kinds ORDER BY slug")
    assert {r["slug"] for r in rows} == set(ORG_KINDS)
    for r in rows:
        assert r["display_name"].strip()
        # The description is the Jev criterion text (design D1): never empty.
        assert r["description"].strip()


async def test_org_kind_slug_unique(db):
    with pytest.raises(asyncpg.UniqueViolationError):
        await db.execute(
            "INSERT INTO org_kinds (id, slug, display_name, description) VALUES ($1,$2,$3,$4)",
            generate_id(),
            "business",
            "Dup",
            "dup",
        )


# --- organizations.org_kind_id ---


async def test_organization_kind_defaults_to_undecided(db):
    oid = await _org(db)
    assert await db.fetchval("SELECT org_kind_id FROM organizations WHERE id=$1", oid) is None


async def test_organization_kind_references_org_kinds(db):
    oid = await _org(db)
    with pytest.raises(asyncpg.ForeignKeyViolationError):
        await db.execute(
            "UPDATE organizations SET org_kind_id=$1 WHERE id=$2", "01KZNOTAKIND00000000000000", oid
        )


# --- classification_suggestions ---


@pytest.mark.parametrize(
    "cols",
    [
        {"entity_type": "person"},
        {"field": "colour"},
        {"source": "guess"},
        # manual_action belongs to manual rows only, and a manual row needs one.
        {"manual_action": "accept"},
        {"source": "manual", "model": None},
        {"source": "manual", "model": None, "manual_action": "approve"},
        # accepted_source (#877 phase 3): exactly on an accept, rules or jev only.
        {"source": "manual", "model": None, "manual_action": "accept"},
        {"source": "manual", "model": None, "manual_action": "accept", "accepted_source": "x"},
        {"source": "manual", "model": None, "manual_action": "set", "accepted_source": "rules"},
        {"accepted_source": "rules"},
    ],
)
async def test_suggestion_checks_reject_bad_rows(db, cols):
    with pytest.raises(asyncpg.CheckViolationError):
        await _suggest(db, **cols)


@pytest.mark.parametrize("action", ["accept", "set", "dismiss", "clear"])
async def test_manual_row_takes_each_action(db, action):
    value = None if action in ("dismiss", "clear") else "business"
    accepted = "rules" if action == "accept" else None
    sid = await _suggest(
        db, source="manual", model=None, manual_action=action, value=value, accepted_source=accepted
    )
    assert (
        await db.fetchval("SELECT manual_action FROM classification_suggestions WHERE id=$1", sid)
        == action
    )


async def test_one_live_jev_answer_per_model(db):
    """Classify once: a second live Jev row for the same model is refused (D3)."""
    eid = generate_id()
    first = await _suggest(db, entity_id=eid, source="jev", model="jev-1.14.0", confidence=0.91)
    with pytest.raises(asyncpg.UniqueViolationError):
        async with db.transaction():
            await _suggest(db, entity_id=eid, source="jev", model="jev-1.14.0", confidence=0.5)
    # A re-ask marks the old answer ignored first; then a new live row fits.
    await db.execute("UPDATE classification_suggestions SET ignored_at=NOW() WHERE id=$1", first)
    await _suggest(db, entity_id=eid, source="jev", model="jev-1.14.0", confidence=0.6)
    # Another model is another answer.
    await _suggest(db, entity_id=eid, source="jev", model="jev-1.15.0", confidence=0.7)


async def test_rules_rows_are_not_limited_to_one(db):
    """Rules history is append-only: a changed answer is a new row."""
    eid = generate_id()
    await _suggest(db, entity_id=eid, value="business")
    await _suggest(db, entity_id=eid, value="none", note="identifier removed")
    n = await db.fetchval("SELECT count(*) FROM classification_suggestions WHERE entity_id=$1", eid)
    assert n == 2


async def test_role_type_field_is_accepted_for_roles(db):
    """The store is shared with #303's role-type suggestions."""
    await _suggest(db, entity_type="role", field="role_type", value="org_lobbyist")


async def test_created_at_is_per_statement_clock(db):
    """CR 1: NOW() is the transaction's start, so rows written in one transaction would
    tie and fall back to a non-monotonic ULID; clock_timestamp() orders them by write."""
    default = await db.fetchval(
        "SELECT column_default FROM information_schema.columns"
        " WHERE table_name='classification_suggestions' AND column_name='created_at'"
    )
    assert default == "clock_timestamp()"


def _backfill_sql() -> str:
    """schema.sql's accepted_source backfill, verbatim."""
    text = Path("src/core/schema.sql").read_text()
    m = re.search(
        r"UPDATE classification_suggestions\s+SET accepted_source = .*?;", text, re.DOTALL
    )
    assert m, "the accepted_source backfill is gone from schema.sql"
    return m.group(0)


async def test_accepted_source_backfill_reads_the_accept_note(db):
    """CR 3: a backfill that missed would leave the CHECK unadded, with only a deploy WARNING."""
    await db.execute(
        "ALTER TABLE classification_suggestions DROP CONSTRAINT chk_suggestion_accepted_source"
    )
    manual = {"source": "manual", "model": None, "manual_action": "accept"}
    by_rules = await _suggest(db, **manual, note="accepted rules")
    by_jev = await _suggest(db, **manual, note="accepted jev")
    other = await _suggest(db, **manual, note="accepted rules, then checked")
    await db.execute(_backfill_sql())
    rows = await db.fetch(
        "SELECT id, accepted_source FROM classification_suggestions WHERE id = ANY($1::text[])",
        [by_rules, by_jev, other],
    )
    assert {r["id"]: r["accepted_source"] for r in rows} == {
        by_rules: "rules",
        by_jev: "jev",
        other: None,
    }


# --- classification_runs (#877 phase 2) ----------------------------------


async def _run(db, **cols) -> str:
    row = {
        "id": generate_id(),
        "field": "org_kind",
        "origin": "tick",
        "status": "ok",
        "model": "jev-1.14.0",
        "asked": 2,
        "answered": 2,
        "started_at": None,
    } | cols
    if row["started_at"] is None:
        row["started_at"] = await db.fetchval("SELECT clock_timestamp()")
    keys = list(row)
    await db.execute(
        f"INSERT INTO classification_runs ({', '.join(keys)})"  # noqa: S608
        f" VALUES ({', '.join(f'${i + 1}' for i in range(len(keys)))})",
        *row.values(),
    )
    return row["id"]


async def test_run_row_accepts_each_origin_and_status(db):
    for origin in ("wake", "tick", "cli", "timer", "reask"):
        for status in ("ok", "partial", "failed"):
            await _run(db, origin=origin, status=status)


@pytest.mark.parametrize(
    "cols",
    [
        {"field": "colour"},
        {"origin": "cron"},
        {"status": "busy"},
        {"asked": 1, "answered": 1, "failed": 1},
        {"input_tokens": -1},
    ],
)
async def test_run_checks_reject_bad_rows(db, cols):
    with pytest.raises(asyncpg.CheckViolationError):
        async with db.transaction():
            await _run(db, **cols)
