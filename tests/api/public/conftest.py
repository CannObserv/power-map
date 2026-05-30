"""Shared fixtures for public API tests."""

from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient

from src.api.deps import get_db
from src.api.main import app


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
