"""Tests for rehome_assignment_relationships — merge edge preservation (#301).

The edge FKs CASCADE on assignment delete, so a merge that hard-deletes a losing
assignment would silently drop its active edges. rehome_assignment_relationships
re-points them onto the survivor (dedup on self-edge / collision) first.
"""

import pytest
import pytest_asyncio

from src.core.ancillary_migrate import rehome_assignment_relationships
from src.core.db import generate_id

pytestmark = [pytest.mark.integration]


@pytest_asyncio.fixture(loop_scope="session")
async def conn(db_pool):
    async with db_pool.acquire() as c:
        tr = c.transaction()
        await tr.start()
        try:
            yield c
        finally:
            await tr.rollback()


async def _assignment(conn):
    org, person, role, aid = generate_id(), generate_id(), generate_id(), generate_id()
    await conn.execute("INSERT INTO organizations (id) VALUES ($1)", org)
    await conn.execute("INSERT INTO people (id) VALUES ($1)", person)
    await conn.execute(
        "INSERT INTO roles (id, organization_id, title) VALUES ($1,$2,'R')", role, org
    )
    await conn.execute(
        "INSERT INTO role_assignments (id, person_id, role_id) VALUES ($1,$2,$3)", aid, person, role
    )
    return aid


async def _edge(conn, frm, to):
    eid = generate_id()
    await conn.execute(
        """INSERT INTO role_assignment_relationships
               (id, from_assignment_id, to_assignment_id, rel_type_id)
           SELECT $1,$2,$3,id FROM role_assignment_relationship_types WHERE slug='staff_of'""",
        eid,
        frm,
        to,
    )
    return eid


async def _active(conn, eid):
    return await conn.fetchrow(
        "SELECT from_assignment_id, to_assignment_id, archived_at"
        " FROM role_assignment_relationships WHERE id=$1",
        eid,
    )


async def test_rehome_from_side(conn):
    loser, winner, other = await _assignment(conn), await _assignment(conn), await _assignment(conn)
    e = await _edge(conn, loser, other)  # loser is the staffer
    await rehome_assignment_relationships(conn, [(loser, winner)])
    row = await _active(conn, e)
    assert row["from_assignment_id"] == winner
    assert row["to_assignment_id"] == other


async def test_rehome_to_side(conn):
    loser, winner, other = await _assignment(conn), await _assignment(conn), await _assignment(conn)
    e = await _edge(conn, other, loser)  # loser is the principal
    await rehome_assignment_relationships(conn, [(loser, winner)])
    row = await _active(conn, e)
    assert row["to_assignment_id"] == winner
    assert row["from_assignment_id"] == other


async def test_rehome_self_edge_deleted(conn):
    loser, winner = await _assignment(conn), await _assignment(conn)
    # edge loser -> winner; re-pointing from=loser to from=winner would self-edge
    e = await _edge(conn, loser, winner)
    await rehome_assignment_relationships(conn, [(loser, winner)])
    assert await _active(conn, e) is None  # deleted


async def test_rehome_collision_deleted(conn):
    loser, winner, other = await _assignment(conn), await _assignment(conn), await _assignment(conn)
    winner_edge = await _edge(conn, winner, other)  # winner already staffs `other`
    loser_edge = await _edge(conn, loser, other)  # loser staffs the same `other`
    await rehome_assignment_relationships(conn, [(loser, winner)])
    # loser's edge collides with winner's identical edge → deleted; winner's kept
    assert await _active(conn, loser_edge) is None
    assert (await _active(conn, winner_edge))["from_assignment_id"] == winner


async def test_rehome_survives_cascade_delete(conn):
    """End-to-end: re-home then hard-delete the loser; the edge is preserved."""
    loser, winner, other = await _assignment(conn), await _assignment(conn), await _assignment(conn)
    e = await _edge(conn, loser, other)
    await rehome_assignment_relationships(conn, [(loser, winner)])
    await conn.execute("DELETE FROM role_assignments WHERE id=$1", loser)
    row = await _active(conn, e)
    assert row is not None and row["from_assignment_id"] == winner


async def test_without_rehome_cascade_drops_edge(conn):
    """Control: deleting the loser WITHOUT re-home cascades the edge away."""
    loser, other = await _assignment(conn), await _assignment(conn)
    e = await _edge(conn, loser, other)
    await conn.execute("DELETE FROM role_assignments WHERE id=$1", loser)
    assert await _active(conn, e) is None
