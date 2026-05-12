"""Integration tests for admin dashboard route."""

import re

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient

from src.api.admin.people_dups import get_person_dup_count
from src.api.deps import get_db
from src.api.main import app
from src.core.db import generate_id

pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
]

AUTH_HEADERS = {"X-ExeDev-UserID": "usr_test", "X-ExeDev-Email": "admin@test.com"}


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


@pytest_asyncio.fixture(loop_scope="session")
async def seeded_counts(db_pool):
    """Insert one active record per entity; yield expected counts; teardown."""
    person_id = generate_id()
    org_id = generate_id()
    role_id = generate_id()
    assignment_id = generate_id()
    batch_id = generate_id()

    async with db_pool.acquire() as conn:
        await conn.execute("INSERT INTO people (id) VALUES ($1)", person_id)
        await conn.execute("INSERT INTO organizations (id) VALUES ($1)", org_id)
        await conn.execute(
            "INSERT INTO roles (id, organization_id, title) VALUES ($1, $2, $3)",
            role_id,
            org_id,
            "Test Role",
        )
        await conn.execute(
            "INSERT INTO role_assignments (id, person_id, role_id) VALUES ($1, $2, $3)",
            assignment_id,
            person_id,
            role_id,
        )
        await conn.execute(
            "INSERT INTO import_batches"
            " (id, source_file, file_hash, row_count, loaded_count, error_count)"
            " VALUES ($1, $2, $3, $4, $5, $6)",
            batch_id,
            "test.csv",
            "testhash_dashboard",
            0,
            0,
            0,
        )

    yield

    async with db_pool.acquire() as conn:
        await conn.execute("DELETE FROM import_batches WHERE id = $1", batch_id)
        await conn.execute("DELETE FROM role_assignments WHERE id = $1", assignment_id)
        await conn.execute("DELETE FROM roles WHERE id = $1", role_id)
        await conn.execute("DELETE FROM organizations WHERE id = $1", org_id)
        await conn.execute("DELETE FROM people WHERE id = $1", person_id)


async def test_dashboard_shows_counts(client, seeded_counts):
    """Entity, settings, and activity cards display numeric record counts."""
    resp = client.get("/admin/", headers=AUTH_HEADERS)
    assert resp.status_code == 200
    assert "— records" not in resp.text
    counts = re.findall(r"(\d+) records", resp.text)
    # People, Organizations, Roles, Assignments, Import History
    assert len(counts) == 5, f"Expected 5 count boxes, found: {counts}"


async def test_dashboard_person_dup_badge_shown(client):
    """Person dup count badge appears when get_person_dup_count returns > 0."""
    app.dependency_overrides[get_person_dup_count] = lambda: 7
    try:
        resp = client.get("/admin/", headers=AUTH_HEADERS)
    finally:
        app.dependency_overrides.pop(get_person_dup_count, None)
    assert resp.status_code == 200
    assert "7 duplicates" in resp.text


async def test_dashboard_person_dup_badge_hidden_when_zero(client):
    """Person dup count badge is absent when get_person_dup_count returns 0."""
    app.dependency_overrides[get_person_dup_count] = lambda: 0
    try:
        resp = client.get("/admin/", headers=AUTH_HEADERS)
    finally:
        app.dependency_overrides.pop(get_person_dup_count, None)
    assert resp.status_code == 200
    assert "people/duplicates" not in resp.text


async def test_dashboard_routes_db_through_get_db_dep(client):
    """Dashboard's DB connection must come from Depends(get_db) so test overrides apply (#147)."""
    call_count = {"n": 0}

    async def counting_get_db():
        call_count["n"] += 1
        async for conn in get_db():
            yield conn

    app.dependency_overrides[get_db] = counting_get_db
    try:
        resp = client.get("/admin/", headers=AUTH_HEADERS)
    finally:
        app.dependency_overrides.pop(get_db, None)

    assert resp.status_code == 200
    assert call_count["n"] >= 1, "dashboard route did not route DB acquisition through get_db dep"
