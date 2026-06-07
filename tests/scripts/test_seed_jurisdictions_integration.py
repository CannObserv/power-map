"""Integration tests for seed_jurisdictions upsert helpers.

Requires TEST_DATABASE_URL and a schema-applied DB.

Run via:
    uv run pytest tests/scripts/test_seed_jurisdictions_integration.py
"""

import pytest
import pytest_asyncio

from scripts.seed_jurisdictions import (
    upsert_jurisdiction_relationships,
    upsert_jurisdictions,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
]

_JUR_ROWS = [
    {"slug": "test-seed-country", "name": "Test Country", "type": "country"},
    {"slug": "test-seed-state", "name": "Test State", "type": "state"},
]

_REL_ROWS = [
    {
        "subject_slug": "test-seed-state",
        "object_slug": "test-seed-country",
        "relationship_type": "is_fully_contained_by",
    }
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


# ---------------------------------------------------------------------------
# upsert_jurisdictions
# ---------------------------------------------------------------------------


async def test_upsert_jurisdictions_inserts_rows(db):
    n = await upsert_jurisdictions(db, iter(_JUR_ROWS))
    assert n == 2
    slugs = await db.fetch(
        "SELECT slug FROM jurisdictions WHERE slug LIKE 'test-seed-%' ORDER BY slug"
    )
    assert [r["slug"] for r in slugs] == ["test-seed-country", "test-seed-state"]


async def test_upsert_jurisdictions_is_idempotent(db):
    await upsert_jurisdictions(db, iter(_JUR_ROWS))
    await upsert_jurisdictions(db, iter(_JUR_ROWS))
    n = await db.fetchval("SELECT COUNT(*) FROM jurisdictions WHERE slug LIKE 'test-seed-%'")
    assert n == 2


async def test_upsert_jurisdictions_updates_name(db):
    await upsert_jurisdictions(db, iter(_JUR_ROWS))
    revised = [{**_JUR_ROWS[0], "name": "Revised Country"}]
    await upsert_jurisdictions(db, iter(revised))
    name = await db.fetchval("SELECT name FROM jurisdictions WHERE slug = 'test-seed-country'")
    assert name == "Revised Country"


async def test_upsert_jurisdictions_creates_jur_slug_identifier(db):
    await upsert_jurisdictions(db, iter(_JUR_ROWS[:1]))
    count = await db.fetchval(
        """
        SELECT COUNT(*) FROM identifiers i
        JOIN entity_identifier_types t ON t.id = i.entity_identifier_type_id
        JOIN jurisdictions j ON j.id = i.entity_id
        WHERE j.slug = 'test-seed-country' AND t.slug = 'jur_slug'
        """
    )
    assert count == 1


async def test_upsert_jurisdictions_jur_slug_identifier_idempotent(db):
    await upsert_jurisdictions(db, iter(_JUR_ROWS[:1]))
    await upsert_jurisdictions(db, iter(_JUR_ROWS[:1]))
    count = await db.fetchval(
        """
        SELECT COUNT(*) FROM identifiers i
        JOIN entity_identifier_types t ON t.id = i.entity_identifier_type_id
        JOIN jurisdictions j ON j.id = i.entity_id
        WHERE j.slug = 'test-seed-country' AND t.slug = 'jur_slug'
        """
    )
    assert count == 1


async def test_upsert_jurisdictions_unknown_type_raises(db):
    bad = [{"slug": "test-seed-bad", "name": "Bad", "type": "nonexistent_type"}]
    with pytest.raises(ValueError, match="Unknown jurisdiction type"):
        await upsert_jurisdictions(db, iter(bad))


# ---------------------------------------------------------------------------
# upsert_jurisdiction_relationships
# ---------------------------------------------------------------------------


async def test_upsert_relationships_inserts_rows(db):
    await upsert_jurisdictions(db, iter(_JUR_ROWS))
    n = await upsert_jurisdiction_relationships(db, iter(_REL_ROWS))
    assert n == 1
    count = await db.fetchval(
        """
        SELECT COUNT(*) FROM jurisdiction_relationships jr
        JOIN jurisdictions fs ON fs.id = jr.from_id AND fs.slug = 'test-seed-state'
        JOIN jurisdictions ft ON ft.id = jr.to_id   AND ft.slug = 'test-seed-country'
        """
    )
    assert count == 1


async def test_upsert_relationships_is_idempotent(db):
    await upsert_jurisdictions(db, iter(_JUR_ROWS))
    await upsert_jurisdiction_relationships(db, iter(_REL_ROWS))
    await upsert_jurisdiction_relationships(db, iter(_REL_ROWS))
    count = await db.fetchval(
        """
        SELECT COUNT(*) FROM jurisdiction_relationships jr
        JOIN jurisdictions fs ON fs.id = jr.from_id AND fs.slug = 'test-seed-state'
        JOIN jurisdictions ft ON ft.id = jr.to_id   AND ft.slug = 'test-seed-country'
        """
    )
    assert count == 1


async def test_upsert_relationships_unknown_rel_type_raises(db):
    await upsert_jurisdictions(db, iter(_JUR_ROWS))
    bad = [
        {
            "subject_slug": "test-seed-state",
            "object_slug": "test-seed-country",
            "relationship_type": "nonexistent_rel",
        }
    ]
    with pytest.raises(ValueError, match="Unknown relationship type"):
        await upsert_jurisdiction_relationships(db, iter(bad))


async def test_upsert_relationships_unknown_subject_slug_raises(db):
    await upsert_jurisdictions(db, iter(_JUR_ROWS))
    bad = [
        {
            "subject_slug": "no-such-slug",
            "object_slug": "test-seed-country",
            "relationship_type": "is_fully_contained_by",
        }
    ]
    with pytest.raises(ValueError, match="Unknown jurisdiction slug"):
        await upsert_jurisdiction_relationships(db, iter(bad))
