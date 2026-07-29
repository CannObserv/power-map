"""citations table + touch-cascade trigger (#319).

The identity unique index is NULLS NOT DISTINCT over active rows; the touch
trigger signals the parent's entity_changes outbox for every write path (the
#327 model), indirecting a person_name citation to its owning person and an
entity_event citation to the event's owning entity.
"""

import pytest
import pytest_asyncio

from src.core.db import generate_id

pytestmark = [pytest.mark.integration]


@pytest_asyncio.fixture(loop_scope="session")
async def db(db_pool):
    async with db_pool.acquire() as conn:
        tr = conn.transaction()
        await tr.start()
        try:
            yield conn
        finally:
            await tr.rollback()


async def _org(db) -> str:
    oid = generate_id()
    await db.execute("INSERT INTO organizations (id) VALUES ($1)", oid)
    return oid


async def _person(db) -> str:
    pid = generate_id()
    await db.execute("INSERT INTO people (id) VALUES ($1)", pid)
    return pid


async def _cite(db, entity_type: str, entity_id: str, *, url="https://s/x", field=None) -> str:
    cid = generate_id()
    await db.execute(
        "INSERT INTO citations (id, entity_type, entity_id, field_name, url, title)"
        " VALUES ($1,$2,$3,$4,$5,'t')",
        cid,
        entity_type,
        entity_id,
        field,
        url,
    )
    return cid


async def _signals(db, entity_type: str, entity_id: str) -> int:
    return await db.fetchval(
        "SELECT count(*) FROM entity_changes"
        " WHERE entity_type=$1 AND entity_id=$2 AND change_kind='updated'",
        entity_type,
        entity_id,
    )


# ── identity index ────────────────────────────────────────────────────────────


async def test_identity_unique_active_only(db):
    pid = await _person(db)
    await _cite(db, "person", pid, url="https://s/a", field="notes")
    with pytest.raises(Exception):  # noqa: B017 — uq_citation_identity violation
        await _cite(db, "person", pid, url="https://s/a", field="notes")


async def test_urlless_single_slot_per_entity_field(db):
    pid = await _person(db)
    await _cite(db, "person", pid, url=None, field="notes")
    with pytest.raises(Exception):  # noqa: B017 — NULL url is one distinct slot
        await _cite(db, "person", pid, url=None, field="notes")


async def test_check_url_or_title(db):
    pid = await _person(db)
    cid = generate_id()
    with pytest.raises(Exception):  # noqa: B017 — chk_citation_url_or_title
        await db.execute(
            "INSERT INTO citations (id, entity_type, entity_id) VALUES ($1,'person',$2)",
            cid,
            pid,
        )


# ── touch trigger ─────────────────────────────────────────────────────────────


async def test_insert_touches_top_level_entity(db):
    pid = await _person(db)
    before = await _signals(db, "person", pid)
    await _cite(db, "person", pid)
    assert await _signals(db, "person", pid) == before + 1


async def test_delete_touches_entity(db):
    oid = await _org(db)
    cid = await _cite(db, "organization", oid, field="notes")
    before = await _signals(db, "organization", oid)
    await db.execute("DELETE FROM citations WHERE id=$1", cid)
    assert await _signals(db, "organization", oid) == before + 1


async def test_person_name_citation_touches_owning_person(db):
    pid = await _person(db)
    nid = generate_id()
    await db.execute(
        "INSERT INTO person_names (id, person_id, name, name_type) VALUES ($1,$2,'Jo','legal')",
        nid,
        pid,
    )
    before = await _signals(db, "person", pid)
    await _cite(db, "person_name", nid, field="name")
    assert await _signals(db, "person", pid) == before + 1


async def test_entity_event_citation_touches_owning_entity(db):
    oid = await _org(db)
    eid = generate_id()
    await db.execute(
        "INSERT INTO entity_events (id, entity_type, entity_id, event_type_id)"
        " VALUES ($1,'organization',$2,(SELECT id FROM entity_event_types LIMIT 1))",
        eid,
        oid,
    )
    before = await _signals(db, "organization", oid)
    await _cite(db, "entity_event", eid, field="notes")
    assert await _signals(db, "organization", oid) == before + 1
