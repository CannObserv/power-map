# tests/api/admin/test_roles_search.py
"""Tests for role search typeahead endpoint."""
import os

import asyncpg
import pytest
from httpx import ASGITransport, AsyncClient

from src.api.admin.deps import get_db
from src.api.main import app
from src.core.db import apply_schema, generate_id

pytestmark = pytest.mark.integration

AUTH_HEADERS = {"X-ExeDev-UserID": "test-user", "X-ExeDev-Email": "test@example.com"}
HTMX_HEADERS = {**AUTH_HEADERS, "HX-Request": "true"}


@pytest.fixture
async def db():
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        pytest.skip("DATABASE_URL not set")
    conn = await asyncpg.connect(dsn)
    try:
        await apply_schema(conn)
        tr = conn.transaction()
        await tr.start()
        yield conn
        await tr.rollback()
    finally:
        await conn.close()


@pytest.fixture
async def client(db):
    async def _get_db_override():
        yield db
    app.dependency_overrides[get_db] = _get_db_override
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    app.dependency_overrides.pop(get_db, None)


@pytest.fixture
async def role_id(db):
    oid = generate_id()
    await db.execute("INSERT INTO organizations (id) VALUES ($1)", oid)
    await db.execute(
        "INSERT INTO organization_names (id, organization_id, name, is_canonical)"
        " VALUES ($1, $2, $3, TRUE)",
        generate_id(), oid, "Acme Corp",
    )
    rid = generate_id()
    await db.execute(
        "INSERT INTO roles (id, organization_id, title) VALUES ($1, $2, $3)",
        rid, oid, "Executive Director",
    )
    return rid


async def test_search_returns_matching_role(client, role_id):
    r = await client.get("/admin/roles/search/?q=Executive", headers=HTMX_HEADERS)
    assert r.status_code == 200
    assert b"Executive Director" in r.content


async def test_search_result_has_data_id(client, role_id):
    r = await client.get("/admin/roles/search/?q=Executive", headers=HTMX_HEADERS)
    assert r.status_code == 200
    assert f'data-id="{role_id}"'.encode() in r.content


async def test_search_result_label_includes_org_name(client, role_id):
    r = await client.get("/admin/roles/search/?q=Executive", headers=HTMX_HEADERS)
    assert r.status_code == 200
    assert b"Acme Corp" in r.content


async def test_search_empty_query_returns_empty(client, role_id):
    r = await client.get("/admin/roles/search/?q=", headers=HTMX_HEADERS)
    assert r.status_code == 200
    assert b"data-id" not in r.content


async def test_search_no_match_returns_empty(client, role_id):
    r = await client.get("/admin/roles/search/?q=zzznomatch", headers=HTMX_HEADERS)
    assert r.status_code == 200
    assert b"data-id" not in r.content


async def test_search_excludes_archived_roles(client, db):
    oid = generate_id()
    await db.execute("INSERT INTO organizations (id) VALUES ($1)", oid)
    await db.execute(
        "INSERT INTO organization_names (id, organization_id, name, is_canonical)"
        " VALUES ($1, $2, $3, TRUE)",
        generate_id(), oid, "Old Org",
    )
    rid = generate_id()
    await db.execute(
        "INSERT INTO roles (id, organization_id, title, archived_at)"
        " VALUES ($1, $2, $3, NOW())",
        rid, oid, "Archived Role",
    )
    r = await client.get("/admin/roles/search/?q=Archived", headers=HTMX_HEADERS)
    assert r.status_code == 200
    assert b"Archived Role" not in r.content
