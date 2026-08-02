"""Tests for the assignment-relationship backfill (#301).

Uses synthetic targets (the real ones are prod role ids). Verifies clean
resolution + mint, and that every ambiguity/miss is reported, never guessed.
"""

import datetime

import pytest
import pytest_asyncio

from scripts.backfill_assignment_relationships import Target, resolve, run_backfill
from src.core.db import generate_id

pytestmark = [pytest.mark.integration]

D = datetime.date


@pytest_asyncio.fixture(loop_scope="session")
async def conn(db_pool):
    async with db_pool.acquire() as c:
        tr = c.transaction()
        await tr.start()
        try:
            yield c
        finally:
            await tr.rollback()


async def _person(conn, name):
    pid = generate_id()
    await conn.execute("INSERT INTO people (id) VALUES ($1)", pid)
    await conn.execute(
        "INSERT INTO person_names (id, person_id, name, is_canonical) VALUES ($1,$2,$3,TRUE)",
        generate_id(),
        pid,
        name,
    )
    return pid


async def _org(conn):
    oid = generate_id()
    await conn.execute("INSERT INTO organizations (id) VALUES ($1)", oid)
    return oid


async def _jurisdiction(conn):
    jid = generate_id()
    await conn.execute(
        "INSERT INTO jurisdictions (id, slug, name, type_id)"
        " SELECT $1, $2, 'LD 38', t.id FROM jurisdiction_types t LIMIT 1",
        jid,
        f"ld-38-{jid}",
    )
    return jid


async def _seat_role(conn, org, qualifier):
    """A state_senator seat role (typed → needs a jurisdiction + qualifier)."""
    rid = generate_id()
    jid = await _jurisdiction(conn)
    await conn.execute(
        """INSERT INTO roles (id, organization_id, title, role_type_id, jurisdiction_id, qualifier)
           SELECT $1,$2,$3, rt.id, $4, $5 FROM role_types rt WHERE rt.slug='state_senator'""",
        rid,
        org,
        "State Senator",
        jid,
        qualifier,
    )
    return rid


async def _plain_role(conn, org, title):
    rid = generate_id()
    await conn.execute(
        "INSERT INTO roles (id, organization_id, title) VALUES ($1,$2,$3)", rid, org, title
    )
    return rid


async def _assign(conn, person, role, start=None, end=None):
    aid = generate_id()
    await conn.execute(
        "INSERT INTO role_assignments (id, person_id, role_id, start_date, end_date, is_current)"
        " VALUES ($1,$2,$3,$4,$5,$6)",
        aid,
        person,
        role,
        start,
        end,
        end is None,
    )
    return aid


async def _scenario(conn):
    """Staffer role+assignment, principal person with an overlapping seat."""
    org = await _org(conn)
    staffer_person = await _person(conn, "Kate Armstrong")
    staffer_role = await _plain_role(conn, org, "Legislative Aide, Senator June Robinson")
    staffer_asg = await _assign(conn, staffer_person, staffer_role, start=D(2023, 1, 1))

    principal = await _person(conn, "June Robinson")
    seat = await _seat_role(conn, org, "LD 38")
    seat_asg = await _assign(conn, principal, seat, start=D(2023, 1, 1), end=D(2024, 12, 31))
    return staffer_role, staffer_asg, seat_asg


async def test_resolves_and_mints(conn):
    staffer_role, staffer_asg, seat_asg = await _scenario(conn)
    targets = (Target(staffer_role, "June Robinson"),)

    resolutions = await run_backfill(conn, execute=True, targets=targets)
    (r,) = resolutions
    assert r.problem is None
    assert r.staffer_assignment_id == staffer_asg
    assert r.principal_assignment_id == seat_asg
    assert r.valid_from == D(2023, 1, 1)
    assert r.valid_until == D(2024, 12, 31)  # intersection (seat ends earlier)

    edge = await conn.fetchrow(
        """SELECT from_assignment_id, to_assignment_id, valid_until
           FROM role_assignment_relationships
           WHERE from_assignment_id=$1 AND archived_at IS NULL""",
        staffer_asg,
    )
    assert edge["to_assignment_id"] == seat_asg
    assert edge["valid_until"] == D(2024, 12, 31)


async def test_ambiguous_principal_reported_not_guessed(conn):
    staffer_role, _, _ = await _scenario(conn)
    # a second "June Robinson" makes the principal ambiguous
    await _person(conn, "June Robinson")
    targets = (Target(staffer_role, "June Robinson"),)

    resolutions = await resolve(conn, targets)
    (r,) = resolutions
    assert r.problem is not None and "matched 2 people" in r.problem
    # dry-run mints nothing
    minted = await conn.fetchval("SELECT count(*) FROM role_assignment_relationships")
    assert minted == 0


async def test_missing_seat_reported(conn):
    org = await _org(conn)
    staffer_person = await _person(conn, "Coco Chang")
    staffer_role = await _plain_role(conn, org, "Legislative Assistant to Senator Saldana")
    await _assign(conn, staffer_person, staffer_role, start=D(2023, 1, 1))
    await _person(conn, "Saldana")  # exists but has NO seat assignment
    targets = (Target(staffer_role, "Saldana"),)

    (r,) = await resolve(conn, targets)
    assert r.problem is not None and "seat assignments" in r.problem
