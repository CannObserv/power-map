"""#615: people and organizations behave like the referenced side of an FK.

``entity_events.linked_entity_id`` is polymorphic, so no real FK can hold it.
``trg_entity_events_linked_entity`` (#608) is the referencing half; the
``BEFORE DELETE`` triggers here are the referenced half: deleting a person or
organization that another entity's event still links to — archived events
included — raises ``foreign_key_violation``, naming the trigger as its
constraint. The admin hard delete refuses first with a named 409
(``inbound_link_conflict``); merges re-point every link before the DELETE
(#611); this is the backstop for every other path.
"""

import asyncio

import asyncpg
import pytest
import pytest_asyncio

from src.core.db import generate_id
from tests.db_utils import until_lock_waiting

pytestmark = [pytest.mark.integration]

GUARDS = {
    "person": "trg_people_inbound_event_links",
    "organization": "trg_organizations_inbound_event_links",
}
TABLES = {"person": "people", "organization": "organizations"}
#: An event type each owner type can carry with a link to the given target type.
LINK_SLUGS = {
    ("organization", "organization"): "succeeded_by",
    ("person", "person"): "marriage",
    ("organization", "person"): "other",
    ("person", "organization"): "other",
}


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


async def _entity(conn, entity_type: str) -> str:
    eid = generate_id()
    await conn.execute(f"INSERT INTO {TABLES[entity_type]} (id) VALUES ($1)", eid)
    return eid


async def _link(
    conn, owner_type, owner_id, linked_type, linked_id, *, archived=False, slug=None
) -> str:
    eid = generate_id()
    await conn.execute(
        """INSERT INTO entity_events
               (id, entity_type, entity_id, event_type_id,
                linked_entity_type, linked_entity_id, archived_at)
           SELECT $1, $2, $3, t.id, $5, $6, CASE WHEN $7 THEN NOW() END
           FROM entity_event_types t WHERE t.slug = $4""",
        eid,
        owner_type,
        owner_id,
        slug or LINK_SLUGS[(owner_type, linked_type)],
        linked_type,
        linked_id,
        archived,
    )
    return eid


async def _delete(conn, entity_type: str, entity_id: str) -> None:
    await conn.execute(f"DELETE FROM {TABLES[entity_type]} WHERE id = $1", entity_id)


async def _exists(conn, entity_type: str, entity_id: str) -> bool:
    return await conn.fetchval(
        f"SELECT EXISTS (SELECT 1 FROM {TABLES[entity_type]} WHERE id = $1)", entity_id
    )


@pytest.mark.parametrize("owner_type", ["person", "organization"])
@pytest.mark.parametrize("target_type", ["person", "organization"])
@pytest.mark.parametrize("archived", [False, True], ids=["active", "archived"])
async def test_bare_delete_with_an_inbound_link_is_a_foreign_key_violation(
    conn, owner_type, target_type, archived
):
    """Archived links block too: unarchiving the event would restore the link."""
    owner, target = await _entity(conn, owner_type), await _entity(conn, target_type)
    await _link(conn, owner_type, owner, target_type, target, archived=archived)

    with pytest.raises(asyncpg.ForeignKeyViolationError) as exc:
        await _delete(conn, target_type, target)
    assert exc.value.sqlstate == "23503"
    assert exc.value.constraint_name == GUARDS[target_type]


@pytest.mark.parametrize("target_type", ["person", "organization"])
async def test_delete_without_an_inbound_link_succeeds(conn, target_type):
    """The target's own outbound link, and an unrelated entity's, do not block."""
    target, other = await _entity(conn, target_type), await _entity(conn, target_type)
    await _link(conn, target_type, target, target_type, other)

    await _delete(conn, target_type, target)
    assert not await _exists(conn, target_type, target)


@pytest.mark.parametrize("target_type", ["person", "organization"])
async def test_a_self_link_is_not_inbound(conn, target_type):
    """The entity's own event naming itself is its own data, not another's reference.

    Mirrors ``inbound_link_conflict``, which excludes it, so the backstop never
    refuses a delete the named check admits.
    """
    target = await _entity(conn, target_type)
    await _link(conn, target_type, target, target_type, target, slug="other")

    await _delete(conn, target_type, target)
    assert not await _exists(conn, target_type, target)


async def test_a_link_naming_another_type_does_not_block(conn):
    """The trigger matches on ``linked_entity_type`` too, not the id alone."""
    org, person = await _entity(conn, "organization"), await _entity(conn, "person")
    # A person-typed link to the org's id — dangling, but not a reference to the
    # org. The #608 trigger refuses it, so it is planted with that trigger off,
    # inside this rolled-back transaction.
    await conn.execute("ALTER TABLE entity_events DISABLE TRIGGER trg_entity_events_linked_entity")
    await _link(conn, "person", person, "person", org)

    await _delete(conn, "organization", org)
    assert not await _exists(conn, "organization", org)


async def test_a_link_committed_while_the_delete_waits_is_seen(db_pool):
    """The delete waits on the link's ``FOR KEY SHARE`` (#608), then refuses.

    A ``BEFORE DELETE`` row trigger runs after the row lock is taken, so its
    check sees a link that committed while the delete was blocked on it.
    """
    async with db_pool.acquire() as writer, db_pool.acquire() as deleter:
        # Committed, so the second connection can see (and try to delete) them.
        spouse, target = await _entity(writer, "person"), await _entity(writer, "person")
        tr = writer.transaction()
        await tr.start()
        delete, committed = None, False
        try:
            await _link(writer, "person", spouse, "person", target)
            delete = asyncio.create_task(_delete(deleter, "person", target))
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
            await writer.execute("DELETE FROM entity_events WHERE entity_id = $1", spouse)
            await writer.execute("DELETE FROM people WHERE id = ANY($1::text[])", [spouse, target])
