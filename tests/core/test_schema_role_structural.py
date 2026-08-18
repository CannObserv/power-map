"""Integration tests: role structural-fields schema (#261).

role_types classifier, roles structural columns (role_type_id, jurisdiction_id,
qualifier), the jurisdiction-needs-role-type CHECK, and the split unique indexes.
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


async def test_at_large_role_type_seeded_positionless(db):
    """The at-large seat type exists and does NOT require a qualifier (#302).

    Seeded ahead of the data because `role_types` has no remote write path —
    resolve_role rejects an unknown slug, so an unseeded type would bounce every
    pre-1965 observation as `role_type_not_found`.
    """
    row = await db.fetchrow(
        "SELECT expects_jurisdiction, requires_qualifier FROM role_types"
        " WHERE slug='state_representative_at_large'"
    )
    assert row is not None, "state_representative_at_large not seeded"
    assert row["expects_jurisdiction"] is True
    assert row["requires_qualifier"] is False


# --- new structural columns exist ---


@pytest.mark.parametrize("col", ["role_type_id", "jurisdiction_id", "qualifier"])
async def test_roles_has_structural_columns(db, col):
    exists = await db.fetchval(
        "SELECT 1 FROM information_schema.columns WHERE table_name='roles' AND column_name=$1",
        col,
    )
    assert exists, f"roles.{col} missing"


# --- CHECK: a role with a jurisdiction must name an office ---


async def test_role_with_jurisdiction_requires_role_type(db):
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


async def test_role_with_jurisdiction_and_role_type_ok(db):
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


# --- TRIGGER: a requires_qualifier office needs a qualifier for a districted seat (#273) ---


async def test_positionless_districted_seat_rejected_by_trigger(db):
    """DB backstop for resolve_role: a direct INSERT of a requires_qualifier office
    with a jurisdiction but NULL qualifier is rejected at the data layer (#273)."""
    org = await _make_org(db)
    jur = await _make_jurisdiction(db)
    rt = await _role_type_id(db, "state_representative")
    with pytest.raises(asyncpg.CheckViolationError):
        await db.execute(
            "INSERT INTO roles (id, organization_id, title, role_type_id, jurisdiction_id) "
            "VALUES ($1,$2,$3,$4,$5)",
            generate_id(),
            org,
            "State Representative",
            rt,
            jur,
        )


async def test_senate_districted_seat_null_qualifier_allowed(db):
    """state_senator (requires_qualifier=False) may keep a NULL qualifier."""
    org = await _make_org(db)
    jur = await _make_jurisdiction(db)
    rt = await _role_type_id(db, "state_senator")
    await db.execute(
        "INSERT INTO roles (id, organization_id, title, role_type_id, jurisdiction_id) "
        "VALUES ($1,$2,$3,$4,$5)",
        generate_id(),
        org,
        "State Senator",
        rt,
        jur,
    )


async def test_clearing_qualifier_on_districted_seat_rejected_by_trigger(db):
    """The trigger also fires on UPDATE (not just INSERT): clearing the qualifier of
    a positioned seat is rejected (#273)."""
    org = await _make_org(db)
    jur = await _make_jurisdiction(db)
    rt = await _role_type_id(db, "state_representative")
    rid = generate_id()
    await db.execute(
        "INSERT INTO roles "
        "(id, organization_id, title, role_type_id, jurisdiction_id, qualifier) "
        "VALUES ($1,$2,$3,$4,$5,$6)",
        rid,
        org,
        "State Representative",
        rt,
        jur,
        "Position 1",
    )
    with pytest.raises(asyncpg.CheckViolationError):
        await db.execute("UPDATE roles SET qualifier = NULL WHERE id=$1", rid)


# --- split unique indexes ---


async def test_split_unique_indexes_exist(db):
    rows = {
        r["indexname"]: r["indexdef"].lower()
        for r in await db.fetch(
            "SELECT indexname, indexdef FROM pg_indexes WHERE tablename='roles'"
        )
    }
    assert "uq_role_structural" in rows
    assert "jurisdiction_id is not null" in rows["uq_role_structural"]
    assert "uq_role_org_title" in rows
    assert "jurisdiction_id is null" in rows["uq_role_org_title"]


async def test_two_structural_roles_same_title_distinct_qualifier_ok(db):
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


async def test_at_large_seat_coexists_with_positioned_seats_same_district(db):
    """At-large and positioned seats share one district row without colliding (#302).

    usa-wa reuses the *current* `usa-wa-ld-N` jurisdiction rows for pre-1965
    tenures, so all three roles hang off the same district. They stay distinct
    because `uq_role_structural` keys on role_type as well — the at-large row's
    NULL qualifier only has to be unique within its own type.
    """
    org = await _make_org(db)
    jur = await _make_jurisdiction(db)
    positioned = await _role_type_id(db, "state_representative")
    at_large = await _role_type_id(db, "state_representative_at_large")

    for rt, title, q in (
        (positioned, "State Representative", "Position 1"),
        (positioned, "State Representative", "Position 2"),
        (at_large, "State Representative (At-Large)", None),
    ):
        await db.execute(
            "INSERT INTO roles "
            "(id, organization_id, title, role_type_id, jurisdiction_id, qualifier) "
            "VALUES ($1,$2,$3,$4,$5,$6)",
            generate_id(),
            org,
            title,
            rt,
            jur,
            q,
        )

    n = await db.fetchval(
        "SELECT count(*) FROM roles WHERE organization_id=$1 AND jurisdiction_id=$2",
        org,
        jur,
    )
    assert n == 3


async def test_at_large_seat_null_qualifier_unique_per_district(db):
    """One at-large role per district — multiplicity is assignments, not rows (#302)."""
    org = await _make_org(db)
    jur = await _make_jurisdiction(db)
    rt = await _role_type_id(db, "state_representative_at_large")
    args = ("State Representative (At-Large)", rt, jur, None)
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


async def test_duplicate_structural_role_rejected(db):
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


async def test_senate_role_null_qualifier_is_unique_per_district(db):
    """qualifier NULL: NULLS NOT DISTINCT means one senator role per district."""
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


async def test_role_without_jurisdiction_duplicate_title_still_rejected(db):
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
