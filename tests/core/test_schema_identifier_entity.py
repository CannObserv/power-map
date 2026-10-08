"""#622: ``identifiers.entity_id`` behaves like the referencing side of an FK.

The column is polymorphic — the table its id lives in is the one its type's
``entity_identifier_types.entity_type`` names — so no real FK can hold it.
``trg_identifiers_entity`` stands in: a new row, or one whose entity or type
changes, must name an existing row of the type's table (archived included),
or the write raises ``foreign_key_violation`` naming the trigger as its
constraint. A row naming another type's entity resolves to ``<type>_archived``
and escapes the touch trigger's change-feed dispatch, so it is refused too.
"""

import asyncio

import asyncpg
import pytest
import pytest_asyncio

from src.core.db import generate_id
from tests.db_utils import until_lock_waiting
from tests.polymorphic_seeders import archived_entity

pytestmark = [pytest.mark.integration]

GUARD = "trg_identifiers_entity"
#: One identifier type slug per entity type the catalog admits.
TYPE_SLUGS = {
    "person": "person_ssn",
    "organization": "org_ubi",
    "role_assignment": "role_wa_pdc",
    "jurisdiction": "jur_fips",
}
#: A different entity type for each, to mismatch against.
OTHER = {
    "person": "organization",
    "organization": "person",
    "role_assignment": "person",
    "jurisdiction": "organization",
}
ENTITY_TYPES = sorted(TYPE_SLUGS)


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


async def _type_id(conn, entity_type: str) -> str:
    return await conn.fetchval(
        "SELECT id FROM entity_identifier_types WHERE slug = $1", TYPE_SLUGS[entity_type]
    )


async def _insert(conn, entity_type: str, entity_id: str, value: str = "V-1") -> str:
    iid = generate_id()
    await conn.execute(
        "INSERT INTO identifiers (id, entity_id, entity_identifier_type_id, value)"
        " VALUES ($1, $2, $3, $4)",
        iid,
        entity_id,
        await _type_id(conn, entity_type),
        value,
    )
    return iid


def _assert_guard(exc: pytest.ExceptionInfo) -> None:
    assert exc.value.sqlstate == "23503"
    assert exc.value.constraint_name == GUARD


@pytest.mark.parametrize("entity_type", ENTITY_TYPES)
async def test_an_identifier_on_its_types_entity_is_accepted(conn, entity_type):
    """Archived entities included: unarchiving must find the identifier still there."""
    entity = await archived_entity(conn, entity_type)
    iid = await _insert(conn, entity_type, entity)
    assert await conn.fetchval("SELECT entity_id FROM identifiers WHERE id = $1", iid) == entity


@pytest.mark.parametrize("entity_type", ENTITY_TYPES)
async def test_an_identifier_on_a_missing_entity_is_a_foreign_key_violation(conn, entity_type):
    with pytest.raises(asyncpg.ForeignKeyViolationError) as exc:
        await _insert(conn, entity_type, generate_id())
    _assert_guard(exc)


@pytest.mark.parametrize("entity_type", ENTITY_TYPES)
async def test_an_identifier_on_another_types_entity_is_a_foreign_key_violation(conn, entity_type):
    """The id exists — in the wrong table for the identifier's type."""
    other = await archived_entity(conn, OTHER[entity_type])
    with pytest.raises(asyncpg.ForeignKeyViolationError) as exc:
        await _insert(conn, entity_type, other)
    _assert_guard(exc)


@pytest.mark.parametrize("entity_type", ENTITY_TYPES)
async def test_moving_an_identifier_to_a_missing_entity_is_a_foreign_key_violation(
    conn, entity_type
):
    iid = await _insert(conn, entity_type, await archived_entity(conn, entity_type))
    with pytest.raises(asyncpg.ForeignKeyViolationError) as exc:
        await conn.execute(
            "UPDATE identifiers SET entity_id = $1 WHERE id = $2", generate_id(), iid
        )
    _assert_guard(exc)


async def test_retyping_an_identifier_to_another_entity_type_is_a_foreign_key_violation(conn):
    """The entity stays put but the type now names another table."""
    iid = await _insert(conn, "person", await archived_entity(conn, "person"))
    with pytest.raises(asyncpg.ForeignKeyViolationError) as exc:
        await conn.execute(
            "UPDATE identifiers SET entity_identifier_type_id = $1 WHERE id = $2",
            await _type_id(conn, "organization"),
            iid,
        )
    _assert_guard(exc)


async def test_moving_an_identifier_to_a_same_type_entity_is_accepted(conn):
    """The merge re-point: loser → survivor of the same type."""
    loser, winner = await archived_entity(conn, "person"), await archived_entity(conn, "person")
    iid = await _insert(conn, "person", loser)
    await conn.execute("UPDATE identifiers SET entity_id = $1 WHERE id = $2", winner, iid)
    assert await conn.fetchval("SELECT entity_id FROM identifiers WHERE id = $1", iid) == winner


async def test_retyping_within_one_entity_type_does_not_recheck(conn):
    """The reference is (entity_type, entity_id); another person type changes neither.

    Seed reconciliation re-ids an operator-created type this way inside
    ``apply_schema``; an orphan under it must not abort the apply.
    """
    person = await archived_entity(conn, "person")
    iid = await _insert(conn, "person", person)
    await conn.execute("DELETE FROM people WHERE id = $1", person)  # no FK: dangles

    other_person_type = await conn.fetchval(
        "SELECT id FROM entity_identifier_types WHERE slug = 'person_wa_pdc'"
    )
    await conn.execute(
        "UPDATE identifiers SET entity_identifier_type_id = $1 WHERE id = $2",
        other_person_type,
        iid,
    )
    assert (
        await conn.fetchval("SELECT entity_identifier_type_id FROM identifiers WHERE id = $1", iid)
        == other_person_type
    )


async def test_an_unchanged_reference_does_not_recheck(conn):
    """Rewriting a row whose entity already vanished stays possible.

    An editor that writes every column back — the reference unchanged — must
    not be forced to repoint first (#608's link trigger takes the same line).
    """
    person = await archived_entity(conn, "person")
    iid = await _insert(conn, "person", person)
    await conn.execute("DELETE FROM people WHERE id = $1", person)  # no FK: dangles

    await conn.execute(
        "UPDATE identifiers SET value = 'V-2', entity_id = $1,"
        " entity_identifier_type_id = $2 WHERE id = $3",
        person,
        await _type_id(conn, "person"),
        iid,
    )
    assert await conn.fetchval("SELECT value FROM identifiers WHERE id = $1", iid) == "V-2"


async def test_an_unknown_type_id_is_left_to_the_real_fk(conn):
    """The catalog FK names the missing type; the trigger does not guess one."""
    with pytest.raises(asyncpg.ForeignKeyViolationError) as exc:
        await conn.execute(
            "INSERT INTO identifiers (id, entity_id, entity_identifier_type_id, value)"
            " VALUES ($1, $2, $3, 'V')",
            generate_id(),
            await archived_entity(conn, "person"),
            generate_id(),
        )
    assert exc.value.constraint_name == "identifiers_entity_identifier_type_id_fkey"


async def test_an_unknown_entity_type_is_not_looked_up_as_a_person(conn):
    """A catalog type the trigger has no branch for fails as such, not as a missing person.

    Unreachable while the catalog's CHECK admits only the four types, so the
    CHECK is dropped inside this rolled-back transaction to get past it.
    """
    await conn.execute(
        "ALTER TABLE entity_identifier_types"
        " DROP CONSTRAINT entity_identifier_types_entity_type_check"
    )
    tid = generate_id()
    await conn.execute(
        "INSERT INTO entity_identifier_types (id, entity_type, slug, display_name, full_name)"
        " VALUES ($1, 'role', $2, 'X', 'X')",
        tid,
        f"x-{tid.lower()}",
    )
    with pytest.raises(asyncpg.CheckViolationError, match="unsupported entity_type"):
        await conn.execute(
            "INSERT INTO identifiers (id, entity_id, entity_identifier_type_id, value)"
            " VALUES ($1, $2, $3, 'V')",
            generate_id(),
            await archived_entity(conn, "person"),
            tid,
        )


async def test_an_identifier_written_while_its_entity_is_deleted_is_refused(db_pool):
    """The write waits on the delete's row lock, then sees the row gone.

    The check takes ``FOR KEY SHARE``: a plain read would see the row the
    uncommitted delete still holds, pass, and commit an identifier naming a
    deleted id (the touch trigger's ``UPDATE`` waits too, but then updates
    nothing and raises nothing).
    """
    async with db_pool.acquire() as writer, db_pool.acquire() as deleter:
        person = await archived_entity(writer, "person")  # committed: deleter must see it
        tr = deleter.transaction()
        await tr.start()
        write, committed = None, False
        try:
            await deleter.execute("DELETE FROM people WHERE id = $1", person)
            write = asyncio.create_task(_insert(writer, "person", person))
            await until_lock_waiting(deleter, writer.get_server_pid())
            await tr.commit()
            committed = True

            with pytest.raises(asyncpg.ForeignKeyViolationError) as exc:
                await write
            _assert_guard(exc)
        finally:
            if not committed:
                await tr.rollback()
            if write is not None and not write.done():
                await asyncio.wait([write], timeout=5)
            await deleter.execute("DELETE FROM identifiers WHERE entity_id = $1", person)
            await deleter.execute("DELETE FROM people WHERE id = $1", person)
