"""Shared fixtures for admin route tests."""

import pytest
from fastapi.testclient import TestClient

from src.api.admin.org_dups import invalidate_dup_count_cache as invalidate_org_dups
from src.api.admin.people_dups import (
    invalidate_dup_count_cache as invalidate_people_dups,
)
from src.api.main import app

AUTH_HEADERS = {
    "X-ExeDev-UserID": "usr_test123",
    "X-ExeDev-Email": "admin@example.com",
}


@pytest.fixture(autouse=True)
def _reset_dup_count_caches():
    """Drop the process-local TTL caches between admin tests.

    Both `org_dups` and `people_dups` keep a 5-minute in-process cache. Without
    invalidation the second test in a session sees a stale value (0 if the
    fixture-inserted duplicates hadn't been written yet at the first call, or a
    leftover non-zero count from a prior pair) which masks banner / count
    assertions. Reset on every admin test for deterministic behaviour.
    """
    invalidate_org_dups()
    invalidate_people_dups()
    yield
    invalidate_org_dups()
    invalidate_people_dups()


@pytest.fixture
def client():
    """TestClient without DB pool (no lifespan). Auth + routing tests only."""
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c
