"""Integration tests for the import pipeline."""

import os
from pathlib import Path

import asyncpg
import pytest

from src.core.db import apply_schema
from src.core.ingestion.pipeline import ImportConfig, run_import

ORGS_FIXTURE = Path("tests/fixtures/ingestion/orgs_sample.csv")
PEOPLE_FIXTURE = Path("tests/fixtures/ingestion/people_sample.csv")
ROLES_FIXTURE = Path("tests/fixtures/ingestion/roles_sample.csv")


@pytest.fixture
async def db():
    """Integration DB fixture with rollback — requires DATABASE_URL env var."""
    conn = await asyncpg.connect(os.environ["DATABASE_URL"])
    await apply_schema(conn)
    tr = conn.transaction()
    await tr.start()
    yield conn
    await tr.rollback()
    await conn.close()


@pytest.mark.integration
async def test_run_import_creates_orgs(db):
    config = ImportConfig(
        orgs_csv=ORGS_FIXTURE,
        people_csv=PEOPLE_FIXTURE,
        roles_csv=ROLES_FIXTURE,
        imported_by="test",
        source_reliability=0.8,
    )
    summary = await run_import(db, config)
    assert summary["orgs_loaded"] >= 1
    count = await db.fetchval("SELECT count(*) FROM organizations")
    assert count >= 1


@pytest.mark.integration
async def test_run_import_provenance_written(db):
    config = ImportConfig(
        orgs_csv=ORGS_FIXTURE,
        people_csv=PEOPLE_FIXTURE,
        roles_csv=ROLES_FIXTURE,
        imported_by="test",
        source_reliability=0.8,
    )
    summary = await run_import(db, config)
    count = await db.fetchval(
        "SELECT count(*) FROM import_provenance WHERE batch_id = $1",
        summary["batch_id"],
    )
    assert count >= 1


@pytest.mark.integration
async def test_run_import_field_confidence_written(db):
    config = ImportConfig(
        orgs_csv=ORGS_FIXTURE,
        people_csv=PEOPLE_FIXTURE,
        roles_csv=ROLES_FIXTURE,
        imported_by="test",
        source_reliability=0.8,
    )
    await run_import(db, config)
    count = await db.fetchval("SELECT count(*) FROM field_confidence")
    assert count >= 1
    row = await db.fetchrow("SELECT * FROM field_confidence LIMIT 1")
    assert row["source_reliability"] == pytest.approx(0.8)


@pytest.mark.integration
async def test_run_import_idempotent(db):
    """Running the same import twice yields matched, not duplicated, entities."""
    config = ImportConfig(
        orgs_csv=ORGS_FIXTURE,
        people_csv=PEOPLE_FIXTURE,
        roles_csv=ROLES_FIXTURE,
        imported_by="test",
        source_reliability=0.8,
    )
    await run_import(db, config)
    count_before = await db.fetchval("SELECT count(*) FROM organizations")
    await run_import(db, config)
    count_after = await db.fetchval("SELECT count(*) FROM organizations")
    assert count_before == count_after
    matched = await db.fetchval(
        "SELECT count(*) FROM import_provenance WHERE action = 'matched'"
    )
    assert matched >= 1


@pytest.mark.integration
async def test_run_import_bad_phone_no_contact_method(db):
    """Rows with an invalid phone produce a warning but the entity still loads.

    roles_sample.csv contains 'Bob Jones, COO' with phone='bad-phone'.
    The role_assignment must be created, and no phone contact_method written.
    """
    config = ImportConfig(
        orgs_csv=ORGS_FIXTURE,
        people_csv=PEOPLE_FIXTURE,
        roles_csv=ROLES_FIXTURE,
        imported_by="test",
        source_reliability=0.8,
    )
    summary = await run_import(db, config)
    # Bob's COO role must have loaded despite the bad phone
    assert summary["roles_loaded"] >= 1
    # No phone contact_method should exist for any role_assignment
    phone_count = await db.fetchval(
        "SELECT count(*) FROM contact_methods"
        " WHERE entity_type = 'role_assignment' AND contact_type = 'phone'"
        " AND value = 'bad-phone'"
    )
    assert phone_count == 0
