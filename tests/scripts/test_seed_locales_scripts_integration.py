"""Integration tests for the locale/script upsert helpers.

Exercises the actual SQL against a real DB. Skipped when the `seed`
dep group or `TEST_DATABASE_URL` is missing.

Run via `uv run --group seed pytest tests/scripts/test_seed_locales_scripts_integration.py`.
"""

import os

import asyncpg
import pytest

from src.core.db import apply_schema

pytest.importorskip("langcodes")
pytest.importorskip("pycountry")

from scripts.seed_locales_scripts import (  # noqa: E402
    upsert_locales,
    upsert_scripts,
)

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
        try:
            yield conn
        finally:
            await tr.rollback()
    finally:
        await conn.close()


# Use codes outside the ISO 15924 range (100..999) so they can't collide with
# real seeded data. The four-letter form follows registry shape.
_LOC_ROWS = [
    {
        "code": "test-AA",
        "language": "test",
        "script": None,
        "region": "AA",
        "display_name": "Test (Alpha)",
    },
    {
        "code": "test-BB",
        "language": "test",
        "script": None,
        "region": "BB",
        "display_name": "Test (Bravo)",
    },
]

_SCR_ROWS = [
    {"code": "Tst1", "numeric_code": 30001, "name": "Test One"},
    {"code": "Tst2", "numeric_code": 30002, "name": "Test Two"},
]


async def test_upsert_locales_inserts_new_rows(db):
    n = await upsert_locales(db, iter(_LOC_ROWS))
    assert n == 2
    rows = await db.fetch(
        "SELECT code, display_name FROM bcp47_locales WHERE code LIKE 'test-%' ORDER BY code"
    )
    assert [(r["code"], r["display_name"]) for r in rows] == [
        ("test-AA", "Test (Alpha)"),
        ("test-BB", "Test (Bravo)"),
    ]


async def test_upsert_locales_is_idempotent(db):
    await upsert_locales(db, iter(_LOC_ROWS))
    await upsert_locales(db, iter(_LOC_ROWS))
    n = await db.fetchval(
        "SELECT COUNT(*) FROM bcp47_locales WHERE code LIKE 'test-%'"
    )
    assert n == 2


async def test_upsert_locales_updates_existing_rows(db):
    await upsert_locales(db, iter(_LOC_ROWS))
    revised = [{**_LOC_ROWS[0], "display_name": "Test (Alpha, revised)"}]
    await upsert_locales(db, iter(revised))
    name = await db.fetchval(
        "SELECT display_name FROM bcp47_locales WHERE code = 'test-AA'"
    )
    assert name == "Test (Alpha, revised)"


async def test_upsert_locales_empty_iterator(db):
    n = await upsert_locales(db, iter([]))
    assert n == 0


async def test_upsert_scripts_inserts_new_rows(db):
    n = await upsert_scripts(db, iter(_SCR_ROWS))
    assert n == 2
    rows = await db.fetch(
        "SELECT code, numeric_code, name FROM iso15924_scripts "
        "WHERE numeric_code IN (30001, 30002) ORDER BY numeric_code"
    )
    assert [(r["code"], r["numeric_code"], r["name"]) for r in rows] == [
        ("Tst1", 30001, "Test One"),
        ("Tst2", 30002, "Test Two"),
    ]


async def test_upsert_scripts_is_idempotent(db):
    await upsert_scripts(db, iter(_SCR_ROWS))
    await upsert_scripts(db, iter(_SCR_ROWS))
    n = await db.fetchval(
        "SELECT COUNT(*) FROM iso15924_scripts WHERE numeric_code IN (30001, 30002)"
    )
    assert n == 2


async def test_upsert_scripts_updates_existing_rows(db):
    await upsert_scripts(db, iter(_SCR_ROWS))
    revised = [{**_SCR_ROWS[0], "name": "Test One (revised)"}]
    await upsert_scripts(db, iter(revised))
    name = await db.fetchval(
        "SELECT name FROM iso15924_scripts WHERE code = 'Tst1'"
    )
    assert name == "Test One (revised)"


async def test_upsert_scripts_empty_iterator(db):
    n = await upsert_scripts(db, iter([]))
    assert n == 0
