"""Shared fixtures for admin route tests."""

import pytest
from fastapi.testclient import TestClient

from src.api.main import app

AUTH_HEADERS = {
    "X-ExeDev-UserID": "usr_test123",
    "X-ExeDev-Email": "admin@example.com",
}

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
    """TestClient without DB pool (no lifespan). Auth + routing tests only."""
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c
