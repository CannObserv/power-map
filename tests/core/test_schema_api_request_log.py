"""Integration test: api_request_log table + indexes exist after apply_schema (#260)."""

import pytest

pytestmark = [
    pytest.mark.integration,
]

_EXPECTED_COLUMNS = {
    "id",
    "occurred_at",
    "api_key_id",
    "method",
    "path",
    "route_group",
    "entity_type",
    "status_code",
    "latency_ms",
    "disposition",
    "result_entity_id",
    "reason",
    "item_count",
    "is_empty",
    "client_ip",
    "user_agent",
    "request_body",
    "response_body",
}

_EXPECTED_INDEXES = {
    "idx_arl_occurred",
    "idx_arl_key_occurred",
    "idx_arl_group_occurred",
    "idx_arl_problems",
}


@pytest.mark.integration
async def test_api_request_log_columns(db_pool):
    async with db_pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT column_name, data_type
            FROM information_schema.columns
            WHERE table_name = 'api_request_log'
            """
        )
    cols = {r["column_name"]: r["data_type"] for r in rows}
    assert _EXPECTED_COLUMNS <= set(cols), _EXPECTED_COLUMNS - set(cols)
    assert cols["request_body"] == "jsonb"
    assert cols["response_body"] == "jsonb"


@pytest.mark.integration
async def test_api_request_log_indexes(db_pool):
    async with db_pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT indexname FROM pg_indexes WHERE tablename = 'api_request_log'"
        )
    names = {r["indexname"] for r in rows}
    assert _EXPECTED_INDEXES <= names, _EXPECTED_INDEXES - names


@pytest.mark.integration
async def test_api_request_log_api_key_fk_is_set_null(db_pool):
    """FK on api_key_id must be ON DELETE SET NULL so the log survives key removal."""
    async with db_pool.acquire() as conn:
        deltype = await conn.fetchval(
            """
            SELECT confdeltype
            FROM pg_constraint
            WHERE conrelid = 'api_request_log'::regclass
              AND contype = 'f'
              AND confrelid = 'api_keys'::regclass
            """
        )
    # 'n' = SET NULL in pg_constraint.confdeltype (asyncpg returns "char" as bytes)
    assert deltype in ("n", b"n")


@pytest.mark.integration
async def test_api_request_log_defaults(db_pool):
    """is_empty defaults FALSE; occurred_at has a default."""
    async with db_pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT column_name, column_default, is_nullable
            FROM information_schema.columns
            WHERE table_name = 'api_request_log'
              AND column_name IN ('is_empty', 'occurred_at')
            """
        )
    by_name = {r["column_name"]: r for r in rows}
    assert by_name["is_empty"]["column_default"] is not None
    assert "false" in by_name["is_empty"]["column_default"].lower()
    assert by_name["occurred_at"]["column_default"] is not None
