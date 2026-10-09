"""#630: the four identifier entity tables behave like the referenced side of an FK.

``identifiers.entity_id`` is polymorphic — its table is the one its type's
``entity_identifier_types.entity_type`` names — so no real FK can hold it.
``trg_identifiers_entity`` (#622) is the referencing half; the ``BEFORE DELETE``
triggers here are the referenced half: deleting a person, organization, role
assignment or jurisdiction that an identifier still names raises
``foreign_key_violation``, naming the trigger as its constraint (restrict). The
admin hard delete drops the identifiers first (``delete_entity_ancillary``,
#605) and merges re-home them; this is the backstop for every other path. A
stranded identifier would answer ``<type>_archived`` for its value for good (#481).
"""

import asyncio

import asyncpg
import pytest
import pytest_asyncio

from src.core.ancillary_migrate import ENTITY_TABLES
from src.core.db import IDENTIFIER_ENTITY_GUARD, generate_id
from tests.db_utils import trigger_disabled, until_lock_waiting
from tests.polymorphic_seeders import archived_entity

pytestmark = [pytest.mark.integration]

GUARDS = {
    "person": "trg_people_identifiers",
    "organization": "trg_organizations_identifiers",
    "role_assignment": "trg_role_assignments_identifiers",
    "jurisdiction": "trg_jurisdictions_identifiers",
}
#: One identifier type slug per entity type the catalog admits.
TYPE_SLUGS = {
    "person": "person_ssn",
    "organization": "org_ubi",
    "role_assignment": "role_wa_pdc",
    "jurisdiction": "jur_fips",
}
ENTITY_TYPES = sorted(GUARDS)


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


async def _identify(conn, entity_type: str, entity_id: str) -> str:
    iid = generate_id()
    await conn.execute(
        "INSERT INTO identifiers (id, entity_id, entity_identifier_type_id, value)"
        " SELECT $1, $2, t.id, 'V-1' FROM entity_identifier_types t WHERE t.slug = $3",
        iid,
        entity_id,
        TYPE_SLUGS[entity_type],
    )
    return iid


async def _delete(conn, entity_type: str, entity_id: str) -> None:
    await conn.execute(f"DELETE FROM {ENTITY_TABLES[entity_type]} WHERE id = $1", entity_id)


async def _exists(conn, entity_type: str, entity_id: str) -> bool:
    return await conn.fetchval(
        f"SELECT EXISTS (SELECT 1 FROM {ENTITY_TABLES[entity_type]} WHERE id = $1)", entity_id
    )


@pytest.mark.parametrize("entity_type", ENTITY_TYPES)
async def test_bare_delete_of_an_identified_entity_is_a_foreign_key_violation(conn, entity_type):
    entity = await archived_entity(conn, entity_type)
    await _identify(conn, entity_type, entity)

    with pytest.raises(asyncpg.ForeignKeyViolationError) as exc:
        await _delete(conn, entity_type, entity)
    assert exc.value.sqlstate == "23503"
    assert exc.value.constraint_name == GUARDS[entity_type]


@pytest.mark.parametrize("entity_type", ENTITY_TYPES)
async def test_delete_proceeds_once_the_identifiers_are_gone(conn, entity_type):
    """Restrict, not cascade: the caller drops (or re-homes) first, then deletes."""
    entity = await archived_entity(conn, entity_type)
    iid = await _identify(conn, entity_type, entity)
    await conn.execute("DELETE FROM identifiers WHERE id = $1", iid)

    await _delete(conn, entity_type, entity)
    assert not await _exists(conn, entity_type, entity)


@pytest.mark.parametrize("entity_type", ENTITY_TYPES)
async def test_another_entitys_identifier_does_not_block(conn, entity_type):
    entity, other = (
        await archived_entity(conn, entity_type),
        await archived_entity(conn, entity_type),
    )
    await _identify(conn, entity_type, other)

    await _delete(conn, entity_type, entity)
    assert not await _exists(conn, entity_type, entity)


async def test_an_identifier_of_another_type_on_the_same_id_does_not_block(conn):
    """The trigger matches on the type's ``entity_type`` too, not the id alone."""
    person = await archived_entity(conn, "person")
    # An org-typed identifier on the person's id — dangling, but not a reference
    # to the person. #622 refuses it, so it is planted with that trigger off,
    # inside this rolled-back transaction.
    async with trigger_disabled(conn, "identifiers", IDENTIFIER_ENTITY_GUARD):
        await _identify(conn, "organization", person)

    await _delete(conn, "person", person)
    assert not await _exists(conn, "person", person)


async def test_an_identifier_committed_while_the_delete_waits_is_seen(db_pool):
    """The delete waits on the identifier's ``FOR KEY SHARE`` (#622), then refuses.

    A ``BEFORE DELETE`` row trigger runs after the row lock is taken, so its
    check sees an identifier that committed while the delete was blocked on it.
    """
    async with db_pool.acquire() as writer, db_pool.acquire() as deleter:
        person = await archived_entity(writer, "person")  # committed: deleter must see it
        tr = writer.transaction()
        await tr.start()
        delete, committed = None, False
        try:
            await _identify(writer, "person", person)
            delete = asyncio.create_task(_delete(deleter, "person", person))
            await until_lock_waiting(writer, deleter.get_server_pid())
            await tr.commit()
            committed = True

            with pytest.raises(asyncpg.ForeignKeyViolationError) as exc:
                await delete
            assert exc.value.constraint_name == GUARDS["person"]
        finally:
            if not committed:
                await tr.rollback()
            if delete is not None and not delete.done():
                await asyncio.wait([delete], timeout=5)
            await writer.execute("DELETE FROM identifiers WHERE entity_id = $1", person)
            await writer.execute("DELETE FROM people WHERE id = $1", person)
