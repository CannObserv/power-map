"""The admin's slot registry (#498): how each pinnable field's live value is read.

A slot is one producer-owned field — `(entity_type, field)`, the overlay pair —
and its value is what the edit hook compares either side of an admin write.
The rule for a multi-row slot (names, acronyms) is the canonical row, else the
earliest: whichever row it lands on, the value is one PM holds, so a pin of it
is always a noop for the applier, never a re-insert.
"""

import pytest
import pytest_asyncio

from src.api.admin.overlay_slots import SLOTS, read_slots, slots_for, tracked
from src.core.curation_overlay import active_pins
from src.core.db import generate_id
from src.core.ingestion.crosswalk import PRODUCER_SOURCE

pytestmark = pytest.mark.integration

DISSOLVED = "01KV0000000000000000000007"  # seeded entity_event_types.dissolved


@pytest_asyncio.fixture(loop_scope="session")
async def db(db_pool):
    async with db_pool.acquire() as conn:
        tr = conn.transaction()
        await tr.start()
        try:
            yield conn
        finally:
            await tr.rollback()


@pytest_asyncio.fixture(loop_scope="session")
async def curator(db) -> str:
    user_id = f"u-{generate_id()}"
    await db.execute("INSERT INTO app_users (id, email) VALUES ($1, 'c@example.org')", user_id)
    return user_id


async def _anchor(db, kind: str, pm_id: str) -> None:
    await db.execute(
        "INSERT INTO producer_crosswalk"
        " (id, source, kind, producer_id, exported_pm_id, pm_id, resolution)"
        " VALUES ($1, $2, $3, $4, $5, $5, 'live')",
        generate_id(),
        PRODUCER_SOURCE,
        kind,
        f"p-{generate_id()}",
        pm_id,
    )


async def _person(db, *names: tuple[str, str, bool], anchored: bool = True) -> str:
    pid = generate_id()
    await db.execute("INSERT INTO people (id) VALUES ($1)", pid)
    for text, name_type, canonical in names:
        await db.execute(
            "INSERT INTO person_names (id, person_id, name, name_type, is_canonical)"
            " VALUES ($1, $2, $3, $4, $5)",
            generate_id(),
            pid,
            text,
            name_type,
            canonical,
        )
    if anchored:
        await _anchor(db, "person", pid)
    return pid


async def _org(db, *, parent=None) -> str:
    oid = generate_id()
    await db.execute("INSERT INTO organizations (id, parent_id) VALUES ($1, $2)", oid, parent)
    await _anchor(db, "organization", oid)
    return oid


def test_the_registry_holds_the_five_slots():
    assert set(SLOTS) == {
        ("person", "name"),
        ("organization", "legal_name"),
        ("organization", "acronym"),
        ("organization", "parent_id"),
        ("organization", "dissolved_year"),
    }
    assert [s.field for s in slots_for("person")] == ["name"]


# --- reading a slot --------------------------------------------------------------


async def test_the_name_slot_is_the_canonical_legal_row(db):
    pid = await _person(
        db, ("Dennis L. Heck", "legal", False), ("Denny Heck", "legal", True), ("D", "alias", False)
    )

    assert await read_slots(db, "person", pid) == {"name": "Denny Heck"}


async def test_the_name_slot_falls_back_to_the_earliest_legal_row(db):
    """The display name is `preferred`: the slot is still a legal value PM holds."""
    pid = await _person(db, ("Mike", "preferred", True), ("Michael Padden", "legal", False))

    assert await read_slots(db, "person", pid) == {"name": "Michael Padden"}


async def test_a_person_with_no_legal_row_reads_none(db):
    pid = await _person(db, ("Mike", "preferred", True))

    assert await read_slots(db, "person", pid) == {"name": None}


async def test_the_organization_slots(db):
    parent = await _org(db)
    oid = await _org(db, parent=parent)
    for text, name_type, canonical in (("CB", "dba", True), ("Capital Budget", "legal", False)):
        await db.execute(
            "INSERT INTO organization_names (id, organization_id, name, name_type, is_canonical)"
            " VALUES ($1, $2, $3, $4, $5)",
            generate_id(),
            oid,
            text,
            name_type,
            canonical,
        )
    for text, canonical in (("CAPB", False), ("CB", True)):
        await db.execute(
            "INSERT INTO organization_acronyms (id, organization_id, acronym, is_canonical)"
            " VALUES ($1, $2, $3, $4)",
            generate_id(),
            oid,
            text,
            canonical,
        )
    for year, archived in ((2019, True), (2020, False)):
        await db.execute(
            "INSERT INTO entity_events (id, entity_type, entity_id, event_type_id, event_year,"
            " archived_at) VALUES ($1, 'organization', $2, $3, $4, CASE WHEN $5 THEN NOW() END)",
            generate_id(),
            oid,
            DISSOLVED,
            year,
            archived,
        )

    assert await read_slots(db, "organization", oid) == {
        "legal_name": "Capital Budget",
        "acronym": "CB",
        "parent_id": parent,
        "dissolved_year": 2020,  # the archived 2019 event is not the slot
    }


# --- tracked(): the edit hook ------------------------------------------------------


async def test_an_edit_that_moves_a_slot_pins_the_post_edit_value(db, curator):
    pid = await _person(db, ("Denny Heck", "legal", True))

    async with tracked(db, "person", pid, user_id=curator) as edit:
        await db.execute("UPDATE person_names SET name = 'Dennis Heck' WHERE person_id = $1", pid)

    assert edit.pinned == ["name"]
    assert [(p.field, p.value) for p in await active_pins(db, "person", pid)] == [
        ("name", "Dennis Heck")
    ]


async def test_an_edit_that_leaves_the_slot_alone_pins_nothing(db, curator):
    """A locale edit is not a claim about the value."""
    pid = await _person(db, ("Denny Heck", "legal", True))

    async with tracked(db, "person", pid, user_id=curator) as edit:
        await db.execute("UPDATE person_names SET locale = 'en-US' WHERE person_id = $1", pid)

    assert edit.pinned == []
    assert await active_pins(db, "person", pid) == []


async def test_deleting_the_last_legal_row_pins_the_slot_empty(db, curator):
    pid = await _person(db, ("Denny Heck", "legal", True))

    async with tracked(db, "person", pid, user_id=curator):
        await db.execute("DELETE FROM person_names WHERE person_id = $1", pid)

    assert [(p.field, p.value) for p in await active_pins(db, "person", pid)] == [("name", None)]


async def test_an_entity_outside_the_crosswalk_is_direct_curation(db, curator):
    pid = await _person(db, ("Denny Heck", "legal", True), anchored=False)

    async with tracked(db, "person", pid, user_id=curator) as edit:
        await db.execute("UPDATE person_names SET name = 'Dennis Heck' WHERE person_id = $1", pid)

    assert edit.pinned == []
    assert await active_pins(db, "person", pid) == []


async def test_an_edit_that_raises_pins_nothing(db, curator):
    """The pin rides the edit's transaction; an edit that fails never reaches the hook."""
    pid = await _person(db, ("Denny Heck", "legal", True))

    with pytest.raises(RuntimeError):
        async with tracked(db, "person", pid, user_id=curator):
            await db.execute("UPDATE person_names SET name = 'X' WHERE person_id = $1", pid)
            raise RuntimeError("the route's own validation failed")

    assert await active_pins(db, "person", pid) == []
