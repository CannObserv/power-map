"""Shared fixtures for public API tests."""

from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient

from src.api.deps import get_db
from src.api.main import app
from src.core.db import generate_id
from src.core.normalizers import address as addr_mod


@pytest_asyncio.fixture(loop_scope="session")
async def db(db_pool):
    """Per-test connection acquired from the session-scoped pool."""
    async with db_pool.acquire() as conn:
        yield conn


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture
def local_address_normalizer(monkeypatch):
    """Pin the local (usaddress) normalizer for address-writing observation tests.

    Deletes ADDRESS_VALIDATOR_API_KEY and resets the cached normalizer so address
    claims normalize deterministically without depending on the external validator.
    Mirrors the pinning in tests/core/test_observation_writers.py; request it from
    any endpoint test whose payload carries `addresses`.
    """
    monkeypatch.delenv("ADDRESS_VALIDATOR_API_KEY", raising=False)
    addr_mod._reset_normalizer()
    try:
        yield
    finally:
        addr_mod._reset_normalizer()


@pytest.fixture
def unit_client():
    """TestClient with get_db overridden — for auth-reject tests that never touch the DB."""

    async def _noop_db():
        yield AsyncMock()

    app.dependency_overrides[get_db] = _noop_db
    try:
        with TestClient(app, raise_server_exceptions=False) as c:
            yield c
    finally:
        app.dependency_overrides.pop(get_db, None)


@pytest_asyncio.fixture(loop_scope="session")
async def link_type(db):
    """Ensure a 'website' link type exists; reuse existing row if present."""
    row = await db.fetchrow("SELECT id FROM link_types WHERE slug='website' LIMIT 1")
    if row:
        yield row["id"]
        return
    lt_id = generate_id()
    await db.execute(
        "INSERT INTO link_types (id, slug, display_name, is_social)"
        " VALUES ($1,'website','Website',FALSE)",
        lt_id,
    )
    yield lt_id
