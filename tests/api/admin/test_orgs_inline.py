# tests/api/admin/test_orgs_inline.py
"""Integration tests for org core fields inline editing."""

import asyncio
import os

import asyncpg
import pytest
from fastapi.testclient import TestClient

from src.api.main import app
from src.core.db import apply_schema, generate_id

pytestmark = pytest.mark.integration
AUTH_HEADERS = {"X-ExeDev-UserID": "usr_test", "X-ExeDev-Email": "admin@test.com"}
HTMX_HEADERS = {**AUTH_HEADERS, "HX-Request": "true"}


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
def org_id():
    dsn = _dsn()
    oid = generate_id()

    async def setup():
        conn = await asyncpg.connect(dsn)
        await apply_schema(conn)
        try:
            await conn.execute("INSERT INTO organizations (id) VALUES ($1)", oid)
            await conn.execute(
                "INSERT INTO organization_names (id, organization_id, name, is_canonical)"
                " VALUES ($1, $2, 'Inline Test Org', TRUE)",
                generate_id(),
                oid,
            )
        finally:
            await conn.close()

    async def teardown():
        conn = await asyncpg.connect(dsn)
        try:
            await conn.execute(
                "DELETE FROM organization_acronyms WHERE organization_id=$1", oid
            )
            await conn.execute(
                "DELETE FROM organization_names WHERE organization_id=$1", oid
            )
            await conn.execute("DELETE FROM organizations WHERE id=$1", oid)
        finally:
            await conn.close()

    asyncio.run(setup())
    yield oid
    asyncio.run(teardown())


def test_core_fields_get_returns_partial(client, org_id):
    r = client.get(f"/admin/orgs/{org_id}/inline/core/", headers=HTMX_HEADERS)
    assert r.status_code == 200
    assert "Inline Test Org" in r.text


def test_core_fields_post_updates_name(client, org_id):
    r = client.post(
        f"/admin/orgs/{org_id}/inline/core/",
        headers=HTMX_HEADERS,
        data={"name": "Updated Name", "acronym": "", "active": "true", "notes": ""},
        follow_redirects=False,
    )
    assert r.status_code == 200
    assert "Updated Name" in r.text


def test_core_fields_post_missing_name_returns_form(client, org_id):
    r = client.post(
        f"/admin/orgs/{org_id}/inline/core/",
        headers=HTMX_HEADERS,
        data={"name": "", "acronym": "", "active": "true", "notes": ""},
    )
    assert r.status_code == 422 or "required" in r.text.lower()


def test_core_fields_edit_get_returns_form(client, org_id):
    r = client.get(f"/admin/orgs/{org_id}/inline/core/edit/", headers=HTMX_HEADERS)
    assert r.status_code == 200
    assert "<form" in r.text
    assert "Inline Test Org" in r.text


def test_parent_get_returns_partial(client, org_id):
    r = client.get(f"/admin/orgs/{org_id}/inline/parent/", headers=HTMX_HEADERS)
    assert r.status_code == 200


def test_parent_post_sets_parent(client, org_id):
    # Create a second org to be the parent
    dsn = _dsn()
    parent_id = generate_id()

    async def make_parent():
        conn = await asyncpg.connect(dsn)
        await apply_schema(conn)
        try:
            await conn.execute("INSERT INTO organizations (id) VALUES ($1)", parent_id)
        finally:
            await conn.close()

    async def drop_parent():
        conn = await asyncpg.connect(dsn)
        try:
            await conn.execute("DELETE FROM organizations WHERE id=$1", parent_id)
        finally:
            await conn.close()

    asyncio.run(make_parent())
    try:
        r = client.post(
            f"/admin/orgs/{org_id}/inline/parent/",
            headers=HTMX_HEADERS,
            data={"parent_id": parent_id},
            follow_redirects=False,
        )
        assert r.status_code == 200
    finally:
        # Clear parent before dropping
        client.post(
            f"/admin/orgs/{org_id}/inline/parent/",
            headers=HTMX_HEADERS,
            data={"parent_id": ""},
        )
        asyncio.run(drop_parent())


def test_parent_post_circular_returns_422(client, org_id):
    r = client.post(
        f"/admin/orgs/{org_id}/inline/parent/",
        headers=HTMX_HEADERS,
        data={"parent_id": org_id},  # self-reference
        follow_redirects=False,
    )
    assert r.status_code == 422
