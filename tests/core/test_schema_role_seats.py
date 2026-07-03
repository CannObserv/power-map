"""Integration tests: seat-Role schema (#261).

role_types classifier, roles seat columns (role_type_id, jurisdiction_id,
qualifier), the districted-role CHECK, and the split unique indexes.
"""

import asyncpg
import pytest
import pytest_asyncio

from src.core.db import generate_id

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


async def _make_org(db) -> str:
    oid = generate_id()
    await db.execute("INSERT INTO organizations (id) VALUES ($1)", oid)
    return oid


async def _make_jurisdiction(db) -> str:
    jid = generate_id()
    type_id = await db.fetchval(
        "SELECT id FROM jurisdiction_types WHERE slug='legislative_district'"
    )
    await db.execute(
        "INSERT INTO jurisdictions (id, slug, name, type_id) VALUES ($1,$2,$3,$4)",
        jid,
        f"ld-{jid[-8:].lower()}",
        "Test LD",
        type_id,
    )
    return jid


async def _role_type_id(db, slug: str) -> str:
    return await db.fetchval("SELECT id FROM role_types WHERE slug=$1", slug)


# --- role_types classifier ---


async def test_role_types_seeded(db):
    slugs = {r["slug"] for r in await db.fetch("SELECT slug FROM role_types")}
    assert {"state_representative", "state_senator"} <= slugs


# --- new seat columns exist ---


@pytest.mark.parametrize("col", ["role_type_id", "jurisdiction_id", "qualifier"])
async def test_roles_has_seat_columns(db, col):
    exists = await db.fetchval(
        "SELECT 1 FROM information_schema.columns WHERE table_name='roles' AND column_name=$1",
        col,
    )
    assert exists, f"roles.{col} missing"


# --- CHECK: a districted role must name an office ---


async def test_districted_role_requires_role_type(db):
    org = await _make_org(db)
    jur = await _make_jurisdiction(db)
    with pytest.raises(asyncpg.CheckViolationError):
        await db.execute(
            "INSERT INTO roles (id, organization_id, title, jurisdiction_id) VALUES ($1,$2,$3,$4)",
            generate_id(),
            org,
            "State Representative",
            jur,
        )


async def test_districted_role_with_role_type_ok(db):
    org = await _make_org(db)
    jur = await _make_jurisdiction(db)
    rt = await _role_type_id(db, "state_representative")
    await db.execute(
        "INSERT INTO roles "
        "(id, organization_id, title, role_type_id, jurisdiction_id, qualifier) "
        "VALUES ($1,$2,$3,$4,$5,$6)",
        generate_id(),
        org,
        "State Representative",
        rt,
        jur,
        "Position 1",
    )


# --- split unique indexes ---


async def test_split_unique_indexes_exist(db):
    rows = {
        r["indexname"]: r["indexdef"].lower()
        for r in await db.fetch(
            "SELECT indexname, indexdef FROM pg_indexes WHERE tablename='roles'"
        )
    }
    assert "uq_role_seat" in rows
    assert "jurisdiction_id is not null" in rows["uq_role_seat"]
    assert "uq_role_org_title" in rows
    assert "jurisdiction_id is null" in rows["uq_role_org_title"]


async def test_two_seats_same_title_distinct_qualifier_ok(db):
    org = await _make_org(db)
    jur = await _make_jurisdiction(db)
    rt = await _role_type_id(db, "state_representative")
    for q in ("Position 1", "Position 2"):
        await db.execute(
            "INSERT INTO roles "
            "(id, organization_id, title, role_type_id, jurisdiction_id, qualifier) "
            "VALUES ($1,$2,$3,$4,$5,$6)",
            generate_id(),
            org,
            "State Representative",
            rt,
            jur,
            q,
        )
    n = await db.fetchval(
        "SELECT count(*) FROM roles WHERE organization_id=$1 AND jurisdiction_id=$2",
        org,
        jur,
    )
    assert n == 2


async def test_duplicate_seat_rejected(db):
    org = await _make_org(db)
    jur = await _make_jurisdiction(db)
    rt = await _role_type_id(db, "state_representative")
    args = (
        "State Representative",
        rt,
        jur,
        "Position 1",
    )
    await db.execute(
        "INSERT INTO roles "
        "(id, organization_id, title, role_type_id, jurisdiction_id, qualifier) "
        "VALUES ($1,$2,$3,$4,$5,$6)",
        generate_id(),
        org,
        *args,
    )
    with pytest.raises(asyncpg.UniqueViolationError):
        await db.execute(
            "INSERT INTO roles "
            "(id, organization_id, title, role_type_id, jurisdiction_id, qualifier) "
            "VALUES ($1,$2,$3,$4,$5,$6)",
            generate_id(),
            org,
            *args,
        )


async def test_senate_seat_null_qualifier_is_unique_per_district(db):
    """qualifier NULL: NULLS NOT DISTINCT means one senate seat per district."""
    org = await _make_org(db)
    jur = await _make_jurisdiction(db)
    rt = await _role_type_id(db, "state_senator")
    await db.execute(
        "INSERT INTO roles "
        "(id, organization_id, title, role_type_id, jurisdiction_id) "
        "VALUES ($1,$2,$3,$4,$5)",
        generate_id(),
        org,
        "State Senator",
        rt,
        jur,
    )
    with pytest.raises(asyncpg.UniqueViolationError):
        await db.execute(
            "INSERT INTO roles "
            "(id, organization_id, title, role_type_id, jurisdiction_id) "
            "VALUES ($1,$2,$3,$4,$5)",
            generate_id(),
            org,
            "State Senator",
            rt,
            jur,
        )


async def test_non_districted_duplicate_title_still_rejected(db):
    """Title-based identity (jurisdiction NULL) unchanged, case-insensitive."""
    org = await _make_org(db)
    await db.execute(
        "INSERT INTO roles (id, organization_id, title) VALUES ($1,$2,$3)",
        generate_id(),
        org,
        "Speaker",
    )
    with pytest.raises(asyncpg.UniqueViolationError):
        await db.execute(
            "INSERT INTO roles (id, organization_id, title) VALUES ($1,$2,$3)",
            generate_id(),
            org,
            "speaker",
        )


async def test_qualifier_without_jurisdiction_rejected(db):
    """chk_role_qualifier_needs_jurisdiction: a qualifier requires a district."""
    org = await _make_org(db)
    with pytest.raises(asyncpg.CheckViolationError):
        await db.execute(
            "INSERT INTO roles (id, organization_id, title, qualifier) VALUES ($1,$2,$3,$4)",
            generate_id(),
            org,
            "Speaker",
            "Position 1",
        )
