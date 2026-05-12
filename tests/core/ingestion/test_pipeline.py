"""Integration tests for the import pipeline."""

import shutil
from pathlib import Path

import pytest
import pytest_asyncio

from src.core.ingestion.pipeline import ImportConfig, run_import

ORGS_FIXTURE = Path("tests/fixtures/ingestion/orgs_sample.csv")
PEOPLE_FIXTURE = Path("tests/fixtures/ingestion/people_sample.csv")
ROLES_FIXTURE = Path("tests/fixtures/ingestion/roles_sample.csv")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
]


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
    matched = await db.fetchval("SELECT count(*) FROM import_provenance WHERE action = 'matched'")
    assert matched >= 1


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


async def test_run_import_roles_idempotent_new_batch(db, tmp_path):
    """Re-importing with a new file hash must not create duplicate roles or assignments.

    Simulates the scenario where CSV files change (triggering a new batch_id) but
    the role data is unchanged. After the pipeline fix, role_index is pre-populated
    from the DB so existing roles are matched rather than re-created.
    """
    config1 = ImportConfig(
        orgs_csv=ORGS_FIXTURE,
        people_csv=PEOPLE_FIXTURE,
        roles_csv=ROLES_FIXTURE,
        imported_by="test",
        source_reliability=0.8,
    )
    summary1 = await run_import(db, config1)
    assert summary1["roles_loaded"] > 0

    role_count = await db.fetchval("SELECT count(*) FROM roles")
    assignment_count = await db.fetchval("SELECT count(*) FROM role_assignments")

    # Copy CSVs to tmp_path; append a blank line to orgs to change the combined hash
    orgs2 = tmp_path / "orgs2.csv"
    people2 = tmp_path / "people2.csv"
    roles2 = tmp_path / "roles2.csv"
    shutil.copy(ORGS_FIXTURE, orgs2)
    shutil.copy(PEOPLE_FIXTURE, people2)
    shutil.copy(ROLES_FIXTURE, roles2)
    with orgs2.open("a") as f:
        f.write("\n")  # changes hash; empty row fails org validation (expected)

    config2 = ImportConfig(
        orgs_csv=orgs2,
        people_csv=people2,
        roles_csv=roles2,
        imported_by="test",
        source_reliability=0.8,
    )
    summary2 = await run_import(db, config2)

    role_count_after = await db.fetchval("SELECT count(*) FROM roles")
    assignment_count_after = await db.fetchval("SELECT count(*) FROM role_assignments")

    assert role_count_after == role_count, "duplicate roles created on second import"
    assert assignment_count_after == assignment_count, (
        "duplicate role_assignments created on second import"
    )
    assert summary2["roles_loaded"] == 0
    assert summary2["roles_matched"] > 0
