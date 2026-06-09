"""Integration tests for the dedup_links cleanup script.

Issue #142: ``links`` table has no UNIQUE constraint on
``(entity_type, entity_id, url, link_type_id)``. Pre-existing prod rows may
violate that natural key. This script consolidates each duplicate group down
to the oldest row before the UNIQUE INDEX is created.
"""

import asyncpg
import pytest
import pytest_asyncio

from scripts.dedup_links import run_consolidation
from src.core.db import generate_id

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


async def _org(conn: asyncpg.Connection) -> str:
    oid = generate_id()
    await conn.execute("INSERT INTO organizations (id) VALUES ($1)", oid)
    return oid


async def _website_lt(conn: asyncpg.Connection) -> str:
    return await conn.fetchval("SELECT id FROM link_types WHERE slug='website'")


async def _insert_link(
    conn: asyncpg.Connection,
    *,
    entity_type: str,
    entity_id: str,
    url: str,
    link_type_id: str,
    link_id: str | None = None,
) -> str:
    """Insert a link; caller must drop uq_links_entity_url first if seeding duplicates."""
    lid = link_id or generate_id()
    await conn.execute(
        "INSERT INTO links (id, entity_type, entity_id, url, link_type_id)"
        " VALUES ($1, $2, $3, $4, $5)",
        lid,
        entity_type,
        entity_id,
        url,
        link_type_id,
    )
    return lid


async def _drop_unique_index(conn: asyncpg.Connection) -> None:
    """Drop uq_links_entity_url so the test can seed duplicate rows.

    Rolled back with the rest of the test transaction; harmless to other tests.
    """
    await conn.execute("DROP INDEX IF EXISTS uq_links_entity_url")


async def test_dedup_links_no_dupes_is_noop(db):
    oid = await _org(db)
    lt_id = await _website_lt(db)
    lid = await _insert_link(
        db,
        entity_type="organization",
        entity_id=oid,
        url="https://example.com",
        link_type_id=lt_id,
    )
    result = await run_consolidation(db, dry_run=False)
    assert result.rows_removed == 0
    survivors = await db.fetch("SELECT id FROM links WHERE entity_id=$1", oid)
    assert [r["id"] for r in survivors] == [lid]


async def test_dedup_links_keeps_oldest_drops_rest(db):
    await _drop_unique_index(db)
    oid = await _org(db)
    lt_id = await _website_lt(db)
    # Three rows with the same natural key; ULIDs sort lexicographically by time.
    lid_oldest = await _insert_link(
        db,
        entity_type="organization",
        entity_id=oid,
        url="https://dup.example.com",
        link_type_id=lt_id,
    )
    await _insert_link(
        db,
        entity_type="organization",
        entity_id=oid,
        url="https://dup.example.com",
        link_type_id=lt_id,
    )
    await _insert_link(
        db,
        entity_type="organization",
        entity_id=oid,
        url="https://dup.example.com",
        link_type_id=lt_id,
    )
    result = await run_consolidation(db, dry_run=False)
    assert result.rows_removed == 2
    survivors = await db.fetch("SELECT id FROM links WHERE entity_id=$1", oid)
    assert [r["id"] for r in survivors] == [lid_oldest]


async def test_dedup_links_dry_run_makes_no_changes(db):
    await _drop_unique_index(db)
    oid = await _org(db)
    lt_id = await _website_lt(db)
    for _ in range(3):
        await _insert_link(
            db,
            entity_type="organization",
            entity_id=oid,
            url="https://dup.example.com",
            link_type_id=lt_id,
        )
    result = await run_consolidation(db, dry_run=True)
    assert result.rows_removed == 2
    assert result.dry_run is True
    n = await db.fetchval("SELECT count(*) FROM links WHERE entity_id=$1", oid)
    assert n == 3, "dry run must not delete any rows"


async def test_dedup_links_keeps_active_over_older_inactive(db):
    """Active rows win ties even when the inactive sibling is older.

    F5: a duplicate group containing one ``is_active=TRUE`` and one
    ``is_active=FALSE`` row must keep the active row regardless of insertion
    order, so a one-time cleanup doesn't silently retire a still-in-use URL.
    """
    await _drop_unique_index(db)
    oid = await _org(db)
    lt_id = await _website_lt(db)
    # Older row is inactive; newer row is active. Without is_active in the
    # ORDER BY, the older row would win and the active link would be dropped.
    older_inactive = generate_id()
    await db.execute(
        "INSERT INTO links (id, entity_type, entity_id, url, link_type_id, is_active)"
        " VALUES ($1, 'organization', $2, 'https://t.example.com', $3, FALSE)",
        older_inactive,
        oid,
        lt_id,
    )
    newer_active = generate_id()
    await db.execute(
        "INSERT INTO links (id, entity_type, entity_id, url, link_type_id, is_active)"
        " VALUES ($1, 'organization', $2, 'https://t.example.com', $3, TRUE)",
        newer_active,
        oid,
        lt_id,
    )
    result = await run_consolidation(db, dry_run=False)
    assert result.rows_removed == 1
    survivors = await db.fetch("SELECT id, is_active FROM links WHERE entity_id=$1", oid)
    assert len(survivors) == 1
    assert survivors[0]["id"] == newer_active
    assert survivors[0]["is_active"] is True


async def test_dedup_links_isolates_per_natural_key(db):
    """Distinct (entity, url, type) tuples are independent groups."""
    await _drop_unique_index(db)
    oid_a = await _org(db)
    oid_b = await _org(db)
    lt_id = await _website_lt(db)
    # Two duplicates on org A.
    await _insert_link(
        db,
        entity_type="organization",
        entity_id=oid_a,
        url="https://a.example.com",
        link_type_id=lt_id,
    )
    await _insert_link(
        db,
        entity_type="organization",
        entity_id=oid_a,
        url="https://a.example.com",
        link_type_id=lt_id,
    )
    # One link on org B with same URL — different entity_id, not a dup.
    await _insert_link(
        db,
        entity_type="organization",
        entity_id=oid_b,
        url="https://a.example.com",
        link_type_id=lt_id,
    )
    result = await run_consolidation(db, dry_run=False)
    assert result.rows_removed == 1
    n_a = await db.fetchval("SELECT count(*) FROM links WHERE entity_id=$1", oid_a)
    n_b = await db.fetchval("SELECT count(*) FROM links WHERE entity_id=$1", oid_b)
    assert n_a == 1
    assert n_b == 1
