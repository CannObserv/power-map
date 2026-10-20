"""Touch-cascade triggers on contact_methods / links / identifiers (#327).

These polymorphic ancillary tables bump the parent's ``updated_at`` via a DB
trigger — the same model as ``entity_addresses`` — so **any** write path (admin
CRUD, public observation, merge re-homing, or a direct INSERT from a script)
moves the parent's ETag uniformly. This suite exercises the trigger through raw
SQL, i.e. the path an app-layer touch would miss. The observable is the parent
row version (``row_version``): ``updated_at`` is frozen inside one transaction.

``contact_methods`` / ``links`` gained a trigger in #327 (previously none — the
public + merge paths touched manually); the identifier trigger gained its
``role_assignment`` branch (org/person/jurisdiction were already covered).
"""

import pytest
import pytest_asyncio

from src.core.db import generate_id
from tests.db_utils import row_version

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


async def test_contact_insert_update_delete_touch_parent(db):
    oid = generate_id()
    await db.execute("INSERT INTO organizations (id) VALUES ($1)", oid)
    cid = generate_id()

    before = await row_version(db, "organizations", oid)
    await db.execute(
        "INSERT INTO contact_methods (id, entity_type, entity_id, contact_type, value)"
        " VALUES ($1, 'organization', $2, 'email', 'a@example.gov')",
        cid,
        oid,
    )
    assert await row_version(db, "organizations", oid) != before

    before = await row_version(db, "organizations", oid)
    await db.execute("UPDATE contact_methods SET value='b@example.gov' WHERE id=$1", cid)
    assert await row_version(db, "organizations", oid) != before

    before = await row_version(db, "organizations", oid)
    await db.execute("DELETE FROM contact_methods WHERE id=$1", cid)
    assert await row_version(db, "organizations", oid) != before


async def test_contact_trigger_dispatches_role_assignment(db):
    """The polymorphic dispatch reaches role_assignment (the #324 no-trigger case)."""
    raid = await _role_assignment(db)
    before = await row_version(db, "role_assignments", raid)
    await db.execute(
        "INSERT INTO contact_methods (id, entity_type, entity_id, contact_type, value)"
        " VALUES ($1, 'role_assignment', $2, 'email', 'dir@wslcb.wa.gov')",
        generate_id(),
        raid,
    )
    assert await row_version(db, "role_assignments", raid) != before


# ── links: INSERT / UPDATE / DELETE cascade; role dispatch ───────────────────


async def test_link_insert_update_delete_touch_parent(db):
    pid = generate_id()
    await db.execute("INSERT INTO people (id) VALUES ($1)", pid)
    lt_id = await db.fetchval("SELECT id FROM link_types WHERE slug='website'")
    lid = generate_id()

    before = await row_version(db, "people", pid)
    await db.execute(
        "INSERT INTO links (id, entity_type, entity_id, url, link_type_id)"
        " VALUES ($1, 'person', $2, 'https://example.gov/p', $3)",
        lid,
        pid,
        lt_id,
    )
    assert await row_version(db, "people", pid) != before

    before = await row_version(db, "people", pid)
    await db.execute("UPDATE links SET url='https://example.gov/p2' WHERE id=$1", lid)
    assert await row_version(db, "people", pid) != before

    before = await row_version(db, "people", pid)
    await db.execute("DELETE FROM links WHERE id=$1", lid)
    assert await row_version(db, "people", pid) != before


async def test_link_trigger_dispatches_role(db):
    oid, rid = generate_id(), generate_id()
    await db.execute("INSERT INTO organizations (id) VALUES ($1)", oid)
    await db.execute(
        "INSERT INTO roles (id, organization_id, title) VALUES ($1, $2, 'Chair')", rid, oid
    )
    lt_id = await db.fetchval("SELECT id FROM link_types WHERE slug='website'")
    before = await row_version(db, "roles", rid)
    await db.execute(
        "INSERT INTO links (id, entity_type, entity_id, url, link_type_id)"
        " VALUES ($1, 'role', $2, 'https://example.gov/r', $3)",
        generate_id(),
        rid,
        lt_id,
    )
    assert await row_version(db, "roles", rid) != before


# ── identifiers: role_assignment branch added in #327 ────────────────────────


async def test_identifier_trigger_now_dispatches_role_assignment(db):
    raid = await _role_assignment(db)
    type_id = await db.fetchval(
        "SELECT id FROM entity_identifier_types"
        " WHERE entity_type='role_assignment' AND slug='role_wa_pdc'"
    )
    before = await row_version(db, "role_assignments", raid)
    await db.execute(
        "INSERT INTO identifiers (id, entity_id, entity_identifier_type_id, value)"
        " VALUES ($1, $2, $3, '12345')",
        generate_id(),
        raid,
        type_id,
    )
    assert await row_version(db, "role_assignments", raid) != before


async def test_identifier_move_touches_both_orgs(db):
    """#469 CR: an identifier UPDATE that changes entity_id touches the org that
    LOST the identifier too — its payload changed, so its ETag must
    move (the restore script moves 3532 off the winner exactly this way)."""
    a, b = generate_id(), generate_id()
    await db.execute("INSERT INTO organizations (id) VALUES ($1), ($2)", a, b)
    tid = await db.fetchval(
        "SELECT id FROM entity_identifier_types"
        " WHERE entity_type='organization' AND NOT is_internal LIMIT 1"
    )
    iid = generate_id()
    await db.execute(
        "INSERT INTO identifiers (id, entity_id, entity_identifier_type_id, value)"
        " VALUES ($1, $2, $3, $4)",
        iid,
        a,
        tid,
        f"move-test-{iid}",
    )
    before_a = await row_version(db, "organizations", a)
    before_b = await row_version(db, "organizations", b)
    await db.execute("UPDATE identifiers SET entity_id=$1 WHERE id=$2", b, iid)
    assert await row_version(db, "organizations", b) != before_b  # gained it
    assert await row_version(db, "organizations", a) != before_a  # lost it
