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


# The test DB is shared and these tables accrete rows other tests commit, so
# every assertion reads only this run's rows: its batch's provenance, or the
# entities that provenance names (#819).


async def _batch_entity_ids(db, batch_id: str, entity_type: str, *actions: str) -> list[str]:
    """Ids of ``entity_type`` rows the batch's provenance records under ``actions``."""
    return [
        r["entity_id"]
        for r in await db.fetch(
            "SELECT entity_id FROM import_provenance"
            " WHERE batch_id = $1 AND entity_type = $2 AND action = ANY($3::text[])",
            batch_id,
            entity_type,
            list(actions),
        )
    ]


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
    created = await _batch_entity_ids(db, summary["batch_id"], "organization", "created")
    count = await db.fetchval(
        "SELECT count(*) FROM organizations WHERE id = ANY($1::text[])", created
    )
    assert count == summary["orgs_loaded"]


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
    summary = await run_import(db, config)
    rows = await db.fetch(
        "SELECT source_reliability FROM field_confidence WHERE assessed_by = $1",
        f"import:{summary['batch_id']}",
    )
    assert rows
    assert all(r["source_reliability"] == pytest.approx(0.8) for r in rows)


async def test_run_import_idempotent(db):
    """Running the same import twice yields matched, not duplicated, entities."""
    config = ImportConfig(
        orgs_csv=ORGS_FIXTURE,
        people_csv=PEOPLE_FIXTURE,
        roles_csv=ROLES_FIXTURE,
        imported_by="test",
        source_reliability=0.8,
    )
    first = await run_import(db, config)
    created = sorted(await _batch_entity_ids(db, first["batch_id"], "organization", "created"))
    assert created
    # Same files, same hash: the re-run writes under the first run's batch.
    second = await run_import(db, config)
    after = sorted(await _batch_entity_ids(db, second["batch_id"], "organization", "created"))
    assert after == created
    matched = await _batch_entity_ids(db, second["batch_id"], "organization", "matched")
    assert sorted(matched) == created


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
    # No phone contact_method should exist for any of this batch's role_assignments
    assignments = await _batch_entity_ids(db, summary["batch_id"], "role_assignment", "created")
    phone_count = await db.fetchval(
        "SELECT count(*) FROM contact_methods"
        " WHERE entity_type = 'role_assignment' AND entity_id = ANY($1::text[])"
        " AND contact_type = 'phone' AND value = 'bad-phone'",
        assignments,
    )
    assert phone_count == 0


async def _role_count(db, org_ids: list[str]) -> int:
    return await db.fetchval(
        "SELECT count(*) FROM roles WHERE organization_id = ANY($1::text[])", org_ids
    )


async def _assignment_count(db, org_ids: list[str]) -> int:
    return await db.fetchval(
        "SELECT count(*) FROM role_assignments ra JOIN roles r ON r.id = ra.role_id"
        " WHERE r.organization_id = ANY($1::text[])",
        org_ids,
    )


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
    orgs = await _batch_entity_ids(db, summary1["batch_id"], "organization", "created", "matched")
    assert orgs

    role_count = await _role_count(db, orgs)
    assignment_count = await _assignment_count(db, orgs)

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

    role_count_after = await _role_count(db, orgs)
    assignment_count_after = await _assignment_count(db, orgs)

    assert role_count_after == role_count, "duplicate roles created on second import"
    assert assignment_count_after == assignment_count, (
        "duplicate role_assignments created on second import"
    )
    assert summary2["roles_loaded"] == 0
    assert summary2["roles_matched"] > 0


async def test_run_import_records_address_standardization_status(db):
    """#595: the CSV import records how each address was normalized.

    The suite runs without ADDRESS_VALIDATOR_API_KEY, so the import parses locally.
    """
    config = ImportConfig(
        orgs_csv=ORGS_FIXTURE,
        people_csv=PEOPLE_FIXTURE,
        roles_csv=ROLES_FIXTURE,
        imported_by="test",
        source_reliability=0.8,
        local_addresses_only=True,
    )
    summary = await run_import(db, config)
    rows = await db.fetch(
        "SELECT a.standardization_status, a.standardization_checked_at"
        " FROM import_provenance p"
        " JOIN entity_addresses ea ON ea.entity_type = p.entity_type AND ea.entity_id = p.entity_id"
        " JOIN addresses a ON a.id = ea.address_id"
        " WHERE p.batch_id = $1",
        summary["batch_id"],
    )
    assert rows
    assert {r["standardization_status"] for r in rows} == {"local"}
    assert all(r["standardization_checked_at"] is not None for r in rows)
