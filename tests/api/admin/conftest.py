"""Shared fixtures for admin route tests."""

import pytest
from fastapi.testclient import TestClient

from src.api.main import app

AUTH_HEADERS = {
    "X-ExeDev-UserID": "usr_test123",
    "X-ExeDev-Email": "admin@example.com",
}


@pytest.fixture
def client():
    """TestClient without DB pool (no lifespan). Auth + routing tests only."""
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c
