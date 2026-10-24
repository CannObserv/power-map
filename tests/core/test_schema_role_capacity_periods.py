"""Integration tests for seat capacity and membership type (#671).

`role_capacity_periods` holds how many principals a role seats at once, per
period; `role_assignments.membership_type` tells a substitute from the
principal it serves under. Both feed the D7 seat-occupancy audit.
"""

import datetime

import asyncpg
import pytest
import pytest_asyncio

from src.core.db import generate_id

pytestmark = [pytest.mark.integration]

D = datetime.date


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


async def _role(conn) -> str:
    oid, rid = generate_id(), generate_id()
    await conn.execute("INSERT INTO organizations (id) VALUES ($1)", oid)
    await conn.execute(
        "INSERT INTO roles (id, organization_id, title) VALUES ($1, $2, 'Senator')", rid, oid
    )
    return rid


async def _period(conn, role_id, valid_from, valid_to=None, max_holders=None) -> str:
    pid = generate_id()
    await conn.execute(
        """INSERT INTO role_capacity_periods (id, role_id, valid_from, valid_to, max_holders)
           VALUES ($1, $2, $3, $4, $5)""",
        pid,
        role_id,
        valid_from,
        valid_to,
        max_holders,
    )
    return pid


async def _assignment(conn, role_id, membership_type=None) -> str:
    pid, aid = generate_id(), generate_id()
    await conn.execute("INSERT INTO people (id) VALUES ($1)", pid)
    if membership_type is None:
        await conn.execute(
            "INSERT INTO role_assignments (id, person_id, role_id) VALUES ($1, $2, $3)",
            aid,
            pid,
            role_id,
        )
    else:
        await conn.execute(
            """INSERT INTO role_assignments (id, person_id, role_id, membership_type)
               VALUES ($1, $2, $3, $4)""",
            aid,
            pid,
            role_id,
            membership_type,
        )
    return aid


# --------------------------------------------------------------------------
# role_capacity_periods
# --------------------------------------------------------------------------


async def test_a_period_stores_an_open_end_and_no_limit(conn):
    """Null `valid_to` is an open period, null `max_holders` no limit (usa-wa D1)."""
    rid = await _role(conn)
    pid = await _period(conn, rid, D(1891, 1, 1))

    row = await conn.fetchrow("SELECT * FROM role_capacity_periods WHERE id = $1", pid)

    assert row["valid_to"] is None and row["max_holders"] is None
    assert row["archived_at"] is None and row["updated_at"] is not None


async def test_a_one_day_period_is_legal(conn):
    """`valid_to` is the inclusive last day, so equal bounds are one day, not empty."""
    rid = await _role(conn)
    await _period(conn, rid, D(1889, 1, 1), D(1889, 1, 1), 1)


async def test_a_period_ending_before_it_starts_is_refused(conn):
    rid = await _role(conn)
    with pytest.raises(asyncpg.CheckViolationError, match="chk_capacity_period_range"):
        await _period(conn, rid, D(1891, 1, 1), D(1890, 12, 31), 1)


@pytest.mark.parametrize("bad", [0, -1])
async def test_a_capacity_below_one_is_refused(conn, bad):
    """No limit is null, never 0: a seat that holds nobody is not a period."""
    rid = await _role(conn)
    with pytest.raises(asyncpg.CheckViolationError, match="chk_capacity_period_max"):
        await _period(conn, rid, D(1889, 1, 1), D(1890, 12, 31), bad)


async def test_one_live_period_per_role_and_start(conn):
    rid = await _role(conn)
    await _period(conn, rid, D(1889, 1, 1), D(1890, 12, 31), 5)

    with pytest.raises(asyncpg.UniqueViolationError, match="uq_role_capacity_period_start"):
        await _period(conn, rid, D(1889, 1, 1), D(1890, 12, 31), 3)


async def test_an_archived_period_frees_its_start(conn):
    """The applier archives a re-tiled period, then inserts at the same start."""
    rid = await _role(conn)
    old = await _period(conn, rid, D(1889, 1, 1), D(1890, 12, 31), 5)
    await conn.execute("UPDATE role_capacity_periods SET archived_at = NOW() WHERE id = $1", old)

    await _period(conn, rid, D(1889, 1, 1), D(1890, 12, 31), 3)


async def test_a_period_needs_a_real_role(conn):
    with pytest.raises(asyncpg.ForeignKeyViolationError):
        await _period(conn, generate_id(), D(1889, 1, 1))


async def test_deleting_a_role_deletes_its_periods(conn):
    """The admin role merge hard-deletes the loser; its periods go with it."""
    rid = await _role(conn)
    pid = await _period(conn, rid, D(1889, 1, 1), D(1890, 12, 31), 5)

    await conn.execute("DELETE FROM roles WHERE id = $1", rid)

    assert await conn.fetchval("SELECT count(*) FROM role_capacity_periods WHERE id = $1", pid) == 0


async def test_updated_at_is_maintained_by_the_trigger(conn):
    rid = await _role(conn)
    pid = await _period(conn, rid, D(1889, 1, 1), D(1890, 12, 31), 5)
    await conn.execute(
        "UPDATE role_capacity_periods SET updated_at = '2000-01-01' WHERE id = $1", pid
    )

    await conn.execute("UPDATE role_capacity_periods SET max_holders = 3 WHERE id = $1", pid)

    assert await conn.fetchval(
        "SELECT updated_at > '2000-01-02' FROM role_capacity_periods WHERE id = $1", pid
    )


# --------------------------------------------------------------------------
# role_assignments.membership_type
# --------------------------------------------------------------------------


async def test_membership_type_defaults_to_principal(conn):
    """Every row a writer creates without saying is the role's own holder."""
    aid = await _assignment(conn, await _role(conn))

    assert (
        await conn.fetchval("SELECT membership_type FROM role_assignments WHERE id = $1", aid)
        == "principal"
    )


@pytest.mark.parametrize("value", ["principal", "substitute", "ex_officio", "acting"])
async def test_membership_type_takes_the_closed_vocabulary(conn, value):
    await _assignment(conn, await _role(conn), value)


@pytest.mark.parametrize("bad", ["Substitute", "deputy", ""])
async def test_membership_type_refuses_anything_else(conn, bad):
    with pytest.raises(asyncpg.CheckViolationError, match="chk_ra_membership_type"):
        await _assignment(conn, await _role(conn), bad)


async def test_membership_type_is_never_null(conn):
    aid = await _assignment(conn, await _role(conn))
    with pytest.raises(asyncpg.NotNullViolationError):
        await conn.execute("UPDATE role_assignments SET membership_type = NULL WHERE id = $1", aid)
