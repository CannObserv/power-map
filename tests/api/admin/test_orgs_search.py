# tests/api/admin/test_orgs_search.py
"""Integration tests for org search typeahead endpoint."""

import asyncio
import os

import asyncpg
import pytest
from fastapi.testclient import TestClient

from src.api.main import app
from src.core.db import apply_schema, generate_id

pytestmark = pytest.mark.integration

AUTH_HEADERS = {"X-ExeDev-UserID": "usr_test", "X-ExeDev-Email": "admin@test.com"}


def _dsn():
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        pytest.skip("DATABASE_URL not set")
    return dsn


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture
def search_org_id():
    dsn = _dsn()
    oid = generate_id()

    async def setup():
        conn = await asyncpg.connect(dsn)
        await apply_schema(conn)
        try:
            await conn.execute("INSERT INTO organizations (id) VALUES ($1)", oid)
            await conn.execute(
                "INSERT INTO organization_names (id, organization_id, name, is_canonical)"
                " VALUES ($1, $2, 'Searchable Org', TRUE)",
                generate_id(), oid,
            )
        finally:
            await conn.close()

    async def teardown():
        conn = await asyncpg.connect(dsn)
        try:
            await conn.execute("DELETE FROM organization_names WHERE organization_id=$1", oid)
            await conn.execute("DELETE FROM organizations WHERE id=$1", oid)
        finally:
            await conn.close()

    asyncio.run(setup())
    yield oid
    asyncio.run(teardown())


def test_search_returns_matching_org(client, search_org_id):
    response = client.get("/admin/orgs/search/?q=Searchable", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert "Searchable Org" in response.text


def test_search_excludes_archived_org(client):
    dsn = _dsn()
    oid = generate_id()

    async def setup():
        conn = await asyncpg.connect(dsn)
        await apply_schema(conn)
        try:
            await conn.execute(
                "INSERT INTO organizations (id, archived_at) VALUES ($1, NOW())", oid
            )
            await conn.execute(
                "INSERT INTO organization_names (id, organization_id, name, is_canonical)"
                " VALUES ($1, $2, 'Archived Searchable', TRUE)",
                generate_id(), oid,
            )
        finally:
            await conn.close()

    async def teardown():
        conn = await asyncpg.connect(dsn)
        try:
            await conn.execute("DELETE FROM organization_names WHERE organization_id=$1", oid)
            await conn.execute("DELETE FROM organizations WHERE id=$1", oid)
        finally:
            await conn.close()

    asyncio.run(setup())
    try:
        response = client.get(
            "/admin/orgs/search/?q=Archived+Searchable", headers=AUTH_HEADERS
        )
        assert response.status_code == 200
        assert "Archived Searchable" not in response.text
    finally:
        asyncio.run(teardown())


def test_search_empty_query_returns_empty(client):
    response = client.get("/admin/orgs/search/?q=", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert "<li" not in response.text
