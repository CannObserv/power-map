"""Shared fixtures for public API tests.

Endpoint tests use the lifespan-less rollback client (#288): an ``AsyncClient``
over ``ASGITransport`` whose ``get_db`` override yields a single
BEGIN/ROLLBACK-wrapped session-pool connection. No app lifespan → no per-test
``asyncpg.create_pool``; app requests and fixture setup share one transaction
that rolls back automatically, so ``api_key`` / data fixtures need no manual
teardown. Because app and connection run on the same session event loop, this
requires async ``AsyncClient`` (not sync ``TestClient``); endpoint tests using
``client`` must therefore be ``async def`` and ``await`` their calls.
"""

import os
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient

from src.api.deps import get_db
from src.api.main import app
from src.api.public import deps as public_deps
from src.api.public import ratelimit
from src.core import db as core_db
from src.core.db import generate_id
from src.core.embedding_registry import EmbeddingRegistry


@pytest.fixture(autouse=True)
def _reset_rate_limit_state():
    """Fresh limiter buckets + last_used_at debounce stamps per test (#292).

    Both live in per-worker module dicts keyed by api_key_id. Tests mint fresh
    ULID keys so cross-test bleed is unlikely, but a session-scoped key fixture
    or a request-heavy test could otherwise flake mysteriously — reset both
    unconditionally.
    """
    ratelimit.reset()
    public_deps.reset_last_used_stamps()
    yield
    ratelimit.reset()
    public_deps.reset_last_used_stamps()


@pytest_asyncio.fixture(scope="session", loop_scope="session", autouse=True)
async def _init_app_state():
    """Mirror the app lifespan once per session (tests run lifespan-less, #288).

    Two pieces of lifespan state that public routes/middleware depend on:
    - ``app.state.embedding_registry`` — read by people / embeddings endpoints.
    - the module-global asyncpg pool (``src.core.db._pool``) — the request-log
      middleware writes its row on a fire-and-forget background task via
      ``db.get_pool()`` (deliberately a separate, committed connection, not the
      request-scoped one), so it needs the real global pool to exist.

    When a test DB is configured, create the global pool and load the registry
    from it (exactly what the lifespan does); otherwise fall back to an empty
    registry so DB-free unit runs keep working.
    """
    test_url = os.environ.get("TEST_DATABASE_URL")
    if not test_url:
        app.state.embedding_registry = EmbeddingRegistry({})
        yield
        return
    await core_db.create_pool(test_url)
    try:
        async with core_db.get_pool().acquire() as conn:
            app.state.embedding_registry = await EmbeddingRegistry.load(conn)
        yield
    finally:
        await core_db.close_pool()


@pytest_asyncio.fixture(loop_scope="session")
async def db(db_pool):
    """Pool-acquired connection wrapped in a rolled-back transaction.

    App writes (via the ``client`` get_db override) and fixture setup share this
    one connection/transaction, which is rolled back at test end — providing
    per-test isolation with no manual teardown.
    """
    async with db_pool.acquire() as conn:
        tr = conn.transaction()
        await tr.start()
        try:
            yield conn
        finally:
            await tr.rollback()


@pytest_asyncio.fixture(loop_scope="session")
async def client(db):
    """AsyncClient over the app, overriding get_db to the test connection."""

    async def _get_db_override():
        yield db

    app.dependency_overrides[get_db] = _get_db_override
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test", follow_redirects=True
    ) as c:
        yield c
    app.dependency_overrides.pop(get_db, None)


@pytest_asyncio.fixture(loop_scope="session")
async def committing_db(db_pool):
    """Autocommit connection (NO wrapping transaction) for tests that need writes
    in *separate* transactions.

    Two cases the single-transaction rollback `db` cannot serve (#288):
    - ``updated_at`` / etag must *advance* between two writes — Postgres ``now()``
      is the transaction start time, constant within one transaction, so the
      rollback client freezes it.
    - a *separate* connection must see the row (e.g. the request-log middleware
      writes via the global pool on a background task).

    Each statement autocommits, so timestamps advance and other connections see
    the data. No teardown: rows leak but carry unique ULIDs and are cleared by the
    session-start truncation (matches the pre-#288 public `db` semantics).
    """
    async with db_pool.acquire() as conn:
        yield conn


@pytest_asyncio.fixture(loop_scope="session")
async def committing_client(committing_db):
    """Lifespan-less client whose get_db yields the autocommit ``committing_db``.

    No per-test pool (like ``client``), but writes commit instead of rolling back
    — for the etag / middleware tests that need cross-transaction visibility.
    """

    async def _get_db_override():
        yield committing_db

    app.dependency_overrides[get_db] = _get_db_override
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test", follow_redirects=True
    ) as c:
        yield c
    app.dependency_overrides.pop(get_db, None)


@pytest.fixture
def unit_client():
    """Sync TestClient with get_db mocked — auth-reject tests that never touch DB.

    Constructed without ``with`` so no lifespan runs and no app pool is created
    (#288). Stays a sync client; consumers may remain sync ``def`` tests.
    """

    async def _noop_db():
        yield AsyncMock()

    app.dependency_overrides[get_db] = _noop_db
    try:
        yield TestClient(app, raise_server_exceptions=False)
    finally:
        app.dependency_overrides.pop(get_db, None)


@pytest_asyncio.fixture(loop_scope="session")
async def link_type(db):
    """Ensure a 'website' link type exists; reuse existing row if present."""
    row = await db.fetchrow("SELECT id FROM link_types WHERE slug='website' LIMIT 1")
    if row:
        return row["id"]
    lt_id = generate_id()
    await db.execute(
        "INSERT INTO link_types (id, slug, display_name, is_social)"
        " VALUES ($1,'website','Website',FALSE)",
        lt_id,
    )
    return lt_id
