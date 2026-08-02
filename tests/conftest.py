"""Root test configuration.

Redirects DATABASE_URL to TEST_DATABASE_URL when the latter is set, so that
integration tests never touch the production database when run with the
standard `.env` file loaded.

If TEST_DATABASE_URL is absent, all integration-marked tests are skipped.
The db_pool fixture hard-fails (not skips) when TEST_DATABASE_URL is unset —
this is a last-resort guard to prevent accidental production DB truncation if
a test requests db_pool without the integration marker.
"""

import os

import asyncpg
import pytest
import pytest_asyncio

from src.core.db import apply_schema
from src.core.normalizers import address as addr_mod

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
        "role_assignment_relationship_types",
        "organization_jurisdiction_affiliation_types",
        "role_types",
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
    # Cap the app-level pool (created by TestClient lifespan) at two connections
    # so the full suite doesn't exhaust DO DB slots (issue #226).
    # The test db_pool fixture passes explicit min/max and ignores these.
    os.environ.setdefault("DB_POOL_MIN_SIZE", "1")
    os.environ.setdefault("DB_POOL_MAX_SIZE", "2")


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

    Hard-fails (not skips) when TEST_DATABASE_URL is unset — last-resort
    guard against accidental production DB truncation when a test requests
    this fixture without the integration marker.
    """
    test_url = os.environ.get("TEST_DATABASE_URL")
    if not test_url:
        pytest.fail(
            "db_pool requires TEST_DATABASE_URL — refusing to operate on a non-test database. "
            "Set TEST_DATABASE_URL in .env (see docs/COMMANDS.md)."
        )
    pool = await asyncpg.create_pool(test_url, min_size=1, max_size=2)
    try:
        async with pool.acquire() as conn:
            await apply_schema(conn)
            await _reset_data_tables(conn)
        yield pool
    finally:
        await pool.close()


@pytest.fixture
def local_address_normalizer(monkeypatch):
    """Pin the local (usaddress) normalizer for tests that write address claims.

    Deletes ADDRESS_VALIDATOR_API_KEY and resets the cached normalizer so address
    claims normalize deterministically without depending on the external validator.
    Request it from any test — writer-level or endpoint-level — whose path reaches
    ``write_addresses``.
    """
    monkeypatch.delenv("ADDRESS_VALIDATOR_API_KEY", raising=False)
    addr_mod._reset_normalizer()
    try:
        yield
    finally:
        addr_mod._reset_normalizer()
