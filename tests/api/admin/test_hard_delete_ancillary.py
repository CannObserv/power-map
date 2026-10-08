"""#605: an admin hard delete leaves no polymorphic row pointing at its entity.

The polymorphic tables key a row on ``(entity_type, entity_id)`` — identifiers
through ``entity_identifier_types`` — with no FK, so nothing cascades. A row left
behind names a deleted id: ``resolve_entity`` then rejects a producer's identifier
as ``<type>_archived`` (an unarchive that cannot happen) instead of minting a
new entity, for good. Merges are not in scope here: they re-home onto the
survivor (#467).

Each hard-delete route is driven end to end with a row seeded in every
polymorphic table its entity type admits; the ratchet below fails when a new
polymorphic table appears without a seeder or a stated reason to survive.
"""

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from src.api.admin.deps import get_db
from src.api.main import app
from src.core.db import generate_id
from src.core.observation import Disposition, resolve_entity
from tests.polymorphic_seeders import (
    SEEDERS,
    SURVIVORS,
    archived_entity,
    polymorphic_tables,
    seed_entity_addresses,
    seed_entity_events,
    seed_every_table,
)

pytestmark = [pytest.mark.integration]
HTMX_HEADERS = {
    "X-ExeDev-UserID": "usr_test",
    "X-ExeDev-Email": "admin@test.com",
    "HX-Request": "true",
}

#: The admin URL segment for each hard-deletable entity type.
ROUTES = {
    "person": "people",
    "organization": "orgs",
    "jurisdiction": "jurisdictions",
    "role": "roles",
    "role_assignment": "role-assignments",
}


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
async def client(db):
    async def _get_db_override():
        yield db

    app.dependency_overrides[get_db] = _get_db_override
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    app.dependency_overrides.pop(get_db, None)


async def test_every_polymorphic_table_is_seeded_or_survives(db):
    """Ratchet: a new polymorphic table needs a seeder, or a reason in SURVIVORS."""
    unaccounted = await polymorphic_tables(db) - SEEDERS.keys() - SURVIVORS.keys()
    assert not unaccounted, (
        f"{sorted(unaccounted)} key rows on entity_id with no FK. Add a seeder in"
        " tests/polymorphic_seeders.py (drop the rows in delete_entity_ancillary, count"
        " them in count_orphaned_polymorphic_rows), or say in SURVIVORS why they"
        " outlive their entity (#605, #609)."
    )


@pytest.mark.parametrize("entity_type", sorted(ROUTES))
async def test_hard_delete_leaves_no_polymorphic_row(client, db, entity_type):
    eid = await archived_entity(db, entity_type)
    seeded = await seed_every_table(db, entity_type, eid)
    if entity_type != "role":  # #605's case: the identifier must actually be there
        assert "identifiers" in {t for t, _ in seeded}

    r = await client.delete(f"/admin/{ROUTES[entity_type]}/{eid}/", headers=HTMX_HEADERS)
    assert r.status_code == 200, r.text

    survivors = [
        (table, row_id)
        for table, row_id in seeded
        if await db.fetchval(f"SELECT 1 FROM {table} WHERE id = $1", row_id)
    ]
    assert not survivors, f"{entity_type} hard delete left {survivors}"
    for table in sorted(await polymorphic_tables(db) - SURVIVORS.keys()):
        assert not await db.fetchval(f"SELECT 1 FROM {table} WHERE entity_id = $1", eid), (
            f"{table} still names the deleted {entity_type}"
        )


async def test_producer_reobserving_a_deleted_identifier_gets_a_new_entity(client, db):
    """Across the seam: resolve_entity mints afresh, not ``organization_archived`` (#481)."""
    eid = await archived_entity(db, "organization")
    value = f"UBI-GONE-{eid}"
    await db.execute(
        "INSERT INTO identifiers (id, entity_id, entity_identifier_type_id, value)"
        " SELECT $1, $2, id, $3 FROM entity_identifier_types WHERE slug = 'org_ubi'",
        generate_id(),
        eid,
        value,
    )

    r = await client.delete(f"/admin/orgs/{eid}/", headers=HTMX_HEADERS)
    assert r.status_code == 200

    new_id, _, disposition, reason = await resolve_entity(db, "org_ubi", value)
    assert disposition == Disposition.NEW, reason
    assert new_id != eid


async def test_hard_delete_keeps_an_address_another_entity_shares(client, db):
    """Only the deleted entity's link goes; an address still linked elsewhere stays."""
    gone = await archived_entity(db, "organization")
    kept = await archived_entity(db, "organization")
    [(_, link_id), (_, address_id)] = await seed_entity_addresses(db, "organization", gone)
    await db.execute(
        "INSERT INTO entity_addresses (id, entity_type, entity_id, address_id, address_type)"
        " VALUES ($1, 'organization', $2, $3, 'physical')",
        generate_id(),
        kept,
        address_id,
    )

    r = await client.delete(f"/admin/orgs/{gone}/", headers=HTMX_HEADERS)
    assert r.status_code == 200

    assert not await db.fetchval("SELECT 1 FROM entity_addresses WHERE id = $1", link_id)
    assert await db.fetchval("SELECT 1 FROM addresses WHERE id = $1", address_id)


async def test_hard_delete_keeps_an_address_that_is_another_events_place(client, db):
    """An event place reference keeps the address too (its FK would SET NULL)."""
    gone = await archived_entity(db, "organization")
    kept = await archived_entity(db, "organization")
    [(_, link_id), (_, address_id)] = await seed_entity_addresses(db, "organization", gone)
    [(_, event_id), _] = await seed_entity_events(db, "organization", kept)
    await db.execute(
        "UPDATE entity_events SET event_place_address_id = $1 WHERE id = $2", address_id, event_id
    )

    r = await client.delete(f"/admin/orgs/{gone}/", headers=HTMX_HEADERS)
    assert r.status_code == 200

    assert not await db.fetchval("SELECT 1 FROM entity_addresses WHERE id = $1", link_id)
    assert (
        await db.fetchval(
            "SELECT event_place_address_id FROM entity_events WHERE id = $1", event_id
        )
        == address_id
    )
