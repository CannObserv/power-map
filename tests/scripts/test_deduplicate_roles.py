"""Integration tests for the deduplicate_roles migration script."""

import os

import asyncpg
import pytest

from scripts.deduplicate_roles import run_deduplication
from src.core.db import apply_schema, generate_id

pytestmark = pytest.mark.integration


@pytest.fixture
async def db():
    """Connect, apply schema, yield a connection whose DML rolls back after."""
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        pytest.skip("DATABASE_URL not set")
    conn = await asyncpg.connect(dsn)
    try:
        await apply_schema(conn)
        tr = conn.transaction()
        await tr.start()
        yield conn
        await tr.rollback()
    finally:
        await conn.close()


async def _org(conn: asyncpg.Connection) -> str:
    oid = generate_id()
    await conn.execute("INSERT INTO organizations (id) VALUES ($1)", oid)
    await conn.execute(
        "INSERT INTO organization_names (id, organization_id, name, is_canonical)"
        " VALUES ($1, $2, $3, TRUE)",
        generate_id(), oid, f"Org {oid[:8]}",
    )
    return oid


async def _person(conn: asyncpg.Connection) -> str:
    pid = generate_id()
    await conn.execute("INSERT INTO people (id) VALUES ($1)", pid)
    await conn.execute(
        "INSERT INTO person_names (id, person_id, name, is_canonical)"
        " VALUES ($1, $2, $3, TRUE)",
        generate_id(), pid, f"Person {pid[:8]}",
    )
    return pid


async def _insert_role(conn: asyncpg.Connection, org_id: str, title: str) -> str:
    """Insert a role; caller must drop uq_role_org_title first if seeding duplicates."""
    rid = generate_id()
    await conn.execute(
        "INSERT INTO roles (id, organization_id, title) VALUES ($1, $2, $3)",
        rid, org_id, title,
    )
    return rid


async def _insert_assignment(
    conn: asyncpg.Connection,
    person_id: str,
    role_id: str,
    start_date: str | None = None,
) -> str:
    """Insert a role_assignment; caller must drop uq_role_assignment_person_role_start
    first if seeding duplicates."""
    aid = generate_id()
    await conn.execute(
        "INSERT INTO role_assignments (id, person_id, role_id, start_date)"
        " VALUES ($1, $2, $3, $4)",
        aid, person_id, role_id, start_date,
    )
    return aid


async def test_deduplicate_roles_removes_duplicate_roles(db):
    """Duplicate roles (same org+title) must be collapsed to one canonical row."""
    org_id = await _org(db)

    # Drop the unique index so we can seed duplicates
    await db.execute("DROP INDEX IF EXISTS uq_role_org_title")
    role_a = await _insert_role(db, org_id, "CEO")
    role_b = await _insert_role(db, org_id, "CEO")
    role_c = await _insert_role(db, org_id, "CEO")

    await run_deduplication(db, dry_run=False)

    # This org must now have exactly one active CEO role
    ceo_count = await db.fetchval(
        "SELECT count(*) FROM roles"
        " WHERE organization_id = $1 AND lower(title) = 'ceo' AND archived_at IS NULL",
        org_id,
    )
    assert ceo_count == 1

    # Canonical must be the smallest id (ULIDs sort chronologically)
    canonical_id = await db.fetchval(
        "SELECT id FROM roles"
        " WHERE organization_id = $1 AND lower(title) = 'ceo' AND archived_at IS NULL",
        org_id,
    )
    assert canonical_id == min(role_a, role_b, role_c)


async def test_deduplicate_roles_reassigns_role_assignments(db):
    """Assignments pointing to duplicate roles must be re-pointed to canonical."""
    org_id = await _org(db)
    person_a = await _person(db)
    person_b = await _person(db)

    await db.execute("DROP INDEX IF EXISTS uq_role_org_title")
    await db.execute("DROP INDEX IF EXISTS uq_role_assignment_person_role_start")
    role_canonical = await _insert_role(db, org_id, "CFO")
    role_dup = await _insert_role(db, org_id, "CFO")
    # Two people, each assigned to a different duplicate role row
    await _insert_assignment(db, person_a, role_canonical)
    await _insert_assignment(db, person_b, role_dup)

    await run_deduplication(db, dry_run=False)

    # Both assignments must now point to the single canonical role
    roles_in_use = await db.fetch(
        "SELECT DISTINCT role_id FROM role_assignments WHERE archived_at IS NULL"
        " AND person_id = ANY($1::text[])",
        [person_a, person_b],
    )
    assert len(roles_in_use) == 1
    assert roles_in_use[0]["role_id"] == min(role_canonical, role_dup)


async def test_conflicting_assignment_deleted_not_doubled(db):
    """When the same person is assigned to both a canonical and a duplicate role,
    the conflicting duplicate assignment must be deleted (not re-pointed) so the
    person ends up with exactly one assignment to the canonical role."""
    org_id = await _org(db)
    person_id = await _person(db)

    await db.execute("DROP INDEX IF EXISTS uq_role_org_title")
    await db.execute("DROP INDEX IF EXISTS uq_role_assignment_person_role_start")
    role_canonical = await _insert_role(db, org_id, "CSO")
    role_dup = await _insert_role(db, org_id, "CSO")
    # Same person assigned to BOTH duplicate role rows — re-pointing would create a dupe
    await _insert_assignment(db, person_id, role_canonical)
    await _insert_assignment(db, person_id, role_dup)

    await run_deduplication(db, dry_run=False)

    # Person must have exactly one active CSO assignment
    count = await db.fetchval(
        "SELECT count(*) FROM role_assignments ra"
        " JOIN roles r ON r.id = ra.role_id"
        " WHERE ra.person_id = $1"
        "   AND lower(r.title) = 'cso'"
        "   AND ra.archived_at IS NULL"
        "   AND r.archived_at IS NULL",
        person_id,
    )
    assert count == 1

    # result.assignments_removed reflects the entire DB (including production duplicates),
    # so we don't assert on its value here — count == 1 above is the real spec.


async def test_deduplicate_assignments_removes_duplicates(db):
    """Duplicate role_assignments (same person+role+start_date) must be collapsed."""
    org_id = await _org(db)
    person_id = await _person(db)
    role_id = await _insert_role(db, org_id, "CTO")

    await db.execute("DROP INDEX IF EXISTS uq_role_assignment_person_role_start")
    ra_a = await _insert_assignment(db, person_id, role_id)
    ra_b = await _insert_assignment(db, person_id, role_id)
    ra_c = await _insert_assignment(db, person_id, role_id)

    await run_deduplication(db, dry_run=False)

    # This person must now have exactly one active CTO assignment
    count_after = await db.fetchval(
        "SELECT count(*) FROM role_assignments"
        " WHERE person_id = $1 AND role_id = $2 AND archived_at IS NULL",
        person_id, role_id,
    )
    assert count_after == 1

    # Canonical must be the smallest id
    canonical_id = await db.fetchval(
        "SELECT id FROM role_assignments"
        " WHERE person_id = $1 AND role_id = $2 AND archived_at IS NULL",
        person_id, role_id,
    )
    assert canonical_id == min(ra_a, ra_b, ra_c)


async def test_dry_run_makes_no_changes(db):
    """Dry run must report duplicates but make no DB changes."""
    org_id = await _org(db)
    person_id = await _person(db)

    await db.execute("DROP INDEX IF EXISTS uq_role_org_title")
    await db.execute("DROP INDEX IF EXISTS uq_role_assignment_person_role_start")
    role_a = await _insert_role(db, org_id, "COO")
    await _insert_role(db, org_id, "COO")
    await _insert_assignment(db, person_id, role_a)
    await _insert_assignment(db, person_id, role_a)

    result = await run_deduplication(db, dry_run=True)

    # This org's COO count must be unchanged (still 2)
    coo_count = await db.fetchval(
        "SELECT count(*) FROM roles"
        " WHERE organization_id = $1 AND lower(title) = 'coo' AND archived_at IS NULL",
        org_id,
    )
    assert coo_count == 2

    # This person's assignments must be unchanged (still 2)
    ra_count = await db.fetchval(
        "SELECT count(*) FROM role_assignments"
        " WHERE person_id = $1 AND role_id = $2 AND archived_at IS NULL",
        person_id, role_a,
    )
    assert ra_count == 2

    # Result reports that at least our test duplicates would be removed
    assert result.roles_removed >= 1
    assert result.assignments_removed >= 1
    assert result.dry_run is True
