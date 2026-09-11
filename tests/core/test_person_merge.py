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
from src.core.person_merge import PersonNotFoundError, merge_person_into, preview_person_merge

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


# --- preview_person_merge: what the merge will do, stated before it does it ------

_IDTYPE_ROSTER = "person_wa_legislature_roster"


async def _name(db, person_id, name, name_type="legal", canonical=False):
    nid = generate_id()
    await db.execute(
        "INSERT INTO person_names (id, person_id, name, name_type, is_canonical)"
        " VALUES ($1, $2, $3, $4, $5)",
        nid,
        person_id,
        name,
        name_type,
        canonical,
    )
    return nid


async def _identifier(db, person_id, value):
    iid = generate_id()
    await db.execute(
        "INSERT INTO identifiers (id, entity_id, entity_identifier_type_id, value)"
        " SELECT $1, $2, id, $3 FROM entity_identifier_types WHERE slug = $4",
        iid,
        person_id,
        value,
        _IDTYPE_ROSTER,
    )
    return iid


async def _override(db, entity_type, entity_id, field, value):
    await db.execute(
        "INSERT INTO curation_overlay (id, entity_type, entity_id, field, value)"
        " VALUES ($1, $2, $3, $4, $5)",
        generate_id(),
        entity_type,
        entity_id,
        field,
        value,
    )


async def test_preview_states_each_row_the_merge_then_moves_or_drops(db):
    role, other_role = await _role(db), await _role(db)
    winner, loser = await _person(db, "Preview Winner"), await _person(db, "Preview Loser")
    twin = await _name(db, winner, "Shared Form", "alias")
    deduped = await _name(db, loser, "Shared Form", "variant")  # both display types → dedup
    moved_name = await _name(db, loser, "Only On Loser", "alias")
    survivor_ra = await _assign(db, winner, role, "2020-01-01")
    dropped_ra = await _assign(db, loser, role, "2020-01-01")
    moved_ra = await _assign(db, loser, other_role, "2012-01-01")
    ident = await _identifier(db, loser, "previewloser:1977")
    await _override(db, "person", loser, "name", "Loser Pick")
    await _override(db, "person", loser, "pronouns", "they/them")
    await _override(db, "person", winner, "name", "Winner Pick")
    canonical_loser_name = await db.fetchval(
        "SELECT id FROM person_names WHERE person_id=$1 AND is_canonical", loser
    )

    preview = await preview_person_merge(db, winner_id=winner, loser_id=loser)

    assert preview["names"] == sorted(
        [
            {"id": canonical_loser_name, "action": "move", "into": None},
            {"id": deduped, "action": "dedup", "into": twin},
            {"id": moved_name, "action": "move", "into": None},
        ],
        key=lambda n: n["id"],
    )
    assert preview["assignments"] == sorted(
        [
            {"id": dropped_ra, "action": "drop", "into": survivor_ra},
            {"id": moved_ra, "action": "move", "into": None},
        ],
        key=lambda a: a["id"],
    )
    assert preview["identifiers"] == [ident]
    assert preview["overlay"] == {"moved": ["pronouns"], "dropped": ["name"]}

    await merge_person_into(db, winner_id=winner, loser_id=loser, actor_email=ACTOR)

    # Every "move" landed on the winner; every "dedup"/"drop" is gone.
    for row in preview["names"]:
        owner = await db.fetchval("SELECT person_id FROM person_names WHERE id=$1", row["id"])
        assert owner == (winner if row["action"] == "move" else None), row
    for row in preview["assignments"]:
        owner = await db.fetchval("SELECT person_id FROM role_assignments WHERE id=$1", row["id"])
        assert owner == (winner if row["action"] == "move" else None), row
    assert await db.fetchval("SELECT entity_id FROM identifiers WHERE id=$1", ident) == winner


async def test_preview_writes_nothing(db):
    winner, loser = await _person(db, "Preview Idle A"), await _person(db, "Preview Idle B")
    await _assign(db, loser, await _role(db), "2001-01-01")
    before = await db.fetchval("SELECT coalesce(max(id), 0) FROM entity_changes")

    await preview_person_merge(db, winner_id=winner, loser_id=loser)

    assert await db.fetchval("SELECT coalesce(max(id), 0) FROM entity_changes") == before
    assert await db.fetchval("SELECT count(*) FROM people WHERE id=$1", loser) == 1


async def test_preview_of_a_missing_person_raises(db):
    winner = await _person(db, "Preview Missing")
    with pytest.raises(PersonNotFoundError):
        await preview_person_merge(db, winner_id=winner, loser_id=generate_id())
