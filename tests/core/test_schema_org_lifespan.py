"""Integration tests for the v_org_lifespan view (#307).

An org's lifespan end (``ended_on``) derives from its earliest non-archived
``dissolved`` / ``merged_with`` entity event, resolved to the *latest* date
within the event's known precision (year-only 2023 → 2023-12-31) so that
closing assignments at ``ended_on`` never invents an earlier end than the
source supports.
"""

import datetime

import asyncpg
import pytest
import pytest_asyncio

from src.core.db import generate_id

pytestmark = [
    pytest.mark.integration,
]


@pytest_asyncio.fixture(loop_scope="session")
async def conn(db_pool):
    """Pool-acquired connection wrapped in a rolled-back transaction."""
    async with db_pool.acquire() as c:
        tr = c.transaction()
        await tr.start()
        try:
            yield c
        finally:
            await tr.rollback()


async def _insert_org(conn):
    oid = generate_id()
    await conn.execute("INSERT INTO organizations (id) VALUES ($1)", oid)
    return oid


async def _insert_event(conn, org_id, slug, year=None, month=None, day=None, archived=False):
    eid = generate_id()
    await conn.execute(
        """INSERT INTO entity_events
               (id, entity_type, entity_id, event_type_id,
                event_year, event_month, event_day, archived_at)
           SELECT $1, 'organization', $2, t.id, $4, $5, $6,
                  CASE WHEN $7 THEN NOW() END
           FROM entity_event_types t WHERE t.slug = $3""",
        eid,
        org_id,
        slug,
        year,
        month,
        day,
        archived,
    )
    return eid


async def _ended_on(conn, org_id):
    return await conn.fetchval(
        "SELECT ended_on FROM v_org_lifespan WHERE organization_id = $1", org_id
    )


async def test_full_date_dissolved_event(conn):
    oid = await _insert_org(conn)
    await _insert_event(conn, oid, "dissolved", 2023, 1, 9)
    assert await _ended_on(conn, oid) == datetime.date(2023, 1, 9)


async def test_year_only_resolves_to_dec_31(conn):
    oid = await _insert_org(conn)
    await _insert_event(conn, oid, "dissolved", 2023)
    assert await _ended_on(conn, oid) == datetime.date(2023, 12, 31)


async def test_month_precision_resolves_to_last_day_of_month(conn):
    oid = await _insert_org(conn)
    await _insert_event(conn, oid, "dissolved", 2024, 2)
    assert await _ended_on(conn, oid) == datetime.date(2024, 2, 29)


async def test_org_without_end_event_has_no_row(conn):
    oid = await _insert_org(conn)
    await _insert_event(conn, oid, "founded", 2020)
    await _insert_event(conn, oid, "renamed", 2022)
    assert await _ended_on(conn, oid) is None


async def test_archived_end_event_is_ignored(conn):
    oid = await _insert_org(conn)
    await _insert_event(conn, oid, "dissolved", 2023, 1, 9, archived=True)
    assert await _ended_on(conn, oid) is None


async def test_merged_with_counts_as_end(conn):
    oid = await _insert_org(conn)
    await _insert_event(conn, oid, "merged_with", 2021, 6, 30)
    assert await _ended_on(conn, oid) == datetime.date(2021, 6, 30)


async def test_merged_with_without_year_is_excluded(conn):
    """merged_with does not require a year; a dateless one derives no bound."""
    oid = await _insert_org(conn)
    await _insert_event(conn, oid, "merged_with")
    assert await _ended_on(conn, oid) is None


async def test_multiple_end_events_take_earliest(conn):
    oid = await _insert_org(conn)
    await _insert_event(conn, oid, "merged_with", 2023, 1, 9)
    await _insert_event(conn, oid, "dissolved", 2024)
    assert await _ended_on(conn, oid) == datetime.date(2023, 1, 9)


async def test_one_row_per_org(conn):
    oid = await _insert_org(conn)
    await _insert_event(conn, oid, "dissolved", 2023, 1, 9)
    await _insert_event(conn, oid, "merged_with", 2023, 1, 9)
    rows = await conn.fetch("SELECT * FROM v_org_lifespan WHERE organization_id = $1", oid)
    assert len(rows) == 1


async def test_year_zero_event_rejected(conn):
    """Year 0 doesn't exist in the Gregorian calendar and make_date() errors on
    it — the CHECK must reject it so one bad row can't break v_org_lifespan."""
    oid = await _insert_org(conn)
    with pytest.raises(asyncpg.CheckViolationError):
        await _insert_event(conn, oid, "dissolved", 0)
