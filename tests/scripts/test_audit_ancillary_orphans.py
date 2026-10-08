"""Tests for the daily ancillary-orphan audit (#324, #326, #609)."""

import logging

import pytest
import pytest_asyncio

import scripts.audit_ancillary_orphans as audit_module
from scripts.audit_ancillary_orphans import audit
from src.core.ancillary_migrate import ENTITY_TABLES
from src.core.db import generate_id
from tests.polymorphic_seeders import archived_entity, seed_citations, seed_identifiers


@pytest_asyncio.fixture(loop_scope="session")
async def db(db_pool):
    async with db_pool.acquire() as conn:
        tr = conn.transaction()
        await tr.start()
        try:
            yield conn
        finally:
            await tr.rollback()


@pytest.mark.integration
@pytest.mark.parametrize("entity_type", ["person", "organization", "jurisdiction"])
async def test_flags_an_identifier_off_a_deleted_entity(db, caplog, entity_type):
    """#609: person/org/jurisdiction rows count, not only role and role_assignment."""
    eid = await archived_entity(db, entity_type)
    await seed_identifiers(db, entity_type, eid)
    await seed_citations(db, "organization", generate_id())  # the citation scope stays
    await db.execute(f"DELETE FROM {ENTITY_TABLES[entity_type]} WHERE id = $1", eid)

    with caplog.at_level(logging.WARNING):
        assert await audit(db) == 3

    assert f"{entity_type}.identifiers=" in caplog.text
    assert "citation.organization=" in caplog.text


async def test_clean_counts_exit_zero(monkeypatch, caplog):
    """CR 5: the timer's green half — all-zero counts log clean and exit 0."""

    async def _zero_rows(conn):
        return {"organization.links": 0, "person.entity_events_linked": 0}

    async def _zero_citations(conn):
        return {"organization": 0}

    monkeypatch.setattr(audit_module, "count_orphaned_polymorphic_rows", _zero_rows)
    monkeypatch.setattr(audit_module, "count_orphaned_citations", _zero_citations)

    with caplog.at_level(logging.INFO):
        assert await audit(None) == 0

    assert "clean (0 orphans)" in caplog.text
