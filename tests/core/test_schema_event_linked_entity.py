"""#608: ``entity_events.linked_entity_id`` behaves like the referencing side of an FK.

The column is polymorphic (person | organization), so no real FK can hold it.
``trg_entity_events_linked_entity`` stands in: a new or changed link takes
``FOR KEY SHARE`` on the linked row — so a concurrent hard delete, which locks
that row ``FOR UPDATE`` before checking for inbound links, waits for this
write to commit and then sees it — and a link to a missing row raises
``foreign_key_violation``, the error a real FK would raise.
"""

import asyncpg
import pytest
import pytest_asyncio

from src.core.db import generate_id

pytestmark = [pytest.mark.integration]


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


async def _org(conn) -> str:
    oid = generate_id()
    await conn.execute("INSERT INTO organizations (id) VALUES ($1)", oid)
    return oid


async def _person(conn) -> str:
    pid = generate_id()
    await conn.execute("INSERT INTO people (id) VALUES ($1)", pid)
    return pid


async def _link(conn, entity_type, entity_id, slug, linked_type, linked_id) -> str:
    eid = generate_id()
    await conn.execute(
        """INSERT INTO entity_events
               (id, entity_type, entity_id, event_type_id, linked_entity_type, linked_entity_id)
           SELECT $1, $2, $3, t.id, $5, $6 FROM entity_event_types t WHERE t.slug = $4""",
        eid,
        entity_type,
        entity_id,
        slug,
        linked_type,
        linked_id,
    )
    return eid


@pytest.mark.parametrize(
    "entity_type,slug,linked_type",
    [("organization", "succeeded_by", "organization"), ("person", "marriage", "person")],
)
async def test_link_to_a_missing_entity_is_a_foreign_key_violation(
    conn, entity_type, slug, linked_type
):
    owner = await (_org(conn) if entity_type == "organization" else _person(conn))
    with pytest.raises(asyncpg.ForeignKeyViolationError):
        await _link(conn, entity_type, owner, slug, linked_type, generate_id())


async def test_relink_to_a_missing_entity_is_a_foreign_key_violation(conn):
    pred, succ = await _org(conn), await _org(conn)
    eid = await _link(conn, "organization", pred, "succeeded_by", "organization", succ)
    with pytest.raises(asyncpg.ForeignKeyViolationError):
        await conn.execute(
            "UPDATE entity_events SET linked_entity_id = $1 WHERE id = $2", generate_id(), eid
        )


async def test_link_type_must_match_the_linked_row(conn):
    """A person id under ``linked_entity_type='organization'`` names no organization."""
    pred, person = await _org(conn), await _person(conn)
    with pytest.raises(asyncpg.ForeignKeyViolationError):
        await _link(conn, "organization", pred, "succeeded_by", "organization", person)


async def test_an_unchanged_link_does_not_recheck(conn):
    """Editing other fields of an event whose link already dangles stays possible.

    The admin edit form rewrites every column, the link included; it must not
    force a repoint just to fix a note (the form skips re-validation for the
    same reason).
    """
    pred, succ = await _org(conn), await _org(conn)
    eid = await _link(conn, "organization", pred, "succeeded_by", "organization", succ)
    await conn.execute("DELETE FROM organizations WHERE id = $1", succ)  # no FK: dangles

    await conn.execute(
        "UPDATE entity_events SET notes = 'fixed', linked_entity_type = 'organization',"
        " linked_entity_id = $1 WHERE id = $2",
        succ,
        eid,
    )
    assert await conn.fetchval("SELECT notes FROM entity_events WHERE id = $1", eid) == "fixed"


async def test_clearing_a_link_is_allowed(conn):
    owner = await _org(conn)
    other = await _org(conn)
    eid = await _link(conn, "organization", owner, "other", "organization", other)
    await conn.execute(
        "UPDATE entity_events SET linked_entity_type = NULL, linked_entity_id = NULL WHERE id = $1",
        eid,
    )
    assert (
        await conn.fetchval("SELECT linked_entity_id FROM entity_events WHERE id = $1", eid) is None
    )


async def test_a_new_link_holds_key_share_on_the_linked_row(db_pool):
    """The lock a concurrent ``FOR UPDATE`` (the hard delete's) must wait on.

    A person link, because an organization link already locks its target through
    the org-touch trigger's ``UPDATE`` — that would pass with no new lock at all.
    """
    async with db_pool.acquire() as writer, db_pool.acquire() as deleter:
        # Committed, so the second connection can see (and try to lock) them.
        spouse, target = await _person(writer), await _person(writer)
        try:
            async with writer.transaction():
                await _link(writer, "person", spouse, "marriage", "person", target)

                async with deleter.transaction():
                    await deleter.execute("SET LOCAL lock_timeout = '200ms'")
                    with pytest.raises(asyncpg.LockNotAvailableError):
                        await deleter.execute(
                            "SELECT 1 FROM people WHERE id = $1 FOR UPDATE", target
                        )
                raise _Rollback
        except _Rollback:
            pass
        finally:
            await writer.execute("DELETE FROM people WHERE id = ANY($1::text[])", [spouse, target])


class _Rollback(Exception):
    """Unwinds the writer's transaction once the lock has been observed."""
