"""Integration tests for src.core.observation.resolve_entity.

Uses seeded entity_identifier_types from schema.sql:
  - 'person_wa_legislature_member_id'  (entity_type=person)
  - 'org_ubi'                          (entity_type=organization)
  - 'jur_ocd'                          (entity_type=jurisdiction)
  - 'jur_slug'                         (entity_type=jurisdiction)
"""

import pytest
import pytest_asyncio

from src.core.db import generate_id
from src.core.observation import Disposition, resolve_entity

pytestmark = [
    pytest.mark.integration,
]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(loop_scope="session")
async def db(db_pool):
    """Pool-acquired connection wrapped in a rolled-back transaction."""
    async with db_pool.acquire() as conn:
        tr = conn.transaction()
        await tr.start()
        try:
            yield conn
        finally:
            await tr.rollback()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_auto_attached_returns_existing_entity(db):
    """Identifier already exists → AUTO_ATTACHED with original entity_id."""
    person_id = generate_id()
    await db.execute("INSERT INTO people (id) VALUES ($1)", person_id)

    eit_row = await db.fetchrow(
        "SELECT id FROM entity_identifier_types WHERE slug = $1",
        "person_wa_legislature_member_id",
    )
    assert eit_row is not None

    identifier_id = generate_id()
    await db.execute(
        "INSERT INTO identifiers (id, entity_id, entity_identifier_type_id, value)"
        " VALUES ($1, $2, $3, $4)",
        identifier_id,
        person_id,
        eit_row["id"],
        "WA-LEGISLATOR-001",
    )

    entity_id, entity_type, disposition = await resolve_entity(
        db, "person_wa_legislature_member_id", "WA-LEGISLATOR-001"
    )

    assert entity_id == person_id
    assert entity_type == "person"
    assert disposition == Disposition.AUTO_ATTACHED


async def test_new_person_creates_rows(db):
    """Unknown identifier value for person slug → NEW, creates people + identifiers rows."""
    entity_id, entity_type, disposition = await resolve_entity(
        db, "person_wa_legislature_member_id", "WA-LEGISLATOR-NEW-999"
    )

    assert disposition == Disposition.NEW
    assert entity_type == "person"
    assert entity_id != ""

    # Verify people row was created
    person_row = await db.fetchrow("SELECT id FROM people WHERE id = $1", entity_id)
    assert person_row is not None

    # Verify identifier row was created
    eit_row = await db.fetchrow(
        "SELECT id FROM entity_identifier_types WHERE slug = $1",
        "person_wa_legislature_member_id",
    )
    id_row = await db.fetchrow(
        "SELECT entity_id FROM identifiers WHERE entity_identifier_type_id = $1 AND value = $2",
        eit_row["id"],
        "WA-LEGISLATOR-NEW-999",
    )
    assert id_row is not None
    assert id_row["entity_id"] == entity_id


async def test_new_organization_creates_rows(db):
    """Unknown identifier value for org slug → NEW, creates organizations + identifiers rows."""
    entity_id, entity_type, disposition = await resolve_entity(db, "org_ubi", "UBI-TEST-88888")

    assert disposition == Disposition.NEW
    assert entity_type == "organization"
    assert entity_id != ""

    # Verify organizations row was created
    org_row = await db.fetchrow("SELECT id FROM organizations WHERE id = $1", entity_id)
    assert org_row is not None

    # Verify identifier row was created
    eit_row = await db.fetchrow("SELECT id FROM entity_identifier_types WHERE slug = $1", "org_ubi")
    id_row = await db.fetchrow(
        "SELECT entity_id FROM identifiers WHERE entity_identifier_type_id = $1 AND value = $2",
        eit_row["id"],
        "UBI-TEST-88888",
    )
    assert id_row is not None
    assert id_row["entity_id"] == entity_id


async def test_rejected_unknown_slug(db):
    """Unknown identifier_type_slug → REJECTED, returns empty strings."""
    entity_id, entity_type, disposition = await resolve_entity(
        db, "nonexistent_slug_xyz", "ANY-VALUE"
    )

    assert disposition == Disposition.REJECTED
    assert entity_id == ""
    assert entity_type == ""


async def test_idempotent_second_call_returns_auto_attached(db):
    """Two calls with same inputs → second call returns AUTO_ATTACHED with same entity_id."""
    first_id, first_type, first_disp = await resolve_entity(
        db, "person_wa_legislature_member_id", "WA-LEGISLATOR-IDEM-42"
    )
    assert first_disp == Disposition.NEW

    second_id, second_type, second_disp = await resolve_entity(
        db, "person_wa_legislature_member_id", "WA-LEGISLATOR-IDEM-42"
    )
    assert second_disp == Disposition.AUTO_ATTACHED
    assert second_id == first_id
    assert second_type == first_type


# ---------------------------------------------------------------------------
# jur_slug self-registration
# ---------------------------------------------------------------------------


async def test_new_jurisdiction_via_jur_ocd_auto_registers_jur_slug(db):
    """NEW via jur_ocd → jur_slug identifier row auto-inserted with the jurisdiction slug value."""
    suffix = generate_id()[:8].lower()
    slug = f"test-jur-ocd-{suffix}"
    create_data = {"slug": slug, "name": f"Test OCD {suffix}", "type_slug": "state"}

    entity_id, entity_type, disposition = await resolve_entity(
        db,
        "jur_ocd",
        f"ocd-division/country:us/test:{suffix}",
        create_data=create_data,
    )

    assert disposition == Disposition.NEW
    assert entity_type == "jurisdiction"

    slug_row = await db.fetchrow(
        """SELECT i.value FROM identifiers i
           JOIN entity_identifier_types t ON t.id = i.entity_identifier_type_id
           WHERE i.entity_id = $1 AND t.slug = 'jur_slug'""",
        entity_id,
    )
    assert slug_row is not None, "jur_slug identifier was not auto-registered"
    assert slug_row["value"] == slug


async def test_new_jurisdiction_via_jur_slug_no_duplicate_identifier(db):
    """NEW via jur_slug → exactly one jur_slug identifier row, not two."""
    suffix = generate_id()[:8].lower()
    slug = f"test-jur-slug-{suffix}"
    create_data = {"slug": slug, "name": f"Test Slug {suffix}", "type_slug": "state"}

    entity_id, entity_type, disposition = await resolve_entity(
        db, "jur_slug", slug, create_data=create_data
    )

    assert disposition == Disposition.NEW
    assert entity_type == "jurisdiction"

    count = await db.fetchval(
        """SELECT COUNT(*) FROM identifiers i
           JOIN entity_identifier_types t ON t.id = i.entity_identifier_type_id
           WHERE i.entity_id = $1 AND t.slug = 'jur_slug'""",
        entity_id,
    )
    assert count == 1


async def test_auto_attach_via_jur_slug_after_jur_ocd_creation(db):
    """Jurisdiction created via jur_ocd → subsequent resolve via jur_slug returns AUTO_ATTACHED."""
    suffix = generate_id()[:8].lower()
    slug = f"test-jur-attach-{suffix}"
    create_data = {"slug": slug, "name": f"Test Attach {suffix}", "type_slug": "state"}

    entity_id, _, first_disp = await resolve_entity(
        db,
        "jur_ocd",
        f"ocd-division/country:us/test:{suffix}",
        create_data=create_data,
    )
    assert first_disp == Disposition.NEW

    attached_id, entity_type, disp = await resolve_entity(db, "jur_slug", slug)
    assert disp == Disposition.AUTO_ATTACHED
    assert attached_id == entity_id
    assert entity_type == "jurisdiction"
