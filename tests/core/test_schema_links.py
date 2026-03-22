"""Integration tests for link_types and links schema."""

import asyncio
import os

import asyncpg
import pytest

from src.core.db import apply_schema

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
