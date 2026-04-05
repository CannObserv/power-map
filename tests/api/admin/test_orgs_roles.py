"""Integration tests for inline role create on org detail."""

import asyncio
import json
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
def org():
    dsn = _dsn()
    oid = generate_id()

    async def setup():
        conn = await asyncpg.connect(dsn)
        await apply_schema(conn)
        try:
            await conn.execute("INSERT INTO organizations (id) VALUES ($1)", oid)
            await conn.execute(
                "INSERT INTO organization_names (id, organization_id, name, is_canonical)"
                " VALUES ($1, $2, 'Test Org', TRUE)",
                generate_id(),
                oid,
            )
        finally:
            await conn.close()

    async def teardown():
        conn = await asyncpg.connect(dsn)
        try:
            await conn.execute("DELETE FROM roles WHERE organization_id=$1", oid)
            await conn.execute("DELETE FROM organization_names WHERE organization_id=$1", oid)
            await conn.execute("DELETE FROM organizations WHERE id=$1", oid)
        finally:
            await conn.close()

    asyncio.run(setup())
    yield oid
    asyncio.run(teardown())


def test_roles_new_row_returns_form(client, org):
    r = client.get(f"/admin/orgs/{org}/roles/new-row/", headers=HTMX_HEADERS)
    assert r.status_code == 200
    assert "<form" in r.text
    assert 'name="title"' in r.text


def test_roles_new_row_unknown_org_returns_404(client):
    r = client.get(f"/admin/orgs/{generate_id()}/roles/new-row/", headers=HTMX_HEADERS)
    assert r.status_code == 404


def test_roles_create_persists_role(client, org):
    dsn = _dsn()
    r = client.post(
        f"/admin/orgs/{org}/roles/",
        headers=HTMX_HEADERS,
        data={"title": "Executive Director"},
    )
    assert r.status_code == 200

    async def check():
        conn = await asyncpg.connect(dsn)
        try:
            return await conn.fetchrow(
                "SELECT id FROM roles WHERE organization_id=$1 AND lower(title)=lower($2)"
                " AND archived_at IS NULL",
                org,
                "Executive Director",
            )
        finally:
            await conn.close()

    assert asyncio.run(check()) is not None


def test_roles_create_returns_tbody_with_new_role(client, org):
    r = client.post(
        f"/admin/orgs/{org}/roles/",
        headers=HTMX_HEADERS,
        data={"title": "Policy Director"},
    )
    assert r.status_code == 200
    assert "Policy Director" in r.text
    assert "<table" not in r.text  # tbody only, not full table


def test_roles_create_returns_success_flash(client, org):
    r = client.post(
        f"/admin/orgs/{org}/roles/",
        headers=HTMX_HEADERS,
        data={"title": "Communications Director"},
    )
    assert r.status_code == 200
    trigger = json.loads(r.headers["hx-trigger"])
    assert trigger["showFlash"]["level"] == "success"
    assert "Communications Director" in trigger["showFlash"]["body"]


def test_roles_create_duplicate_returns_error_flash(client, org):
    dsn = _dsn()
    rid = generate_id()

    async def add_existing():
        conn = await asyncpg.connect(dsn)
        await apply_schema(conn)
        try:
            await conn.execute(
                "INSERT INTO roles (id, organization_id, title) VALUES ($1, $2, $3)",
                rid, org, "Finance Director",
            )
        finally:
            await conn.close()

    asyncio.run(add_existing())
    r = client.post(
        f"/admin/orgs/{org}/roles/",
        headers=HTMX_HEADERS,
        data={"title": "Finance Director"},
    )
    assert r.status_code == 200
    trigger = json.loads(r.headers["hx-trigger"])
    assert trigger["showFlash"]["level"] == "error"
    # Form must be returned (not the tbody) so user can correct input
    assert "<form" in r.text


def test_roles_create_duplicate_case_insensitive(client, org):
    """Unique index is case-insensitive — 'TITLE' and 'title' are duplicates."""
    dsn = _dsn()
    rid = generate_id()

    async def add_existing():
        conn = await asyncpg.connect(dsn)
        await apply_schema(conn)
        try:
            await conn.execute(
                "INSERT INTO roles (id, organization_id, title) VALUES ($1, $2, $3)",
                rid, org, "Legal Counsel",
            )
        finally:
            await conn.close()

    asyncio.run(add_existing())
    r = client.post(
        f"/admin/orgs/{org}/roles/",
        headers=HTMX_HEADERS,
        data={"title": "LEGAL COUNSEL"},
    )
    assert r.status_code == 200
    trigger = json.loads(r.headers["hx-trigger"])
    assert trigger["showFlash"]["level"] == "error"


def test_roles_create_non_htmx_redirects(client, org):
    r = client.post(
        f"/admin/orgs/{org}/roles/",
        headers=AUTH_HEADERS,
        data={"title": "Board Member"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"].startswith("/admin/orgs/")


def test_roles_create_unknown_org_returns_404(client):
    r = client.post(
        f"/admin/orgs/{generate_id()}/roles/",
        headers=HTMX_HEADERS,
        data={"title": "Some Role"},
    )
    assert r.status_code == 404
