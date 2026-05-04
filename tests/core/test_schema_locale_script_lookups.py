"""Phase 2-prep: bcp47_locales + iso15924_scripts lookup tables."""

import os

import asyncpg
import pytest

from src.core.db import apply_schema

pytestmark = pytest.mark.integration


@pytest.fixture
async def db():
    dsn = os.environ.get("TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("TEST_DATABASE_URL not set")
    conn = await asyncpg.connect(dsn)
    try:
        await apply_schema(conn)
        tr = conn.transaction()
        await tr.start()
        yield conn
        await tr.rollback()
    finally:
        await conn.close()


# --- Table existence + column shape ---

async def test_bcp47_locales_table_exists(db):
    row = await db.fetchrow(
        "SELECT 1 FROM information_schema.tables WHERE table_name='bcp47_locales'"
    )
    assert row is not None


@pytest.mark.parametrize(
    "column,data_type",
    [
        ("code", "text"),
        ("language", "text"),
        ("script", "text"),
        ("region", "text"),
        ("display_name", "text"),
    ],
)
async def test_bcp47_locales_has_column(db, column, data_type):
    row = await db.fetchrow(
        "SELECT data_type FROM information_schema.columns "
        "WHERE table_name='bcp47_locales' AND column_name=$1",
        column,
    )
    assert row is not None, f"bcp47_locales.{column} missing"
    assert row["data_type"] == data_type


async def test_iso15924_scripts_table_exists(db):
    row = await db.fetchrow(
        "SELECT 1 FROM information_schema.tables WHERE table_name='iso15924_scripts'"
    )
    assert row is not None


@pytest.mark.parametrize(
    "column,data_type",
    [
        ("code", "text"),
        ("numeric_code", "smallint"),
        ("name", "text"),
    ],
)
async def test_iso15924_scripts_has_column(db, column, data_type):
    row = await db.fetchrow(
        "SELECT data_type FROM information_schema.columns "
        "WHERE table_name='iso15924_scripts' AND column_name=$1",
        column,
    )
    assert row is not None, f"iso15924_scripts.{column} missing"
    assert row["data_type"] == data_type


async def test_iso15924_scripts_numeric_code_unique(db):
    await db.execute(
        "INSERT INTO iso15924_scripts (code, numeric_code, name) "
        "VALUES ('Aaaa', 999, 'Test')"
    )
    with pytest.raises(asyncpg.UniqueViolationError):
        await db.execute(
            "INSERT INTO iso15924_scripts (code, numeric_code, name) "
            "VALUES ('Bbbb', 999, 'Other')"
        )


# --- pg_trgm GIN indexes for typeahead substring search ---

@pytest.mark.parametrize(
    "table,column",
    [
        ("bcp47_locales", "code"),
        ("bcp47_locales", "display_name"),
        ("iso15924_scripts", "code"),
        ("iso15924_scripts", "name"),
    ],
)
async def test_trgm_gin_index_exists(db, table, column):
    """pg_trgm GIN index must exist on every column the typeahead searches."""
    row = await db.fetchrow(
        """
        SELECT indexdef
        FROM pg_indexes
        WHERE tablename = $1
          AND indexdef ILIKE '%using gin%'
          AND indexdef ILIKE '%gin_trgm_ops%'
          AND indexdef ILIKE '%' || $2 || '%'
        """,
        table, column,
    )
    assert row is not None, f"missing pg_trgm GIN index on {table}({column})"
