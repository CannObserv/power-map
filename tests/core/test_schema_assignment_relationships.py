"""Integration tests for the role_assignment_relationships edge (#301).

Covers the schema-level guarantees: identity uniqueness, self-edge rejection,
change-feed emission (own entity_type + touch both endpoints), and the endpoint
mutation cascade (archive / clamp-earlier-end / clamp-later-start / invert->archive).
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


async def _org(conn):
    oid = generate_id()
    await conn.execute("INSERT INTO organizations (id) VALUES ($1)", oid)
    return oid


async def _person(conn):
    pid = generate_id()
    await conn.execute("INSERT INTO people (id) VALUES ($1)", pid)
    return pid


async def _role(conn, org_id, title):
    rid = generate_id()
    await conn.execute(
        "INSERT INTO roles (id, organization_id, title) VALUES ($1, $2, $3)",
        rid,
        org_id,
        title,
    )
    return rid


async def _assignment(conn, person_id, role_id, start=None, end=None, is_current=False):
    aid = generate_id()
    await conn.execute(
        """INSERT INTO role_assignments
               (id, person_id, role_id, start_date, end_date, is_current)
           VALUES ($1, $2, $3, $4, $5, $6)""",
        aid,
        person_id,
        role_id,
        start,
        end,
        is_current,
    )
    return aid


async def _edge(conn, frm, to, valid_from=None, valid_until=None, rel_slug="staff_of"):
    eid = generate_id()
    await conn.execute(
        """INSERT INTO role_assignment_relationships
               (id, from_assignment_id, to_assignment_id, rel_type_id, valid_from, valid_until)
           SELECT $1, $2, $3, t.id, $5, $6
           FROM role_assignment_relationship_types t WHERE t.slug = $4""",
        eid,
        frm,
        to,
        rel_slug,
        valid_from,
        valid_until,
    )
    return eid


async def _two_assignments(conn, **kw):
    """A staffer assignment and a principal assignment, independent people/roles."""
    org = await _org(conn)
    staffer = await _assignment(
        conn, await _person(conn), await _role(conn, org, "Legislative Aide"), **kw
    )
    principal = await _assignment(
        conn, await _person(conn), await _role(conn, org, "Senator"), **kw
    )
    return staffer, principal


# --------------------------------------------------------------------------- #
# Identity / constraints
# --------------------------------------------------------------------------- #


async def test_identity_unique_active(conn):
    frm, to = await _two_assignments(conn)
    await _edge(conn, frm, to)
    with pytest.raises(asyncpg.UniqueViolationError):
        await _edge(conn, frm, to)


async def test_identity_allows_reuse_after_archive(conn):
    frm, to = await _two_assignments(conn)
    first = await _edge(conn, frm, to)
    await conn.execute(
        "UPDATE role_assignment_relationships SET archived_at = NOW() WHERE id = $1", first
    )
    # archived row is excluded from the partial unique index → re-create allowed
    await _edge(conn, frm, to)


async def test_self_edge_rejected(conn):
    org = await _org(conn)
    a = await _assignment(conn, await _person(conn), await _role(conn, org, "Aide"))
    with pytest.raises(asyncpg.CheckViolationError):
        await _edge(conn, a, a)


async def test_invalid_range_rejected(conn):
    frm, to = await _two_assignments(conn)
    with pytest.raises(asyncpg.CheckViolationError):
        await _edge(conn, frm, to, valid_from=D(2024, 1, 1), valid_until=D(2023, 1, 1))


# --------------------------------------------------------------------------- #
# Change feed: own entity_type + touch both endpoints
# --------------------------------------------------------------------------- #


async def _max_change_id(conn):
    return await conn.fetchval("SELECT COALESCE(MAX(id), 0) FROM entity_changes")


async def test_edge_emits_own_change_and_touches_both_endpoints(conn):
    frm, to = await _two_assignments(conn)
    baseline = await _max_change_id(conn)
    eid = await _edge(conn, frm, to)
    rows = await conn.fetch(
        "SELECT entity_type, entity_id FROM entity_changes WHERE id > $1", baseline
    )
    pairs = {(r["entity_type"], r["entity_id"]) for r in rows}
    assert ("role_assignment_relationship", eid) in pairs
    assert ("role_assignment", frm) in pairs
    assert ("role_assignment", to) in pairs


async def test_edge_archive_surfaces_as_updated(conn):
    frm, to = await _two_assignments(conn)
    eid = await _edge(conn, frm, to)
    baseline = await _max_change_id(conn)
    await conn.execute(
        "UPDATE role_assignment_relationships SET archived_at = NOW() WHERE id = $1", eid
    )
    kinds = await conn.fetch(
        """SELECT change_kind FROM entity_changes
           WHERE id > $1 AND entity_type = 'role_assignment_relationship' AND entity_id = $2""",
        baseline,
        eid,
    )
    assert [r["change_kind"] for r in kinds] == ["updated"]


# --------------------------------------------------------------------------- #
# Endpoint mutation cascade
# --------------------------------------------------------------------------- #


async def _edge_row(conn, eid):
    return await conn.fetchrow(
        """SELECT valid_from, valid_until, archived_at
           FROM role_assignment_relationships WHERE id = $1""",
        eid,
    )


async def test_cascade_archive_on_endpoint_archive(conn):
    frm, to = await _two_assignments(conn, start=D(2023, 1, 1))
    eid = await _edge(conn, frm, to, valid_from=D(2023, 1, 10))
    await conn.execute("UPDATE role_assignments SET archived_at = NOW() WHERE id = $1", frm)
    row = await _edge_row(conn, eid)
    assert row["archived_at"] is not None


async def test_cascade_clamp_earlier_end(conn):
    frm, to = await _two_assignments(conn, start=D(2023, 1, 1))
    eid = await _edge(conn, frm, to, valid_from=D(2023, 1, 1), valid_until=D(2024, 12, 31))
    # principal seat ends earlier than the edge's stored valid_until
    await conn.execute(
        "UPDATE role_assignments SET end_date = $2 WHERE id = $1", to, D(2024, 6, 30)
    )
    row = await _edge_row(conn, eid)
    assert row["archived_at"] is None
    assert row["valid_until"] == D(2024, 6, 30)
    assert row["valid_from"] == D(2023, 1, 1)


async def test_cascade_clamp_later_start(conn):
    frm, to = await _two_assignments(conn)
    eid = await _edge(conn, frm, to, valid_from=D(2023, 1, 1), valid_until=D(2024, 12, 31))
    # staffer start moves later than the edge's stored valid_from
    await conn.execute(
        "UPDATE role_assignments SET start_date = $2 WHERE id = $1", frm, D(2023, 6, 1)
    )
    row = await _edge_row(conn, eid)
    assert row["archived_at"] is None
    assert row["valid_from"] == D(2023, 6, 1)
    assert row["valid_until"] == D(2024, 12, 31)


async def test_cascade_clamp_ongoing_edge_on_endpoint_end(conn):
    frm, to = await _two_assignments(conn, start=D(2023, 1, 1))
    # ongoing edge (valid_until NULL); endpoint gains a definite end → clamp closes it
    eid = await _edge(conn, frm, to, valid_from=D(2023, 1, 1), valid_until=None)
    await conn.execute(
        "UPDATE role_assignments SET end_date = $2 WHERE id = $1", to, D(2024, 12, 31)
    )
    row = await _edge_row(conn, eid)
    assert row["archived_at"] is None
    assert row["valid_until"] == D(2024, 12, 31)


async def test_cascade_invert_archives(conn):
    frm, to = await _two_assignments(conn)
    eid = await _edge(conn, frm, to, valid_from=D(2023, 1, 1), valid_until=D(2023, 12, 31))
    # push the staffer start past the edge's valid_until → window inverts → archive
    await conn.execute(
        "UPDATE role_assignments SET start_date = $2 WHERE id = $1", frm, D(2024, 6, 1)
    )
    row = await _edge_row(conn, eid)
    assert row["archived_at"] is not None
