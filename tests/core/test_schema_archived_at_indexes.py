"""Integration tests: partial indexes on archived_at IS NULL for entity tables."""

import pytest
import pytest_asyncio

pytestmark = [
    pytest.mark.integration,
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


@pytest.mark.parametrize(
    "table,index_name",
    [
        ("people", "idx_people_archived_at"),
        ("organizations", "idx_organizations_archived_at"),
        ("roles", "idx_roles_archived_at"),
        ("role_assignments", "idx_role_assignments_archived_at"),
    ],
)
async def test_archived_at_partial_index_exists(db, table, index_name):
    """Partial index on archived_at IS NULL must exist for COUNT(*) index-only scans."""
    row = await db.fetchrow(
        """
        SELECT indexdef
        FROM pg_indexes
        WHERE tablename = $1
          AND indexname = $2
          AND indexdef ILIKE '%archived_at is null%'
        """,
        table,
        index_name,
    )
    assert row is not None, (
        f"missing partial index {index_name} on {table} WHERE archived_at IS NULL"
    )
