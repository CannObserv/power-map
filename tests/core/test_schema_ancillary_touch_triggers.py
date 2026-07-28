"""Touch-cascade triggers on contact_methods / links / identifiers (#327).

These polymorphic ancillary tables signal the parent's ``entity_changes`` outbox
via a DB trigger — the same model as ``entity_addresses`` — so **any** write path
(admin CRUD, public observation, merge re-homing, or a direct INSERT from a
script) notifies subscribers uniformly. This suite exercises the trigger through
raw SQL, i.e. the path an app-layer emit would miss.

``contact_methods`` / ``links`` gained a trigger in #327 (previously none — the
public + merge paths emitted manually); the identifier trigger gained its
``role_assignment`` branch (org/person/jurisdiction were already covered).
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


async def _role_assignment(db) -> str:
    oid, rid, pid, raid = (generate_id() for _ in range(4))
    await db.execute("INSERT INTO organizations (id) VALUES ($1)", oid)
    await db.execute(
        "INSERT INTO roles (id, organization_id, title) VALUES ($1, $2, 'Director')", rid, oid
    )
    await db.execute("INSERT INTO people (id) VALUES ($1)", pid)
    await db.execute(
        "INSERT INTO role_assignments (id, person_id, role_id, is_current)"
        " VALUES ($1, $2, $3, TRUE)",
        raid,
        pid,
        rid,
    )
    return raid


async def _signals(db, entity_type: str, entity_id: str) -> int:
    return await db.fetchval(
        "SELECT count(*) FROM entity_changes"
        " WHERE entity_type=$1 AND entity_id=$2 AND change_kind='updated'",
        entity_type,
        entity_id,
    )


# ── trigger registration ─────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "table,trigger",
    [
        ("contact_methods", "trg_touch_entity_on_contact_change"),
        ("links", "trg_touch_entity_on_link_change"),
    ],
)
async def test_trigger_registered_for_all_events(db, table, trigger):
    """AFTER INSERT OR UPDATE OR DELETE (tgtype bits 4|8|16)."""
    row = await db.fetchrow(
        """
        SELECT tgtype FROM pg_trigger t
        JOIN pg_class c ON c.oid = t.tgrelid
        WHERE c.relname = $1 AND t.tgname = $2 AND t.tgenabled != 'D'
        """,
        table,
        trigger,
    )
    assert row is not None, f"{trigger} not registered on {table}"
    for bit, event in ((4, "INSERT"), (8, "DELETE"), (16, "UPDATE")):
        assert row["tgtype"] & bit, f"{trigger} not registered for {event}"


# ── contact_methods: INSERT / UPDATE / DELETE all cascade ────────────────────


async def test_contact_insert_update_delete_emit(db):
    oid = generate_id()
    await db.execute("INSERT INTO organizations (id) VALUES ($1)", oid)
    cid = generate_id()

    before = await _signals(db, "organization", oid)
    await db.execute(
        "INSERT INTO contact_methods (id, entity_type, entity_id, contact_type, value)"
        " VALUES ($1, 'organization', $2, 'email', 'a@example.gov')",
        cid,
        oid,
    )
    assert await _signals(db, "organization", oid) == before + 1

    before = await _signals(db, "organization", oid)
    await db.execute("UPDATE contact_methods SET value='b@example.gov' WHERE id=$1", cid)
    assert await _signals(db, "organization", oid) == before + 1

    before = await _signals(db, "organization", oid)
    await db.execute("DELETE FROM contact_methods WHERE id=$1", cid)
    assert await _signals(db, "organization", oid) == before + 1


async def test_contact_trigger_dispatches_role_assignment(db):
    """The polymorphic dispatch reaches role_assignment (the #324 no-trigger case)."""
    raid = await _role_assignment(db)
    before = await _signals(db, "role_assignment", raid)
    await db.execute(
        "INSERT INTO contact_methods (id, entity_type, entity_id, contact_type, value)"
        " VALUES ($1, 'role_assignment', $2, 'email', 'dir@wslcb.wa.gov')",
        generate_id(),
        raid,
    )
    assert await _signals(db, "role_assignment", raid) == before + 1


# ── links: INSERT / UPDATE / DELETE cascade; role dispatch ───────────────────


async def test_link_insert_update_delete_emit(db):
    pid = generate_id()
    await db.execute("INSERT INTO people (id) VALUES ($1)", pid)
    lt_id = await db.fetchval("SELECT id FROM link_types WHERE slug='website'")
    lid = generate_id()

    before = await _signals(db, "person", pid)
    await db.execute(
        "INSERT INTO links (id, entity_type, entity_id, url, link_type_id)"
        " VALUES ($1, 'person', $2, 'https://example.gov/p', $3)",
        lid,
        pid,
        lt_id,
    )
    assert await _signals(db, "person", pid) == before + 1

    before = await _signals(db, "person", pid)
    await db.execute("UPDATE links SET url='https://example.gov/p2' WHERE id=$1", lid)
    assert await _signals(db, "person", pid) == before + 1

    before = await _signals(db, "person", pid)
    await db.execute("DELETE FROM links WHERE id=$1", lid)
    assert await _signals(db, "person", pid) == before + 1


async def test_link_trigger_dispatches_role(db):
    oid, rid = generate_id(), generate_id()
    await db.execute("INSERT INTO organizations (id) VALUES ($1)", oid)
    await db.execute(
        "INSERT INTO roles (id, organization_id, title) VALUES ($1, $2, 'Chair')", rid, oid
    )
    lt_id = await db.fetchval("SELECT id FROM link_types WHERE slug='website'")
    before = await _signals(db, "role", rid)
    await db.execute(
        "INSERT INTO links (id, entity_type, entity_id, url, link_type_id)"
        " VALUES ($1, 'role', $2, 'https://example.gov/r', $3)",
        generate_id(),
        rid,
        lt_id,
    )
    assert await _signals(db, "role", rid) == before + 1


# ── identifiers: role_assignment branch added in #327 ────────────────────────


async def test_identifier_trigger_now_dispatches_role_assignment(db):
    raid = await _role_assignment(db)
    type_id = await db.fetchval(
        "SELECT id FROM entity_identifier_types"
        " WHERE entity_type='role_assignment' AND slug='role_wa_pdc'"
    )
    before = await _signals(db, "role_assignment", raid)
    await db.execute(
        "INSERT INTO identifiers (id, entity_id, entity_identifier_type_id, value)"
        " VALUES ($1, $2, $3, '12345')",
        generate_id(),
        raid,
        type_id,
    )
    assert await _signals(db, "role_assignment", raid) == before + 1
