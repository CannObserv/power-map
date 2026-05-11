"""Integration tests for link_types and links schema."""

import asyncio
import os

import asyncpg
import pytest

from src.core.db import apply_schema, generate_id

pytestmark = pytest.mark.integration


def _dsn() -> str:
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        pytest.skip("DATABASE_URL not set")
    return dsn


async def _conn() -> asyncpg.Connection:
    conn = await asyncpg.connect(_dsn())
    await apply_schema(conn)
    return conn


def test_link_types_table_exists():
    async def run():
        conn = await _conn()
        try:
            exists = await conn.fetchval(
                "SELECT EXISTS (SELECT 1 FROM information_schema.tables"
                " WHERE table_name = 'link_types')"
            )
            assert exists, "link_types table must exist"
        finally:
            await conn.close()
    asyncio.run(run())


def test_link_types_social_flags_correct():
    """Twitter/Bluesky/LinkedIn must be social=TRUE; website/profile must be FALSE."""
    async def run():
        conn = await _conn()
        try:
            social = await conn.fetchval(
                "SELECT is_social FROM link_types WHERE slug = 'twitter'"
            )
            assert social is True, "twitter must be social"
            generic = await conn.fetchval(
                "SELECT is_social FROM link_types WHERE slug = 'website'"
            )
            assert generic is False, "website must not be social"
        finally:
            await conn.close()
    asyncio.run(run())


def test_links_table_exists():
    async def run():
        conn = await _conn()
        try:
            exists = await conn.fetchval(
                "SELECT EXISTS (SELECT 1 FROM information_schema.tables"
                " WHERE table_name = 'links')"
            )
            assert exists, "links table must exist"
        finally:
            await conn.close()
    asyncio.run(run())


def test_old_tables_absent():
    """urls, social_links, url_types, platforms must not exist after migration."""
    async def run():
        conn = await _conn()
        try:
            for table in ("urls", "social_links", "url_types", "platforms"):
                exists = await conn.fetchval(
                    "SELECT EXISTS (SELECT 1 FROM information_schema.tables"
                    " WHERE table_name = $1)",
                    table,
                )
                assert not exists, f"{table} must be dropped after migration"
        finally:
            await conn.close()
    asyncio.run(run())


def test_apply_schema_idempotent():
    """Running apply_schema twice must not raise."""
    async def run():
        conn = await _conn()
        try:
            await apply_schema(conn)  # second run
        finally:
            await conn.close()
    asyncio.run(run())


def test_links_unique_index_exists():
    """uq_links_entity_url must exist on (entity_type, entity_id, url, link_type_id)."""
    async def run():
        conn = await _conn()
        try:
            row = await conn.fetchrow(
                "SELECT indexdef FROM pg_indexes"
                " WHERE schemaname='public' AND tablename='links'"
                "   AND indexname='uq_links_entity_url'"
            )
            assert row is not None, "uq_links_entity_url index must exist"
            indexdef = row["indexdef"].lower()
            assert "unique" in indexdef
            for col in ("entity_type", "entity_id", "url", "link_type_id"):
                assert col in indexdef, f"index must cover column {col}"
        finally:
            await conn.close()
    asyncio.run(run())


def test_links_duplicate_insert_blocked_by_unique_constraint():
    """A second raw INSERT with the same natural key must conflict.

    Verifies the UNIQUE index is enforced at the DB level. Uses a transaction
    so test data is rolled back.
    """
    async def run():
        conn = await _conn()
        tr = conn.transaction()
        await tr.start()
        try:
            oid = generate_id()
            await conn.execute("INSERT INTO organizations (id) VALUES ($1)", oid)
            lt_id = await conn.fetchval(
                "SELECT id FROM link_types WHERE slug='website'"
            )
            await conn.execute(
                "INSERT INTO links (id, entity_type, entity_id, url, link_type_id)"
                " VALUES ($1, 'organization', $2, 'https://dup.example.com', $3)",
                generate_id(), oid, lt_id,
            )
            with pytest.raises(asyncpg.exceptions.UniqueViolationError):
                await conn.execute(
                    "INSERT INTO links (id, entity_type, entity_id, url, link_type_id)"
                    " VALUES ($1, 'organization', $2, 'https://dup.example.com', $3)",
                    generate_id(), oid, lt_id,
                )
        finally:
            await tr.rollback()
            await conn.close()
    asyncio.run(run())


def test_links_on_conflict_do_nothing_is_idempotent():
    """INSERT ... ON CONFLICT DO NOTHING with the natural key must silently drop dupes.

    This is the property that makes ingestion-pipeline re-runs safe.
    """
    async def run():
        conn = await _conn()
        tr = conn.transaction()
        await tr.start()
        try:
            oid = generate_id()
            await conn.execute("INSERT INTO organizations (id) VALUES ($1)", oid)
            lt_id = await conn.fetchval(
                "SELECT id FROM link_types WHERE slug='website'"
            )
            for _ in range(3):
                await conn.execute(
                    "INSERT INTO links"
                    " (id, entity_type, entity_id, url, link_type_id)"
                    " VALUES ($1, 'organization', $2, 'https://idem.example.com', $3)"
                    " ON CONFLICT DO NOTHING",
                    generate_id(), oid, lt_id,
                )
            n = await conn.fetchval(
                "SELECT count(*) FROM links WHERE entity_id=$1", oid
            )
            assert n == 1, f"expected exactly 1 link row after 3 inserts, got {n}"
        finally:
            await tr.rollback()
            await conn.close()
    asyncio.run(run())
