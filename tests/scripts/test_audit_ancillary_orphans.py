"""Tests for the daily ancillary-orphan audit (#324, #326, #609)."""

import logging

import pytest
import pytest_asyncio

from scripts.audit_ancillary_orphans import audit
from src.core.db import generate_id
from tests.polymorphic_seeders import archived_entity, seed_citations, seed_identifiers

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


@pytest.mark.parametrize("entity_type", ["person", "organization", "jurisdiction"])
async def test_flags_an_identifier_off_a_deleted_entity(db, caplog, entity_type):
    """#609: person/org/jurisdiction rows count, not only role and role_assignment."""
    eid = await archived_entity(db, entity_type)
    await seed_identifiers(db, entity_type, eid)
    await seed_citations(db, "organization", generate_id())  # the citation scope stays
    table = {"person": "people", "organization": "organizations"}.get(entity_type, "jurisdictions")
    await db.execute(f"DELETE FROM {table} WHERE id = $1", eid)

    with caplog.at_level(logging.WARNING):
        assert await audit(db) == 3

    assert f"{entity_type}.identifiers=" in caplog.text
    assert "citation.organization=" in caplog.text
