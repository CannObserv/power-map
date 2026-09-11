"""The person merge primitive's contract with callers outside the admin (#514).

The admin suites (`tests/api/admin/test_people_merge_canonical.py`,
`test_merge_identity_signals.py`) pin what a merge does to the data. These pin
what it hands back: the applier re-points the producer crosswalk after a merge,
and an assignment the merge dropped as a duplicate has an anchor of its own.
"""

from datetime import date

import pytest
import pytest_asyncio

from src.core.db import generate_id
from src.core.person_merge import PersonNotFoundError, merge_person_into

pytestmark = [pytest.mark.integration]

ACTOR = "test-#514"


@pytest_asyncio.fixture(loop_scope="session")
async def db(db_pool):
    async with db_pool.acquire() as conn:
        tr = conn.transaction()
        await tr.start()
        try:
            yield conn
        finally:
            await tr.rollback()


async def _person(db, name):
    pid = generate_id()
    await db.execute("INSERT INTO people (id) VALUES ($1)", pid)
    await db.execute(
        "INSERT INTO person_names (id, person_id, name, is_canonical) VALUES ($1, $2, $3, TRUE)",
        generate_id(),
        pid,
        name,
    )
    return pid


async def _role(db):
    oid, rid = generate_id(), generate_id()
    await db.execute("INSERT INTO organizations (id) VALUES ($1)", oid)
    await db.execute(
        "INSERT INTO roles (id, organization_id, title) VALUES ($1, $2, 'Member')", rid, oid
    )
    return rid


async def _assign(db, person_id, role_id, start):
    aid = generate_id()
    await db.execute(
        "INSERT INTO role_assignments (id, person_id, role_id, start_date) VALUES ($1, $2, $3, $4)",
        aid,
        person_id,
        role_id,
        date.fromisoformat(start),
    )
    return aid


async def test_returns_the_assignments_dropped_as_duplicates(db):
    role = await _role(db)
    winner, loser = await _person(db, "Merge Return A"), await _person(db, "Merge Return B")
    survivor = await _assign(db, winner, role, "2020-01-01")
    dropped = await _assign(db, loser, role, "2020-01-01")
    moved = await _assign(db, loser, role, "2016-01-01")

    pairs = await merge_person_into(db, winner_id=winner, loser_id=loser, actor_email=ACTOR)

    assert pairs == [(dropped, survivor)]
    assert await db.fetchval("SELECT person_id FROM role_assignments WHERE id=$1", moved) == winner


async def test_returns_no_pairs_when_nothing_collides(db):
    winner, loser = await _person(db, "Merge Return C"), await _person(db, "Merge Return D")
    await _assign(db, loser, await _role(db), "2019-01-01")

    assert await merge_person_into(db, winner_id=winner, loser_id=loser, actor_email=ACTOR) == []


async def test_a_missing_loser_raises_before_writing(db):
    winner = await _person(db, "Merge Return E")
    with pytest.raises(PersonNotFoundError):
        await merge_person_into(db, winner_id=winner, loser_id=generate_id(), actor_email=ACTOR)
