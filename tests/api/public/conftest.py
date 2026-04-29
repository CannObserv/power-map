"""Shared fixtures for public API tests."""

import os
from unittest.mock import AsyncMock

import asyncpg
import pytest
from fastapi.testclient import TestClient

from src.api.deps import get_db
from src.api.main import app
from src.core.db import apply_schema


@pytest.fixture
async def db():
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        pytest.skip("DATABASE_URL not set")
    conn = await asyncpg.connect(dsn)
    try:
        await apply_schema(conn)
        yield conn
    finally:
        await conn.close()


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
