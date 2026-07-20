"""Tests for the org-lifecycle assignment audit (#307).

Invariant: an assignment's window must fall within its org's lifespan
(``v_org_lifespan.ended_on``). Findings:

- ``current_on_ended`` — ``is_current=TRUE`` on an ended org; the only
  auto-fixable class: ``--execute`` closes it at ``ended_on``.
- ``end_after_ended`` / ``start_after_ended`` — dated contradictions;
  report only.
- ``unknown_end_on_ended`` — ``end_date NULL, is_current=FALSE`` on an ended
  org; unknown end is not invented, report only.
- ``missing_end_event`` — org inactive/archived with open assignments but no
  end event to bound them; needs a human-supplied dissolved/merged_with event.
"""

import datetime

import pytest
import pytest_asyncio

from scripts.audit_org_lifecycle_assignments import audit_org_lifecycle, run_audit
from src.core.db import generate_id

pytestmark = [
    pytest.mark.integration,
]

ENDED = datetime.date(2023, 1, 9)


@pytest_asyncio.fixture(loop_scope="session")
async def db(db_pool):
    async with db_pool.acquire() as conn:
        tr = conn.transaction()
        await tr.start()
        try:
            yield conn
        finally:
            await tr.rollback()


async def _seed_org(db, *, active=True, ended_on=None):
    oid = generate_id()
    await db.execute("INSERT INTO organizations (id, active) VALUES ($1, $2)", oid, active)
    if ended_on:
        await db.execute(
            """INSERT INTO entity_events
                   (id, entity_type, entity_id, event_type_id,
                    event_year, event_month, event_day)
               SELECT $1, 'organization', $2, t.id, $3, $4, $5
               FROM entity_event_types t WHERE t.slug = 'dissolved'""",
            generate_id(),
            oid,
            ended_on.year,
            ended_on.month,
            ended_on.day,
        )
    return oid


async def _seed_assignment(
    db, org_id, *, is_current=False, start_date=None, end_date=None, notes=None
):
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
               (id, person_id, role_id, is_current, start_date, end_date, notes)
           VALUES ($1, $2, $3, $4, $5, $6, $7)""",
        aid,
        pid,
        rid,
        is_current,
        start_date,
        end_date,
        notes,
    )
    return aid


def _ids(findings, category):
    return {f["assignment_id"] for f in findings[category]}


async def test_current_on_ended_is_flagged(db):
    org = await _seed_org(db, active=False, ended_on=ENDED)
    aid = await _seed_assignment(db, org, is_current=True)
    findings = await audit_org_lifecycle(db)
    assert aid in _ids(findings, "current_on_ended")


async def test_end_after_ended_is_flagged(db):
    org = await _seed_org(db, ended_on=ENDED)
    aid = await _seed_assignment(db, org, end_date=datetime.date(2024, 6, 1))
    findings = await audit_org_lifecycle(db)
    assert aid in _ids(findings, "end_after_ended")


async def test_start_after_ended_is_flagged_not_closable(db):
    org = await _seed_org(db, ended_on=ENDED)
    aid = await _seed_assignment(db, org, is_current=True, start_date=datetime.date(2024, 2, 1))
    findings = await audit_org_lifecycle(db)
    assert aid in _ids(findings, "start_after_ended")
    assert aid not in _ids(findings, "current_on_ended")


async def test_unknown_end_on_ended_is_warned(db):
    org = await _seed_org(db, ended_on=ENDED)
    aid = await _seed_assignment(db, org, is_current=False, end_date=None)
    findings = await audit_org_lifecycle(db)
    assert aid in _ids(findings, "unknown_end_on_ended")


async def test_missing_end_event_for_inactive_org_with_open_assignments(db):
    org = await _seed_org(db, active=False)
    await _seed_assignment(db, org, is_current=True)
    findings = await audit_org_lifecycle(db)
    assert org in {f["organization_id"] for f in findings["missing_end_event"]}


async def test_compliant_assignment_not_flagged(db):
    org = await _seed_org(db, ended_on=ENDED)
    aid = await _seed_assignment(
        db, org, start_date=datetime.date(2020, 1, 1), end_date=datetime.date(2022, 12, 31)
    )
    findings = await audit_org_lifecycle(db)
    for category in findings:
        assert aid not in _ids(findings, category)


async def test_active_unended_org_not_flagged(db):
    org = await _seed_org(db, active=True)
    await _seed_assignment(db, org, is_current=True)
    findings = await audit_org_lifecycle(db)
    for category, rows in findings.items():
        assert not rows, category


async def test_execute_closes_current_on_ended_at_ended_on(db):
    org = await _seed_org(db, active=False, ended_on=ENDED)
    aid = await _seed_assignment(db, org, is_current=True, notes="existing note")
    await run_audit(db, execute=True)
    row = await db.fetchrow(
        "SELECT is_current, end_date, notes FROM role_assignments WHERE id = $1", aid
    )
    assert row["is_current"] is False
    assert row["end_date"] == ENDED
    assert "existing note" in row["notes"]
    assert "#307" in row["notes"]


async def test_dry_run_changes_nothing(db):
    org = await _seed_org(db, active=False, ended_on=ENDED)
    aid = await _seed_assignment(db, org, is_current=True)
    await run_audit(db, execute=False)
    row = await db.fetchrow("SELECT is_current, end_date FROM role_assignments WHERE id = $1", aid)
    assert row["is_current"] is True
    assert row["end_date"] is None


async def test_execute_does_not_touch_contradictions(db):
    org = await _seed_org(db, ended_on=ENDED)
    start_after = await _seed_assignment(
        db, org, is_current=True, start_date=datetime.date(2024, 2, 1)
    )
    unknown_end = await _seed_assignment(db, org, is_current=False, end_date=None)
    await run_audit(db, execute=True)
    row = await db.fetchrow(
        "SELECT is_current, end_date FROM role_assignments WHERE id = $1", start_after
    )
    assert row["is_current"] is True and row["end_date"] is None
    row = await db.fetchrow("SELECT end_date FROM role_assignments WHERE id = $1", unknown_end)
    assert row["end_date"] is None


async def test_archived_assignments_and_roles_ignored(db):
    org = await _seed_org(db, active=False, ended_on=ENDED)
    aid = await _seed_assignment(db, org, is_current=True)
    await db.execute("UPDATE role_assignments SET archived_at = NOW() WHERE id = $1", aid)
    findings = await audit_org_lifecycle(db)
    for category in ("current_on_ended", "unknown_end_on_ended"):
        assert aid not in _ids(findings, category)
