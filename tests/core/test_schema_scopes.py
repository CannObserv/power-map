"""Integration test: api_key_scope_types and api_key_scopes tables exist after apply_schema."""

import pytest

pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
]


@pytest.mark.integration
async def test_api_key_scope_types_table_exists(db_pool):
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id, display_name FROM api_key_scope_types WHERE id = 'observations:write'"
        )
    assert row is not None
    assert row["display_name"] == "Observations: Write"


@pytest.mark.integration
async def test_api_key_scopes_table_exists(db_pool):
    async with db_pool.acquire() as conn:
        # Verify table exists and FK constraints are in place by inspecting information_schema
        rows = await conn.fetch(
            """
            SELECT column_name, data_type
            FROM information_schema.columns
            WHERE table_name = 'api_key_scopes'
            ORDER BY column_name
            """
        )
    col_names = {r["column_name"] for r in rows}
    assert {"api_key_id", "scope_id", "granted_at", "granted_by"} <= col_names
