"""Root test configuration.

Redirects DATABASE_URL to TEST_DATABASE_URL when the latter is set, so that
integration tests never touch the production database when run with the
standard `.env` file loaded.

If TEST_DATABASE_URL is absent, all integration-marked tests are skipped rather
than falling through to the production DATABASE_URL.
"""

import os

import asyncpg
import pytest
import pytest_asyncio

from src.core.db import apply_schema

INTEGRATION_SKIP_REASON = (
    "TEST_DATABASE_URL not set — set it in .env (see docs/COMMANDS.md); skipping integration tests"
)

# Reference/lookup tables whose seed rows must survive the per-session truncation.
_REFERENCE_TABLES = frozenset(
    {
        "link_types",
        "entity_identifier_types",
        "entity_event_types",
        "jurisdiction_types",
        "jurisdiction_relationship_types",
        "bcp47_locales",
        "iso15924_scripts",
        "api_key_scope_types",
        "embedding_model_registry",
    }
)


async def _reset_data_tables(conn: asyncpg.Connection) -> None:
    """TRUNCATE every non-reference table, resetting sequences and cascading FKs."""
    rows = await conn.fetch(
        """
        SELECT table_name
        FROM information_schema.tables
        WHERE table_schema = 'public'
          AND table_type = 'BASE TABLE'
        """
    )
    to_truncate = [r["table_name"] for r in rows if r["table_name"] not in _REFERENCE_TABLES]
    if to_truncate:
        quoted = ", ".join(f'"{t}"' for t in to_truncate)
        await conn.execute(f"TRUNCATE {quoted} RESTART IDENTITY CASCADE")


def pytest_configure(config):
    """Swap DATABASE_URL → TEST_DATABASE_URL before any test collection."""
    test_url = os.environ.get("TEST_DATABASE_URL")
    if test_url:
        os.environ["DATABASE_URL"] = test_url


def pytest_collection_modifyitems(config, items):
    """Skip integration tests when TEST_DATABASE_URL is not set."""
    if os.environ.get("TEST_DATABASE_URL"):
        return
    skip = pytest.mark.skip(reason=INTEGRATION_SKIP_REASON)
    for item in items:
        if "integration" in item.keywords:
            item.add_marker(skip)


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def db_pool():
    """Session-scoped asyncpg pool for integration test helpers.

    Eliminates per-helper-call TCP handshake + asyncpg type-introspection
    overhead. Schema is applied once on first acquisition; downstream fixtures
    reuse the prepared DB. Data tables (everything except reference/lookup
    tables) are truncated at session start to prevent cross-session accumulation.

    To consume this fixture from a test module, mark it as integration:

        pytestmark = pytest.mark.integration

    Then have fixtures and tests accept ``db_pool`` and use
    ``async with db_pool.acquire() as conn:`` instead of
    ``await asyncpg.connect(...)``.
    """
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        pytest.skip("DATABASE_URL not set")
    pool = await asyncpg.create_pool(dsn)
    try:
        async with pool.acquire() as conn:
            await apply_schema(conn)
            await _reset_data_tables(conn)
        yield pool
    finally:
        await pool.close()
