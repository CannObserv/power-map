"""Integration tests for admin dashboard route."""

import asyncio
import os
import re
from unittest.mock import AsyncMock, patch

import asyncpg
import pytest
from fastapi.testclient import TestClient

from src.api.main import app
from src.core.db import apply_schema, generate_id

pytestmark = pytest.mark.integration

AUTH_HEADERS = {"X-ExeDev-UserID": "usr_test", "X-ExeDev-Email": "admin@test.com"}


def _get_dsn() -> str:
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        pytest.skip("DATABASE_URL not set")
    return dsn


async def _aconnect(dsn: str) -> asyncpg.Connection:
    conn = await asyncpg.connect(dsn)
    await apply_schema(conn)
    return conn


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture
def seeded_counts():
    """Insert one active record per entity; yield expected counts; teardown."""
    dsn = _get_dsn()
    person_id = generate_id()
    org_id = generate_id()
    role_id = generate_id()
    assignment_id = generate_id()
    batch_id = generate_id()

    async def setup():
        conn = await _aconnect(dsn)
        try:
            await conn.execute("INSERT INTO people (id) VALUES ($1)", person_id)
            await conn.execute(
                "INSERT INTO organizations (id) VALUES ($1)", org_id
            )
            await conn.execute(
                "INSERT INTO roles (id, organization_id, title) VALUES ($1, $2, $3)",
                role_id, org_id, "Test Role",
            )
            await conn.execute(
                "INSERT INTO role_assignments (id, person_id, role_id) VALUES ($1, $2, $3)",
                assignment_id, person_id, role_id,
            )
            await conn.execute(
                "INSERT INTO import_batches"
                " (id, source_file, file_hash, row_count, loaded_count, error_count)"
                " VALUES ($1, $2, $3, $4, $5, $6)",
                batch_id, "test.csv", "testhash_dashboard", 0, 0, 0,
            )
        finally:
            await conn.close()

    async def teardown():
        conn = await _aconnect(dsn)
        try:
            await conn.execute(
                "DELETE FROM import_batches WHERE id = $1", batch_id
            )
            await conn.execute(
                "DELETE FROM role_assignments WHERE id = $1", assignment_id
            )
            await conn.execute("DELETE FROM roles WHERE id = $1", role_id)
            await conn.execute(
                "DELETE FROM organizations WHERE id = $1", org_id
            )
            await conn.execute("DELETE FROM people WHERE id = $1", person_id)
        finally:
            await conn.close()

    asyncio.run(setup())
    yield
    asyncio.run(teardown())


def test_dashboard_shows_counts(client, seeded_counts):
    """Entity, settings, and activity cards display numeric record counts."""
    resp = client.get("/admin/", headers=AUTH_HEADERS)
    assert resp.status_code == 200
    assert "— records" not in resp.text
    counts = re.findall(r"(\d+) records", resp.text)
    # People, Organizations, Roles, Assignments, Import History
    assert len(counts) == 5, f"Expected 5 count boxes, found: {counts}"


def test_dashboard_person_dup_badge_shown(client):
    """Person dup count badge appears when count_person_duplicates returns > 0."""
    with patch(
        "src.api.admin.router.count_person_duplicates",
        new=AsyncMock(return_value=7),
    ):
        resp = client.get("/admin/", headers=AUTH_HEADERS)
    assert resp.status_code == 200
    assert "7 duplicates" in resp.text


def test_dashboard_person_dup_badge_hidden_when_zero(client):
    """Person dup count badge is absent when count_person_duplicates returns 0."""
    with patch(
        "src.api.admin.router.count_person_duplicates",
        new=AsyncMock(return_value=0),
    ):
        resp = client.get("/admin/", headers=AUTH_HEADERS)
    assert resp.status_code == 200
    assert "people/duplicates" not in resp.text
