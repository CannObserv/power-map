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


def pytest_configure(config):
    """Swap DATABASE_URL → TEST_DATABASE_URL before any test collection."""
    test_url = os.environ.get("TEST_DATABASE_URL")
    if test_url:
        os.environ["DATABASE_URL"] = test_url


def pytest_collection_modifyitems(config, items):
    """Skip integration tests when TEST_DATABASE_URL is not set."""
    if os.environ.get("TEST_DATABASE_URL"):
        return
    skip = pytest.mark.skip(
        reason="TEST_DATABASE_URL not set — refusing to run integration tests against production DB"
    )
    for item in items:
        if "integration" in item.keywords:
            item.add_marker(skip)


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def db_pool():
    """Session-scoped asyncpg pool for integration test helpers.

    Eliminates per-helper-call TCP handshake + asyncpg type-introspection
    overhead. Schema is applied once on first acquisition; downstream fixtures
    reuse the prepared DB.

    To consume this fixture from a test module, opt the module into session
    loop scope so the pool (bound to the session loop) is awaitable from tests:

        pytestmark = [
            pytest.mark.integration,
            pytest.mark.asyncio(loop_scope="session"),
        ]

    Then have fixtures and tests accept ``db_pool`` and use
    ``async with db_pool.acquire() as conn:`` instead of
    ``await asyncpg.connect(...)``.
    """
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        pytest.skip("DATABASE_URL not set")
    pool = await asyncpg.create_pool(dsn)
    async with pool.acquire() as conn:
        await apply_schema(conn)
    try:
        yield pool
    finally:
        await pool.close()
