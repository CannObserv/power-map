"""Integration tests for link_types and links schema."""

import asyncpg
import pytest
import pytest_asyncio

from src.core.db import apply_schema, generate_id

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


async def test_link_types_table_exists(db):
    exists = await db.fetchval(
        "SELECT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'link_types')"
    )
    assert exists, "link_types table must exist"


async def test_link_types_social_flags_correct(db):
    """Twitter/Bluesky/LinkedIn must be social=TRUE; website/profile must be FALSE."""
    social = await db.fetchval("SELECT is_social FROM link_types WHERE slug = 'twitter'")
    assert social is True, "twitter must be social"
    generic = await db.fetchval("SELECT is_social FROM link_types WHERE slug = 'website'")
    assert generic is False, "website must not be social"


async def test_links_table_exists(db):
    exists = await db.fetchval(
        "SELECT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'links')"
    )
    assert exists, "links table must exist"


async def test_old_tables_absent(db):
    """urls, social_links, url_types, platforms must not exist after migration."""
    for table in ("urls", "social_links", "url_types", "platforms"):
        exists = await db.fetchval(
            "SELECT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = $1)",
            table,
        )
        assert not exists, f"{table} must be dropped after migration"


async def test_apply_schema_idempotent(db):
    """Running apply_schema twice must not raise."""
    await apply_schema(db)  # second run on top of the pool's first apply


async def test_links_unique_index_exists(db):
    """uq_links_entity_url must exist on (entity_type, entity_id, url, link_type_id)."""
    row = await db.fetchrow(
        "SELECT indexdef FROM pg_indexes"
        " WHERE schemaname='public' AND tablename='links'"
        "   AND indexname='uq_links_entity_url'"
    )
    assert row is not None, "uq_links_entity_url index must exist"
    indexdef = row["indexdef"].lower()
    assert "unique" in indexdef
    for col in ("entity_type", "entity_id", "url", "link_type_id"):
        assert col in indexdef, f"index must cover column {col}"


async def test_links_duplicate_insert_blocked_by_unique_constraint(db):
    """A second raw INSERT with the same natural key must conflict.

    Verifies the UNIQUE index is enforced at the DB level.
    """
    oid = generate_id()
    await db.execute("INSERT INTO organizations (id) VALUES ($1)", oid)
    lt_id = await db.fetchval("SELECT id FROM link_types WHERE slug='website'")
    await db.execute(
        "INSERT INTO links (id, entity_type, entity_id, url, link_type_id)"
        " VALUES ($1, 'organization', $2, 'https://dup.example.com', $3)",
        generate_id(),
        oid,
        lt_id,
    )
    with pytest.raises(asyncpg.exceptions.UniqueViolationError):
        async with db.transaction():
            await db.execute(
                "INSERT INTO links (id, entity_type, entity_id, url, link_type_id)"
                " VALUES ($1, 'organization', $2, 'https://dup.example.com', $3)",
                generate_id(),
                oid,
                lt_id,
            )


async def test_links_on_conflict_do_nothing_is_idempotent(db):
    """INSERT ... ON CONFLICT DO NOTHING with the natural key must silently drop dupes.

    This is the property that makes ingestion-pipeline re-runs safe.
    """
    oid = generate_id()
    await db.execute("INSERT INTO organizations (id) VALUES ($1)", oid)
    lt_id = await db.fetchval("SELECT id FROM link_types WHERE slug='website'")
    for _ in range(3):
        await db.execute(
            "INSERT INTO links"
            " (id, entity_type, entity_id, url, link_type_id)"
            " VALUES ($1, 'organization', $2, 'https://idem.example.com', $3)"
            " ON CONFLICT DO NOTHING",
            generate_id(),
            oid,
            lt_id,
        )
    n = await db.fetchval("SELECT count(*) FROM links WHERE entity_id=$1", oid)
    assert n == 1, f"expected exactly 1 link row after 3 inserts, got {n}"
