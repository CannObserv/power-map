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

import asyncpg
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from src.api.admin.deps import get_db
from src.api.main import app
from src.core.db import generate_id
from src.core.observation import Disposition, resolve_entity

pytestmark = [pytest.mark.integration]
HTMX_HEADERS = {
    "X-ExeDev-UserID": "usr_test",
    "X-ExeDev-Email": "admin@test.com",
    "HX-Request": "true",
}

#: Polymorphic tables whose rows outlive their entity on purpose.
SURVIVORS = {
    "deleted_entities": "the tombstone itself",
    "entity_changes": "the outbox the 'deleted' signal travels on",
    "api_key_entity_subscriptions": "the change feed joins it to deliver the tombstone",
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


async def _archived_entity(db, entity_type: str) -> str:
    """One archived entity of ``entity_type``, with whatever parents it needs."""
    oid, pid, rid = generate_id(), generate_id(), generate_id()
    if entity_type in ("organization", "role", "role_assignment"):
        await db.execute("INSERT INTO organizations (id) VALUES ($1)", oid)
    if entity_type in ("person", "role_assignment"):
        await db.execute("INSERT INTO people (id) VALUES ($1)", pid)
    if entity_type in ("role", "role_assignment"):
        await db.execute(
            "INSERT INTO roles (id, organization_id, title) VALUES ($1, $2, 'Member')", rid, oid
        )
    if entity_type == "jurisdiction":
        jid = generate_id()
        jur_type = await db.fetchval("SELECT id FROM jurisdiction_types WHERE slug = 'county'")
        await db.execute(
            "INSERT INTO jurisdictions (id, slug, name, type_id)"
            " VALUES ($1, $2, 'Gone County', $3)",
            jid,
            f"gone-{jid.lower()}",
            jur_type,
        )
        target = jid
    elif entity_type == "role_assignment":
        target = generate_id()
        await db.execute(
            "INSERT INTO role_assignments (id, person_id, role_id) VALUES ($1, $2, $3)",
            target,
            pid,
            rid,
        )
    else:
        target = {"organization": oid, "person": pid, "role": rid}[entity_type]
    table = {
        "person": "people",
        "organization": "organizations",
        "jurisdiction": "jurisdictions",
        "role": "roles",
        "role_assignment": "role_assignments",
    }[entity_type]
    await db.execute(f"UPDATE {table} SET archived_at = now() WHERE id = $1", target)
    return target


# --- Seeders: one row per polymorphic table, returning [(table, row_id), ...] ---
# A seeder raising CheckViolation means the table does not admit that type.


async def _seed_links(db, et, eid):
    lt = await db.fetchval("SELECT id FROM link_types ORDER BY id LIMIT 1")
    i = generate_id()
    await db.execute(
        "INSERT INTO links (id, entity_type, entity_id, url, link_type_id)"
        " VALUES ($1, $2, $3, 'https://gone.example/', $4)",
        i,
        et,
        eid,
        lt,
    )
    return [("links", i)]


async def _seed_contact_methods(db, et, eid):
    i = generate_id()
    await db.execute(
        "INSERT INTO contact_methods (id, entity_type, entity_id, contact_type, value)"
        " VALUES ($1, $2, $3, 'email', 'gone@example.org')",
        i,
        et,
        eid,
    )
    return [("contact_methods", i)]


async def _seed_identifiers(db, et, eid):
    type_id = await db.fetchval(
        "SELECT id FROM entity_identifier_types WHERE entity_type = $1 ORDER BY id LIMIT 1", et
    )
    if type_id is None:  # role: no identifier type keys it
        return []
    i = generate_id()
    await db.execute(
        "INSERT INTO identifiers (id, entity_id, entity_identifier_type_id, value)"
        " VALUES ($1, $2, $3, $4)",
        i,
        eid,
        type_id,
        f"GONE-{i}",
    )
    return [("identifiers", i)]


async def _seed_citations(db, et, eid):
    i = generate_id()
    await db.execute(
        "INSERT INTO citations (id, entity_type, entity_id, url)"
        " VALUES ($1, $2, $3, 'https://gone.example/cite')",
        i,
        et,
        eid,
    )
    seeded = [("citations", i)]
    if et == "person":  # a citation on one of the person's names
        name_id, cite_id = generate_id(), generate_id()
        await db.execute(
            "INSERT INTO person_names (id, person_id, name, is_canonical)"
            " VALUES ($1, $2, 'Gone Person', TRUE)",
            name_id,
            eid,
        )
        await db.execute(
            "INSERT INTO citations (id, entity_type, entity_id, url)"
            " VALUES ($1, 'person_name', $2, 'https://gone.example/name')",
            cite_id,
            name_id,
        )
        seeded.append(("citations", cite_id))
    return seeded


async def _seed_entity_events(db, et, eid):
    type_id = await db.fetchval(
        "SELECT id FROM entity_event_types"
        " WHERE applies_to IN ($1, 'both') AND NOT requires_linked_entity ORDER BY id LIMIT 1",
        et,
    )
    ev_id, cite_id = generate_id(), generate_id()
    await db.execute(
        "INSERT INTO entity_events (id, entity_type, entity_id, event_type_id, event_year)"
        " VALUES ($1, $2, $3, $4, 2000)",
        ev_id,
        et,
        eid,
        type_id,
    )
    await db.execute(
        "INSERT INTO citations (id, entity_type, entity_id, url)"
        " VALUES ($1, 'entity_event', $2, 'https://gone.example/event')",
        cite_id,
        ev_id,
    )
    return [("entity_events", ev_id), ("citations", cite_id)]


async def _seed_entity_addresses(db, et, eid):
    i, address_id = generate_id(), generate_id()
    await db.execute(
        "INSERT INTO addresses (id, address_line_1, city, postal_code, country)"
        " VALUES ($1, '1 Gone St', 'Olympia', '98501', 'US')",
        address_id,
    )
    await db.execute(
        "INSERT INTO entity_addresses (id, entity_type, entity_id, address_id, address_type)"
        " VALUES ($1, $2, $3, $4, 'mailing')",
        i,
        et,
        eid,
        address_id,
    )
    return [("entity_addresses", i), ("addresses", address_id)]


async def _seed_field_confidence(db, et, eid):
    i = generate_id()
    await db.execute(
        "INSERT INTO field_confidence (id, entity_type, entity_id, field_name, value_hash,"
        " source_reliability, validation_status)"
        " VALUES ($1, $2, $3, 'notes', 'h', 0.5, 'not_attempted')",
        i,
        et,
        eid,
    )
    return [("field_confidence", i)]


async def _seed_import_provenance(db, et, eid):
    batch_id, i = generate_id(), generate_id()
    await db.execute(
        "INSERT INTO import_batches (id, source_file, file_hash, row_count, loaded_count,"
        " error_count) VALUES ($1, 'gone.csv', 'h', 1, 1, 0)",
        batch_id,
    )
    await db.execute(
        "INSERT INTO import_provenance (id, batch_id, source_row, entity_type, entity_id,"
        " action, raw_data) VALUES ($1, $2, 1, $3, $4, 'created', '{}')",
        i,
        batch_id,
        et,
        eid,
    )
    return [("import_provenance", i)]


async def _seed_curation_overlay(db, et, eid):
    # The overlay spells an assignment the producer crosswalk's way.
    overlay_type = "assignment" if et == "role_assignment" else et
    i = generate_id()
    await db.execute(
        "INSERT INTO curation_overlay (id, entity_type, entity_id, field, value)"
        " VALUES ($1, $2, $3, 'notes', 'pinned')",
        i,
        overlay_type,
        eid,
    )
    return [("curation_overlay", i)]


SEEDERS = {
    "links": _seed_links,
    "contact_methods": _seed_contact_methods,
    "identifiers": _seed_identifiers,
    "citations": _seed_citations,
    "entity_events": _seed_entity_events,
    "entity_addresses": _seed_entity_addresses,
    "field_confidence": _seed_field_confidence,
    "import_provenance": _seed_import_provenance,
    "curation_overlay": _seed_curation_overlay,
}


async def _polymorphic_tables(db) -> set[str]:
    """Every base table carrying an ``entity_id`` column."""
    rows = await db.fetch(
        "SELECT c.table_name FROM information_schema.columns c"
        " JOIN information_schema.tables t USING (table_schema, table_name)"
        " WHERE c.table_schema = 'public' AND t.table_type = 'BASE TABLE'"
        "   AND c.column_name = 'entity_id'"
    )
    return {r["table_name"] for r in rows}


async def test_every_polymorphic_table_is_seeded_or_survives(db):
    """Ratchet: a new polymorphic table needs a seeder here, or a reason in SURVIVORS."""
    unaccounted = await _polymorphic_tables(db) - SEEDERS.keys() - SURVIVORS.keys()
    assert not unaccounted, (
        f"{sorted(unaccounted)} key rows on entity_id with no FK. Add a seeder (and drop"
        " the rows in delete_entity_ancillary), or say in SURVIVORS why they outlive"
        " their entity (#605)."
    )


@pytest.mark.parametrize("entity_type", sorted(ROUTES))
async def test_hard_delete_leaves_no_polymorphic_row(client, db, entity_type):
    eid = await _archived_entity(db, entity_type)
    seeded: list[tuple[str, str]] = []
    for seed in SEEDERS.values():
        try:
            async with db.transaction():
                seeded += await seed(db, entity_type, eid)
        except asyncpg.CheckViolationError:
            pass  # the table's CHECK does not admit this entity type
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
    for table in sorted(await _polymorphic_tables(db) - SURVIVORS.keys()):
        assert not await db.fetchval(f"SELECT 1 FROM {table} WHERE entity_id = $1", eid), (
            f"{table} still names the deleted {entity_type}"
        )


async def test_producer_reobserving_a_deleted_identifier_gets_a_new_entity(client, db):
    """Across the seam: resolve_entity mints afresh, not ``organization_archived`` (#481)."""
    eid = await _archived_entity(db, "organization")
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
    gone = await _archived_entity(db, "organization")
    kept = await _archived_entity(db, "organization")
    [(_, link_id), (_, address_id)] = await _seed_entity_addresses(db, "organization", gone)
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
