"""Polymorphic-row seeders shared by the #605 hard-delete and #609 orphan-audit tests.

The polymorphic tables key a row on ``(entity_type, entity_id)`` — identifiers
through ``entity_identifier_types`` — with no FK. :data:`SEEDERS` writes one row
per table for a given entity; ``tests/api/admin/test_hard_delete_ancillary.py``
ratchets it against every ``entity_id`` table, so the orphan counter's test can
trust it as the full table set.
"""

import asyncpg

from src.core.ancillary_migrate import ENTITY_TABLES
from src.core.db import generate_id
from tests.db_utils import trigger_disabled

#: Polymorphic tables whose rows outlive their entity on purpose.
SURVIVORS = {
    "deleted_entities": "the tombstone itself",
    "entity_changes": "the outbox the 'deleted' signal travels on",
    "api_key_entity_subscriptions": "the change feed joins it to deliver the tombstone",
}


async def archived_entity(db, entity_type: str) -> str:
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
    await db.execute(
        f"UPDATE {ENTITY_TABLES[entity_type]} SET archived_at = now() WHERE id = $1", target
    )
    return target


#: The #630 referenced-side identifier guard on each table an identifier can name.
IDENTIFIER_DELETE_GUARDS = {
    "person": "trg_people_identifiers",
    "organization": "trg_organizations_identifiers",
    "role_assignment": "trg_role_assignments_identifiers",
    "jurisdiction": "trg_jurisdictions_identifiers",
}


async def raw_delete(db, entity_type: str, entity_id: str) -> None:
    """Delete the entity alone, stranding its identifiers as a bypassing path once did.

    #630 refuses that delete, so its guard is off for the statement; like
    :func:`tests.db_utils.trigger_disabled`, call it only inside a rolled-back
    transaction. Other guards (#615's inbound event links) stay on.
    """
    table = ENTITY_TABLES[entity_type]
    sql = f"DELETE FROM {table} WHERE id = $1"
    guard = IDENTIFIER_DELETE_GUARDS.get(entity_type)
    if guard is None:  # role: identifiers cannot name one
        await db.execute(sql, entity_id)
        return
    async with trigger_disabled(db, table, guard):
        await db.execute(sql, entity_id)


# --- Seeders: one row per polymorphic table, returning [(table, row_id), ...] ---
# A seeder raising CheckViolation means the table does not admit that type.


async def seed_links(db, et, eid):
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


async def seed_contact_methods(db, et, eid):
    i = generate_id()
    await db.execute(
        "INSERT INTO contact_methods (id, entity_type, entity_id, contact_type, value)"
        " VALUES ($1, $2, $3, 'email', 'gone@example.org')",
        i,
        et,
        eid,
    )
    return [("contact_methods", i)]


async def seed_identifiers(db, et, eid):
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


async def seed_citations(db, et, eid):
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


async def seed_entity_events(db, et, eid):
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


async def seed_entity_addresses(db, et, eid):
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


async def seed_field_confidence(db, et, eid):
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


async def seed_import_provenance(db, et, eid):
    batch_id, i = generate_id(), generate_id()
    await db.execute(
        "INSERT INTO import_batches (id, source_file, file_hash, row_count, loaded_count,"
        " error_count) VALUES ($1, 'gone.csv', $1, 1, 1, 0)",
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


async def seed_curation_overlay(db, et, eid):
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
    "links": seed_links,
    "contact_methods": seed_contact_methods,
    "identifiers": seed_identifiers,
    "citations": seed_citations,
    "entity_events": seed_entity_events,
    "entity_addresses": seed_entity_addresses,
    "field_confidence": seed_field_confidence,
    "import_provenance": seed_import_provenance,
    "curation_overlay": seed_curation_overlay,
}


async def seed_every_table(
    db, entity_type: str, entity_id: str, *, skip: frozenset[str] = frozenset()
) -> list[tuple[str, str]]:
    """Run every seeder not in ``skip`` that ``entity_type`` admits; the rows seeded.

    Only the table's ``<table>_entity_type_check`` means "not admitted". Any other
    CHECK is a broken seeder and raises, so a test cannot lose a cell in silence.
    """
    seeded: list[tuple[str, str]] = []
    for table, seed in SEEDERS.items():
        if table in skip:
            continue
        try:
            async with db.transaction():
                seeded += await seed(db, entity_type, entity_id)
        except asyncpg.CheckViolationError as e:
            if not (e.constraint_name or "").endswith("_entity_type_check"):
                raise
    return seeded


async def polymorphic_tables(db) -> set[str]:
    """Every base table carrying an ``entity_id`` column."""
    rows = await db.fetch(
        "SELECT c.table_name FROM information_schema.columns c"
        " JOIN information_schema.tables t USING (table_schema, table_name)"
        " WHERE c.table_schema = 'public' AND t.table_type = 'BASE TABLE'"
        "   AND c.column_name = 'entity_id'"
    )
    return {r["table_name"] for r in rows}
