"""Shared fixtures for admin route tests."""

import pytest
from fastapi.testclient import TestClient

from src.api.main import app

AUTH_HEADERS = {
    "X-ExeDev-UserID": "usr_test123",
    "X-ExeDev-Email": "admin@example.com",
}


async def jurisdiction_change_count(db_pool, jurisdiction_id):
    """Count change-feed rows recorded for a jurisdiction (shared test helper)."""
    async with db_pool.acquire() as conn:
        return await conn.fetchval(
            "SELECT COUNT(*) FROM entity_changes WHERE entity_type='jurisdiction' AND entity_id=$1",
            jurisdiction_id,
        )


@pytest.fixture
def client():
    """TestClient without DB pool (no lifespan). Auth + routing tests only."""
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c
