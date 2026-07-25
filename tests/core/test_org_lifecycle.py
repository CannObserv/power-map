"""Integration tests for src.core.org_lifecycle (#307).

``check_assignment_lifespan`` guards assignment writes against the org
lifespan invariant; unknown-end rows (not current, no end date) are allowed —
the audit script warns on those instead of the write path rejecting them.
"""

import datetime

import pytest
import pytest_asyncio

from src.core.db import generate_id
from src.core.org_lifecycle import (
    AssignmentOutsideOrgLifespan,
    check_assignment_lifespan,
    lifespan_error_message,
    resolve_lifespan_banner,
)

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


async def _seed_role(db, *, ended_on=None):
    oid = generate_id()
    await db.execute("INSERT INTO organizations (id) VALUES ($1)", oid)
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
    rid = generate_id()
    await db.execute(
        "INSERT INTO roles (id, organization_id, title) VALUES ($1, $2, 'Member')",
        rid,
        oid,
    )
    return rid


async def test_unended_org_allows_current(db):
    rid = await _seed_role(db)
    await check_assignment_lifespan(db, rid, is_current=True, start_date=None, end_date=None)


async def test_current_on_ended_rejected(db):
    rid = await _seed_role(db, ended_on=ENDED)
    with pytest.raises(AssignmentOutsideOrgLifespan) as exc:
        await check_assignment_lifespan(db, rid, is_current=True, start_date=None, end_date=None)
    assert exc.value.code == "current_on_ended"
    assert exc.value.ended_on == ENDED


async def test_end_after_ended_rejected(db):
    rid = await _seed_role(db, ended_on=ENDED)
    with pytest.raises(AssignmentOutsideOrgLifespan) as exc:
        await check_assignment_lifespan(
            db, rid, is_current=False, start_date=None, end_date=datetime.date(2024, 6, 1)
        )
    assert exc.value.code == "end_after_ended"


async def test_start_after_ended_rejected_with_precedence(db):
    rid = await _seed_role(db, ended_on=ENDED)
    with pytest.raises(AssignmentOutsideOrgLifespan) as exc:
        await check_assignment_lifespan(
            db, rid, is_current=True, start_date=datetime.date(2024, 2, 1), end_date=None
        )
    assert exc.value.code == "start_after_ended"


async def test_window_within_lifespan_allowed(db):
    rid = await _seed_role(db, ended_on=ENDED)
    await check_assignment_lifespan(
        db,
        rid,
        is_current=False,
        start_date=datetime.date(2020, 1, 1),
        end_date=datetime.date(2022, 12, 31),
    )


async def test_unknown_end_on_ended_org_allowed(db):
    """Not current + no end date = unknown end; write allowed, audit warns."""
    rid = await _seed_role(db, ended_on=ENDED)
    await check_assignment_lifespan(
        db, rid, is_current=False, start_date=datetime.date(2020, 1, 1), end_date=None
    )


async def test_end_on_boundary_allowed(db):
    rid = await _seed_role(db, ended_on=ENDED)
    await check_assignment_lifespan(db, rid, is_current=False, start_date=None, end_date=ENDED)


async def test_error_messages_name_the_boundary(db):
    rid = await _seed_role(db, ended_on=ENDED)
    with pytest.raises(AssignmentOutsideOrgLifespan) as exc:
        await check_assignment_lifespan(db, rid, is_current=True, start_date=None, end_date=None)
    assert "2023-01-09" in lifespan_error_message(exc.value)


# ---------------------------------------------------------------------------
# resolve_lifespan_banner — shared gating for detail GET + active toggle (#320)
# ---------------------------------------------------------------------------


async def _seed_org_with_open_assignment(db, *, active=True, ended_on=None):
    """Seed an org (with an open assignment) and return its full record."""
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
    rid = generate_id()
    await db.execute(
        "INSERT INTO roles (id, organization_id, title) VALUES ($1, $2, 'Member')", rid, oid
    )
    pid = generate_id()
    await db.execute("INSERT INTO people (id) VALUES ($1)", pid)
    await db.execute(
        "INSERT INTO role_assignments (id, person_id, role_id) VALUES ($1, $2, $3)",
        generate_id(),
        pid,
        rid,
    )
    return await db.fetchrow("SELECT * FROM organizations WHERE id = $1", oid)


async def test_resolve_banner_active_unended_is_clear(db):
    """Active org with no lifespan end: open assignments are fine → no banner."""
    org = await _seed_org_with_open_assignment(db, active=True)
    count, ended_on = await resolve_lifespan_banner(db, org)
    assert count == 0
    assert ended_on is None


async def test_resolve_banner_inactive_counts(db):
    """Inactive org surfaces its open assignments."""
    org = await _seed_org_with_open_assignment(db, active=False)
    count, ended_on = await resolve_lifespan_banner(db, org)
    assert count == 1
    assert ended_on is None


async def test_resolve_banner_active_but_ended_counts(db):
    """Active-but-ended org still surfaces open assignments (gating edge)."""
    org = await _seed_org_with_open_assignment(db, active=True, ended_on=ENDED)
    count, ended_on = await resolve_lifespan_banner(db, org)
    assert count == 1
    assert ended_on == ENDED
