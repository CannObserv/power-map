"""Shared test-database helpers.

Extracted from ``tests/conftest.py`` (#300) so out-of-process test tiers can
reuse the session pool's reference-preserving reset. The Playwright browser
sweep (``tests/api/admin/test_a11y_browser.py``) runs uvicorn in a separate
process and so can't use the in-process ``db_pool`` fixture — it prepares the
dedicated test DB with the same ``reset_data_tables`` call the pool applies at
session start.
"""

import asyncio

import asyncpg
import pytest

# Reference/lookup tables whose seed rows must survive a data reset.
REFERENCE_TABLES = frozenset(
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


async def reset_data_tables(conn: asyncpg.Connection) -> None:
    """TRUNCATE every non-reference table, resetting sequences and cascading FKs."""
    rows = await conn.fetch(
        """
        SELECT table_name
        FROM information_schema.tables
        WHERE table_schema = 'public'
          AND table_type = 'BASE TABLE'
        """
    )
    to_truncate = [r["table_name"] for r in rows if r["table_name"] not in REFERENCE_TABLES]
    if to_truncate:
        quoted = ", ".join(f'"{t}"' for t in to_truncate)
        await conn.execute(f"TRUNCATE {quoted} RESTART IDENTITY CASCADE")


async def until_lock_waiting(conn: asyncpg.Connection, pid: int, timeout: float = 10.0) -> None:
    """Return once backend ``pid`` is blocked on a lock; fail after ``timeout``.

    ``pg_stat_activity`` is snapshotted per transaction, and ``conn`` may be
    inside one, so each poll clears the snapshot first (the test pool has no
    spare connection to poll from).
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        await conn.execute("SELECT pg_stat_clear_snapshot()")
        waiting = await conn.fetchval(
            "SELECT wait_event_type = 'Lock' FROM pg_stat_activity WHERE pid = $1", pid
        )
        if waiting:
            return
        await asyncio.sleep(0.05)
    pytest.fail(f"backend {pid} never blocked on a lock")
