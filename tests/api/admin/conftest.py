"""Shared fixtures for admin route tests."""

import os

import asyncpg
import pytest
import pytest_asyncio
from fastapi.testclient import TestClient

from src.api.main import app
from src.core.embedding_registry import EmbeddingRegistry

AUTH_HEADERS = {
    "X-ExeDev-UserID": "usr_test123",
    "X-ExeDev-Email": "admin@example.com",
}


@pytest_asyncio.fixture(scope="session", loop_scope="session", autouse=True)
async def _init_app_embedding_registry():
    """Populate ``app.state.embedding_registry`` once per session.

    Admin route tests drive the app lifespan-less (#288), so the lifespan's
    ``app.state.embedding_registry`` initialization never runs. Person detail /
    name pages read ``request.app.state.embedding_registry``; without this they
    raise ``AttributeError``. Mirrors the lifespan: load from the DB when a test
    DB is configured, else an empty registry (matches the lifespan's no-DSN
    branch, keeps DB-free unit runs working).
    """
    test_url = os.environ.get("TEST_DATABASE_URL")
    if test_url:
        pool = await asyncpg.create_pool(test_url, min_size=1, max_size=1)
        try:
            async with pool.acquire() as conn:
                app.state.embedding_registry = await EmbeddingRegistry.load(conn)
        finally:
            await pool.close()
    else:
        app.state.embedding_registry = EmbeddingRegistry({})
    yield


# Canonical entity ordering across the admin shell (#275): Jurisdiction, Org,
# Person, Role, Assignment. Asserted in the sidebar nav (base.html), the
# dashboard cards, and the entities landing cards so the three surfaces never
# drift out of sync. Href strings are unique per surface region, so
# first-occurrence position reflects render order.
ENTITY_ORDER_HREFS = [
    'href="/admin/jurisdictions/"',
    'href="/admin/orgs/"',
    'href="/admin/people/"',
    'href="/admin/roles/"',
    'href="/admin/role-assignments/"',
]


def assert_render_order(haystack, needles):
    """Assert each needle appears in ``haystack`` and their first-occurrence
    positions are strictly increasing (i.e. rendered in ``needles`` order)."""
    seen = []
    for needle in needles:
        idx = haystack.find(needle)
        assert idx != -1, f"{needle!r} not found in rendered output"
        seen.append((idx, needle))
    actual = [n for _, n in sorted(seen)]
    assert actual == needles, f"Expected order {needles}, got {actual}"


async def jurisdiction_change_count(db_pool, jurisdiction_id):
    """Count change-feed rows recorded for a jurisdiction (shared test helper)."""
    async with db_pool.acquire() as conn:
        return await conn.fetchval(
            "SELECT COUNT(*) FROM entity_changes WHERE entity_type='jurisdiction' AND entity_id=$1",
            jurisdiction_id,
        )


@pytest.fixture
def client():
    """TestClient without DB pool (no lifespan). Auth + routing tests only.

    Constructed without `with`, so the app lifespan never runs and no app pool
    is created (#288) — matching this fixture's DB-free contract. Consumers that
    need a real DB define their own rollback client (see ``test_orgs.py``).
    """
    return TestClient(app, raise_server_exceptions=False)
