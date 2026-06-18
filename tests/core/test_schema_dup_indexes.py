"""Integration tests: GiST trigram indexes for similarity-based dup detection."""

import pytest
import pytest_asyncio

pytestmark = [
    pytest.mark.integration,
]


@pytest_asyncio.fixture(loop_scope="session")
async def db(db_pool):
    async with db_pool.acquire() as conn:
        yield conn


@pytest.mark.parametrize(
    "table,column,partial_where",
    [
        ("organization_names", "name", None),
        ("person_names", "name", "visibility = 'public'"),
    ],
)
async def test_trgm_gist_index_exists(db, table, column, partial_where):
    """GiST trigram index must exist so similarity() joins can use index scans."""
    row = await db.fetchrow(
        """
        SELECT indexdef
        FROM pg_indexes
        WHERE tablename = $1
          AND indexdef ILIKE '%using gist%'
          AND indexdef ILIKE '%gist_trgm_ops%'
          AND indexdef ILIKE '%' || $2 || '%'
        """,
        table,
        column,
    )
    assert row is not None, f"missing pg_trgm GiST index on {table}({column})"
    if partial_where:
        assert partial_where in row["indexdef"], (
            f"GiST index on {table}({column}) missing partial WHERE: {partial_where!r}"
        )
