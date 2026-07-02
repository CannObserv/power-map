"""Integration tests: temporal validity layer on entity_addresses (#181).

Covers the valid_from/valid_until columns, the chk_ea_validity range check,
the NULLS NOT DISTINCT unique constraint, and the touch-parent trigger that
propagates entity_addresses changes to the linked entity's updated_at (and
thus the entity_changes outbox).
"""

from datetime import date

import asyncpg
import pytest
import pytest_asyncio

from src.core.db import generate_id

pytestmark = [
    pytest.mark.integration,
]

OLD_TS = "'2000-01-01'::timestamptz"


@pytest_asyncio.fixture(loop_scope="session")
async def db(db_pool):
    async with db_pool.acquire() as conn:
        tr = conn.transaction()
        await tr.start()
        try:
            yield conn
        finally:
            await tr.rollback()


async def _org(db, *, backdated: bool = False) -> str:
    oid = generate_id()
    if backdated:
        await db.execute(f"INSERT INTO organizations (id, updated_at) VALUES ($1, {OLD_TS})", oid)
    else:
        await db.execute("INSERT INTO organizations (id) VALUES ($1)", oid)
    return oid


async def _person(db, *, backdated: bool = False) -> str:
    pid = generate_id()
    if backdated:
        await db.execute(f"INSERT INTO people (id, updated_at) VALUES ($1, {OLD_TS})", pid)
    else:
        await db.execute("INSERT INTO people (id) VALUES ($1)", pid)
    return pid


async def _address(db) -> str:
    aid = generate_id()
    await db.execute(
        "INSERT INTO addresses (id, address_line_1, city, region, postal_code, country)"
        " VALUES ($1, '123 Main St', 'Olympia', 'WA', '98501', 'US')",
        aid,
    )
    return aid


async def _ea(
    db,
    entity_type: str,
    entity_id: str,
    address_id: str,
    valid_from: date | None = None,
    valid_until: date | None = None,
    address_type: str = "mailing",
) -> str:
    eaid = generate_id()
    await db.execute(
        "INSERT INTO entity_addresses"
        " (id, entity_type, entity_id, address_id, address_type, valid_from, valid_until)"
        " VALUES ($1, $2, $3, $4, $5, $6, $7)",
        eaid,
        entity_type,
        entity_id,
        address_id,
        address_type,
        valid_from,
        valid_until,
    )
    return eaid


# ---------------------------------------------------------------------------
# Columns + range check
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("column", ["valid_from", "valid_until"])
async def test_validity_column_exists_nullable_date(db, column):
    row = await db.fetchrow(
        """
        SELECT data_type, is_nullable
        FROM information_schema.columns
        WHERE table_name = 'entity_addresses' AND column_name = $1
        """,
        column,
    )
    assert row is not None, f"entity_addresses.{column} missing"
    assert row["data_type"] == "date"
    assert row["is_nullable"] == "YES"


async def test_inverted_range_rejected(db):
    oid = await _org(db)
    aid = await _address(db)
    with pytest.raises(asyncpg.CheckViolationError):
        async with db.transaction():
            await _ea(db, "organization", oid, aid, date(2025, 6, 1), date(2025, 1, 1))


@pytest.mark.parametrize(
    "valid_from,valid_until",
    [
        (None, None),
        (date(2025, 1, 1), None),
        (None, date(2025, 6, 1)),
        (date(2025, 1, 1), date(2025, 1, 1)),  # single-day window
        (date(2025, 1, 1), date(2025, 6, 1)),
    ],
)
async def test_open_ended_and_ordered_ranges_accepted(db, valid_from, valid_until):
    oid = await _org(db)
    aid = await _address(db)
    eaid = await _ea(db, "organization", oid, aid, valid_from, valid_until)
    row = await db.fetchrow(
        "SELECT valid_from, valid_until FROM entity_addresses WHERE id = $1", eaid
    )
    assert row["valid_from"] == valid_from
    assert row["valid_until"] == valid_until


# ---------------------------------------------------------------------------
# Unique constraint: NULLS NOT DISTINCT over (entity, address, type, window)
# ---------------------------------------------------------------------------


async def test_duplicate_open_ended_rows_blocked(db):
    """Two open-ended (NULL/NULL) rows for the same link must still collide."""
    oid = await _org(db)
    aid = await _address(db)
    await _ea(db, "organization", oid, aid)
    with pytest.raises(asyncpg.UniqueViolationError):
        async with db.transaction():
            await _ea(db, "organization", oid, aid)


async def test_distinct_windows_same_address_allowed(db):
    """Same entity + address + type across two validity windows is the core use case."""
    oid = await _org(db)
    aid = await _address(db)
    await _ea(db, "organization", oid, aid, date(2020, 1, 1), date(2022, 12, 31))
    await _ea(db, "organization", oid, aid, date(2024, 1, 1), None)
    count = await db.fetchval(
        "SELECT count(*) FROM entity_addresses WHERE entity_id = $1 AND address_id = $2",
        oid,
        aid,
    )
    assert count == 2


async def test_identical_windows_blocked(db):
    oid = await _org(db)
    aid = await _address(db)
    await _ea(db, "organization", oid, aid, date(2024, 1, 1), None)
    with pytest.raises(asyncpg.UniqueViolationError):
        async with db.transaction():
            await _ea(db, "organization", oid, aid, date(2024, 1, 1), None)


# ---------------------------------------------------------------------------
# Touch-parent trigger → updated_at → entity_changes outbox
# ---------------------------------------------------------------------------


async def test_insert_touches_parent_org_updated_at(db):
    oid = await _org(db, backdated=True)
    aid = await _address(db)
    await _ea(db, "organization", oid, aid)
    new = await db.fetchval("SELECT updated_at FROM organizations WHERE id = $1", oid)
    assert new.year > 2000


async def test_validity_update_touches_parent_person_updated_at(db):
    pid = await _person(db)
    aid = await _address(db)
    eaid = await _ea(db, "person", pid, aid)
    # set_updated_at is BEFORE UPDATE — force the sentinel past it.
    await db.execute(
        f"ALTER TABLE people DISABLE TRIGGER USER;"
        f" UPDATE people SET updated_at = {OLD_TS} WHERE id = '{pid}';"
        f" ALTER TABLE people ENABLE TRIGGER USER;"
    )
    await db.execute(
        "UPDATE entity_addresses SET valid_until = $1 WHERE id = $2", date(2025, 6, 30), eaid
    )
    new = await db.fetchval("SELECT updated_at FROM people WHERE id = $1", pid)
    assert new.year > 2000


async def test_delete_touches_parent_org_updated_at(db):
    oid = await _org(db)
    aid = await _address(db)
    eaid = await _ea(db, "organization", oid, aid)
    await db.execute(
        f"ALTER TABLE organizations DISABLE TRIGGER USER;"
        f" UPDATE organizations SET updated_at = {OLD_TS} WHERE id = '{oid}';"
        f" ALTER TABLE organizations ENABLE TRIGGER USER;"
    )
    await db.execute("DELETE FROM entity_addresses WHERE id = $1", eaid)
    new = await db.fetchval("SELECT updated_at FROM organizations WHERE id = $1", oid)
    assert new.year > 2000


async def test_trigger_registered_for_all_events(db):
    """AFTER INSERT OR UPDATE OR DELETE on entity_addresses (tgtype bits 4|16|8)."""
    row = await db.fetchrow(
        """
        SELECT tgtype
        FROM pg_trigger t
        JOIN pg_class c ON c.oid = t.tgrelid
        WHERE c.relname = 'entity_addresses'
          AND t.tgname = 'trg_touch_entity_on_address_change'
          AND t.tgenabled != 'D'
        """
    )
    assert row is not None, "trg_touch_entity_on_address_change not registered"
    for bit, event in ((4, "INSERT"), (8, "DELETE"), (16, "UPDATE")):
        assert row["tgtype"] & bit, f"trigger not registered for {event}"


async def test_address_insert_emits_entity_change_row(db):
    """Touch-parent UPDATE must cascade into the entity_changes outbox."""
    oid = await _org(db)
    aid = await _address(db)
    before = await db.fetchval(
        "SELECT count(*) FROM entity_changes"
        " WHERE entity_type = 'organization' AND entity_id = $1 AND change_kind = 'updated'",
        oid,
    )
    await _ea(db, "organization", oid, aid)
    after = await db.fetchval(
        "SELECT count(*) FROM entity_changes"
        " WHERE entity_type = 'organization' AND entity_id = $1 AND change_kind = 'updated'",
        oid,
    )
    assert after > before
