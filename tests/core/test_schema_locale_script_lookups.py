"""Phase 2-prep: bcp47_locales + iso15924_scripts lookup tables."""

import asyncpg
import pytest
import pytest_asyncio

from src.core.db import generate_id

pytestmark = [
    pytest.mark.integration,
]


@pytest_asyncio.fixture(loop_scope="session")
async def db(db_pool):
    async with db_pool.acquire() as conn:
        tr = conn.transaction()
        await tr.start()
        try:
            yield conn
        finally:
            await tr.rollback()


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
    # ISO 15924 numeric codes are in the range 100..999; use 30000 to
    # guarantee no collision with seeded registry data.
    await db.execute(
        "INSERT INTO iso15924_scripts (code, numeric_code, name) VALUES ('Aaaa', 30000, 'Test')"
    )
    with pytest.raises(asyncpg.UniqueViolationError):
        await db.execute(
            "INSERT INTO iso15924_scripts (code, numeric_code, name) "
            "VALUES ('Bbbb', 30000, 'Other')"
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
        table,
        column,
    )
    assert row is not None, f"missing pg_trgm GIN index on {table}({column})"


# --- FK enforcement: person_names.locale / .script ---


async def _person(conn) -> str:
    pid = generate_id()
    await conn.execute("INSERT INTO people (id) VALUES ($1)", pid)
    return pid


async def test_person_names_locale_fk_rejects_unregistered(db):
    pid = await _person(db)
    # Seed at least one row so the FK actually has somewhere to point.
    await db.execute(
        "INSERT INTO bcp47_locales (code, language, display_name) "
        "VALUES ('en-US', 'en', 'English (United States)') ON CONFLICT DO NOTHING"
    )
    with pytest.raises(asyncpg.ForeignKeyViolationError):
        await db.execute(
            "INSERT INTO person_names (id, person_id, name, locale) VALUES ($1, $2, $3, 'xx-XX')",
            generate_id(),
            pid,
            "Test",
        )


async def test_person_names_locale_fk_accepts_registered(db):
    pid = await _person(db)
    await db.execute(
        "INSERT INTO bcp47_locales (code, language, display_name) "
        "VALUES ('en-US', 'en', 'English (United States)') ON CONFLICT DO NOTHING"
    )
    await db.execute(
        "INSERT INTO person_names (id, person_id, name, locale) VALUES ($1, $2, $3, 'en-US')",
        generate_id(),
        pid,
        "Test",
    )


async def test_person_names_script_fk_rejects_unregistered(db):
    pid = await _person(db)
    await db.execute(
        "INSERT INTO iso15924_scripts (code, numeric_code, name) "
        "VALUES ('Latn', 215, 'Latin') ON CONFLICT DO NOTHING"
    )
    with pytest.raises(asyncpg.ForeignKeyViolationError):
        await db.execute(
            "INSERT INTO person_names (id, person_id, name, script) VALUES ($1, $2, $3, 'Xxxx')",
            generate_id(),
            pid,
            "Test",
        )


async def test_person_names_script_fk_accepts_registered(db):
    pid = await _person(db)
    await db.execute(
        "INSERT INTO iso15924_scripts (code, numeric_code, name) "
        "VALUES ('Latn', 215, 'Latin') ON CONFLICT DO NOTHING"
    )
    await db.execute(
        "INSERT INTO person_names (id, person_id, name, script) VALUES ($1, $2, $3, 'Latn')",
        generate_id(),
        pid,
        "Test",
    )


async def test_person_names_locale_null_still_allowed(db):
    """FK must permit NULL — not all names need a locale tag."""
    pid = await _person(db)
    await db.execute(
        "INSERT INTO person_names (id, person_id, name) VALUES ($1, $2, $3)",
        generate_id(),
        pid,
        "Test",
    )
