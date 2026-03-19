"""Integration tests for admin roles views.

Requires DATABASE_URL. Run with:
    DATABASE_URL=<dsn> uv run pytest tests/api/admin/test_roles.py -m integration -v
"""

import asyncio
import os

import asyncpg
import pytest
from fastapi.testclient import TestClient

from src.api.main import app
from src.core.db import apply_schema, generate_id

pytestmark = pytest.mark.integration

AUTH_HEADERS = {
    "X-ExeDev-UserID": "usr_test",
    "X-ExeDev-Email": "admin@test.com",
}


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
async def org_id(db):
    oid = generate_id()
    await db.execute("INSERT INTO organizations (id) VALUES ($1)", oid)
    await db.execute(
        "INSERT INTO organization_names (id, organization_id, name, is_canonical)"
        " VALUES ($1, $2, 'Test Org', TRUE)",
        generate_id(), oid,
    )
    return oid


@pytest.fixture
async def role_id(db, org_id):
    rid = generate_id()
    await db.execute(
        "INSERT INTO roles (id, organization_id, title) VALUES ($1, $2, 'Test Role')",
        rid, org_id,
    )
    return rid


def test_roles_list_returns_200(client):
    response = client.get("/admin/roles/", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert "roles" in response.text.lower()


def test_roles_list_redirects_unauthenticated(client):
    response = client.get("/admin/roles/", follow_redirects=False)
    assert response.status_code in (302, 307)
    assert "/__exe.dev/login" in response.headers["location"]


def test_role_detail_returns_200(client, role_id):
    response = client.get(f"/admin/roles/{role_id}/", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert "Test Role" in response.text


def test_role_detail_404_for_unknown(client):
    response = client.get(f"/admin/roles/{generate_id()}/", headers=AUTH_HEADERS)
    assert response.status_code == 404


def test_create_role_post_redirects(client, org_id):
    response = client.post(
        "/admin/roles/new/",
        headers=AUTH_HEADERS,
        data={"organization_id": org_id, "title": "New Role", "notes": ""},
        follow_redirects=False,
    )
    assert response.status_code in (302, 303)


def test_archive_role(client, role_id):
    response = client.post(
        f"/admin/roles/{role_id}/archive/",
        headers=AUTH_HEADERS,
        follow_redirects=False,
    )
    assert response.status_code in (302, 303)


def test_hard_delete_requires_archive(client, role_id):
    response = client.delete(f"/admin/roles/{role_id}/", headers=AUTH_HEADERS)
    assert response.status_code == 409


def test_hard_delete_archived_role(client, db, role_id):
    asyncio.get_event_loop().run_until_complete(
        db.execute("UPDATE roles SET archived_at = NOW() WHERE id = $1", role_id)
    )
    response = client.delete(f"/admin/roles/{role_id}/", headers=AUTH_HEADERS)
    assert response.status_code == 200
