"""Integration test: api_key_scope_types and api_key_scopes tables exist after apply_schema."""

import inspect
import re

import pytest
from fastapi.routing import APIRoute

from src.api.main import app

pytestmark = [
    pytest.mark.integration,
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


@pytest.mark.integration
async def test_api_key_scope_types_seed_description(db_pool):
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT description FROM api_key_scope_types WHERE id = 'observations:write'"
        )
    assert row is not None
    assert "observations" in row["description"].lower()


def _routes_gated_on(scope_id: str) -> set[str]:
    """``"METHOD path"`` for every app route whose dependency tree includes
    ``require_scope(scope_id)``; path params read ``{id}``, as descriptions do."""

    def _scopes(dependant) -> set[str]:
        found = set()
        for dep in dependant.dependencies:
            call = dep.call
            if getattr(call, "__qualname__", "").startswith("require_scope."):
                found.add(inspect.getclosurevars(call).nonlocals["scope_id"])
            found |= _scopes(dep)
        return found

    return {
        f"{method} {re.sub(r'{[a-z_]*id}', '{id}', route.path)}"
        for route in app.routes
        if isinstance(route, APIRoute) and scope_id in _scopes(route.dependant)
        for method in route.methods
    }


@pytest.mark.integration
async def test_voice_embeddings_scope_descriptions_name_all_routes(db_pool):
    """Scope descriptions surface in the admin key-management UI — they must
    state the full blast radius of a biometric-data grant (#299 CR)."""
    async with db_pool.acquire() as conn:
        read = await conn.fetchval(
            "SELECT description FROM api_key_scope_types WHERE id = 'voice_embeddings:read'"
        )
        write = await conn.fetchval(
            "SELECT description FROM api_key_scope_types WHERE id = 'voice_embeddings:write'"
        )
    # Every route the read scope gates, derived from the app — a hand-kept list
    # let verify-batch, presence and identify-batch go unnamed (#638 CR 1/7).
    gated = _routes_gated_on("voice_embeddings:read")
    assert "POST /api/v1/people/identify-batch" in gated  # the walk found them
    for route in sorted(gated):
        assert re.search(re.escape(route) + r"(?![\w/-])", read), (
            f"read scope description missing '{route}': {read}"
        )
    for fragment in ("write", "patch", "archive", "restore"):
        assert fragment in write.lower(), f"write scope description missing '{fragment}': {write}"


@pytest.mark.integration
async def test_api_key_scopes_fk_enforced(db_pool):
    """Insert with invalid scope_id must raise."""
    import asyncpg

    async with db_pool.acquire() as conn:
        with pytest.raises(asyncpg.ForeignKeyViolationError):
            await conn.execute(
                "INSERT INTO api_key_scopes (api_key_id, scope_id) VALUES ($1, $2)",
                "nonexistent-key-id",
                "nonexistent:scope",
            )


@pytest.mark.integration
async def test_subscriptions_scope_is_retired(db_pool):
    """The change feed's write scope went with it (#502)."""
    async with db_pool.acquire() as conn:
        assert not await conn.fetchval(
            "SELECT EXISTS (SELECT 1 FROM api_key_scope_types WHERE id = 'subscriptions:write')"
        )
        assert not await conn.fetchval(
            "SELECT EXISTS (SELECT 1 FROM api_key_scopes WHERE scope_id = 'subscriptions:write')"
        )


@pytest.mark.integration
async def test_subscription_table_is_dropped(db_pool):
    """``api_key_entity_subscriptions`` fed only ``/changes`` (#502)."""
    async with db_pool.acquire() as conn:
        assert await conn.fetchval("SELECT to_regclass('api_key_entity_subscriptions')") is None
