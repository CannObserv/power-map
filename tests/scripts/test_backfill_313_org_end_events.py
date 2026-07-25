"""Tests for the #313 org end-event backfill.

Covers the mechanics the audit deliberately won't do: closing *all* open
assignments (incl. ``unknown_end_on_ended`` — ``is_current=FALSE, end_date
NULL``) at the org's ``ended_on``, recording the bounding event, and
reactivating Kalytera. Idempotent on re-run.
"""

import datetime

import pytest
import pytest_asyncio

from scripts import backfill_313_org_end_events as bf
from src.core.db import generate_id

pytestmark = [pytest.mark.integration]

ENDED = datetime.date(2023, 6, 30)


@pytest_asyncio.fixture(loop_scope="session")
async def db(db_pool):
    async with db_pool.acquire() as conn:
        tr = conn.transaction()
        await tr.start()
        try:
            yield conn
        finally:
            await tr.rollback()


async def _seed_org(db, *, active=False):
    oid = generate_id()
    await db.execute("INSERT INTO organizations (id, active) VALUES ($1, $2)", oid, active)
    return oid


async def _seed_assignment(db, org_id, *, is_current=False, end_date=None, notes=None):
    rid = generate_id()
    await db.execute(
        "INSERT INTO roles (id, organization_id, title) VALUES ($1, $2, $3)",
        rid,
        org_id,
        f"Member {rid[-6:]}",
    )
    pid = generate_id()
    await db.execute("INSERT INTO people (id) VALUES ($1)", pid)
    aid = generate_id()
    await db.execute(
        """INSERT INTO role_assignments
               (id, person_id, role_id, is_current, end_date, notes)
           VALUES ($1, $2, $3, $4, $5, $6)""",
        aid,
        pid,
        rid,
        is_current,
        end_date,
        notes,
    )
    return aid


async def _assignment(db, aid):
    return await db.fetchrow(
        "SELECT is_current, end_date, notes FROM role_assignments WHERE id = $1", aid
    )


async def test_create_end_event_sets_lifespan(db):
    oid = await _seed_org(db)
    assert await bf._lifespan_event_exists(db, oid) is False
    await bf._create_end_event(db, oid, "dissolved", ENDED)
    assert await bf._lifespan_event_exists(db, oid) is True
    assert await bf._ended_on(db, oid) == ENDED


async def test_close_open_assignments_closes_current_and_unknown_end(db):
    oid = await _seed_org(db)
    cur = await _seed_assignment(db, oid, is_current=True)
    unknown = await _seed_assignment(db, oid, is_current=False)  # unknown-end
    already = await _seed_assignment(db, oid, end_date=datetime.date(2020, 1, 1))

    closed = await bf._close_open_assignments(db, oid, ENDED)

    assert set(closed) == {cur, unknown}  # the already-closed row is untouched
    for aid in (cur, unknown):
        row = await _assignment(db, aid)
        assert row["end_date"] == ENDED
        assert row["is_current"] is False
        assert bf.CLOSE_NOTE in row["notes"]
    # pre-existing end_date left as-is
    assert (await _assignment(db, already))["end_date"] == datetime.date(2020, 1, 1)


async def test_close_appends_to_existing_notes(db):
    oid = await _seed_org(db)
    aid = await _seed_assignment(db, oid, notes="prior note")
    await bf._close_open_assignments(db, oid, ENDED)
    notes = (await _assignment(db, aid))["notes"]
    assert notes.startswith("prior note")
    assert bf.CLOSE_NOTE in notes


async def test_run_backfill_end_to_end(db, monkeypatch):
    org = await _seed_org(db, active=False)
    a_cur = await _seed_assignment(db, org, is_current=True)
    a_unknown = await _seed_assignment(db, org, is_current=False)
    kalytera = await _seed_org(db, active=False)
    k_asg = await _seed_assignment(db, kalytera, is_current=False)

    monkeypatch.setattr(bf, "END_EVENTS", {org: ("dissolved", ENDED)})
    monkeypatch.setattr(bf, "KALYTERA_ID", kalytera)

    summary = await bf.run_backfill(db, execute=True)

    assert summary["events"] == [org]
    assert set(summary["closed"]) == {a_cur, a_unknown}
    assert summary["reactivated"] == [kalytera]
    assert await bf._ended_on(db, org) == ENDED
    for aid in (a_cur, a_unknown):
        assert (await _assignment(db, aid))["end_date"] == ENDED
    # Kalytera reactivated; its assignment stays open
    assert await db.fetchval("SELECT active FROM organizations WHERE id = $1", kalytera) is True
    assert (await _assignment(db, k_asg))["end_date"] is None


async def test_run_backfill_idempotent(db, monkeypatch):
    org = await _seed_org(db, active=False)
    await _seed_assignment(db, org, is_current=True)
    kalytera = await _seed_org(db, active=False)
    monkeypatch.setattr(bf, "END_EVENTS", {org: ("dissolved", ENDED)})
    monkeypatch.setattr(bf, "KALYTERA_ID", kalytera)

    first = await bf.run_backfill(db, execute=True)
    assert first["events"] == [org]
    assert first["reactivated"] == [kalytera]

    second = await bf.run_backfill(db, execute=True)
    # event already present, org already active, no open rows left → all empty
    assert second["events"] == []
    assert second["closed"] == []
    assert second["reactivated"] == []
    # still exactly one lifespan event (no duplicate)
    n = await db.fetchval(
        """SELECT count(*) FROM entity_events ev
           JOIN entity_event_types t ON t.id = ev.event_type_id
           WHERE ev.entity_id = $1 AND t.slug IN ('dissolved','merged_with')""",
        org,
    )
    assert n == 1


async def test_report_mode_is_readonly(db, monkeypatch):
    org = await _seed_org(db, active=False)
    aid = await _seed_assignment(db, org, is_current=True)
    kalytera = await _seed_org(db, active=False)
    monkeypatch.setattr(bf, "END_EVENTS", {org: ("dissolved", ENDED)})
    monkeypatch.setattr(bf, "KALYTERA_ID", kalytera)

    summary = await bf.run_backfill(db, execute=False)

    assert summary["events"] == [org]
    assert summary["closed"] == [aid]
    assert summary["reactivated"] == [kalytera]
    # nothing actually changed
    assert await bf._lifespan_event_exists(db, org) is False
    assert (await _assignment(db, aid))["end_date"] is None
    assert await db.fetchval("SELECT active FROM organizations WHERE id = $1", kalytera) is False
