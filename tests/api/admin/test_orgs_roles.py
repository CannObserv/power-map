"""Integration tests for inline role create on org detail."""

import json

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from src.api.admin.deps import get_db
from src.api.main import app
from src.core.db import generate_id

pytestmark = [
    pytest.mark.integration,
]
AUTH_HEADERS = {"X-ExeDev-UserID": "usr_test", "X-ExeDev-Email": "admin@test.com"}
HTMX_HEADERS = {**AUTH_HEADERS, "HX-Request": "true"}


@pytest_asyncio.fixture(loop_scope="session")
async def db(db_pool):
    """Pool-acquired connection wrapped in a rolled-back transaction."""
    async with db_pool.acquire() as conn:
        tr = conn.transaction()
        await tr.start()
        try:
            yield conn
        finally:
            await tr.rollback()


@pytest_asyncio.fixture(loop_scope="session")
async def client(db):
    """AsyncClient with app, overriding get_db to use the test connection."""

    async def _get_db_override():
        yield db

    app.dependency_overrides[get_db] = _get_db_override
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test", follow_redirects=True
    ) as c:
        yield c
    app.dependency_overrides.pop(get_db, None)


@pytest_asyncio.fixture(loop_scope="session")
async def org(db):
    oid = generate_id()
    await db.execute("INSERT INTO organizations (id) VALUES ($1)", oid)
    await db.execute(
        "INSERT INTO organization_names (id, organization_id, name, is_canonical)"
        " VALUES ($1, $2, 'Test Org', TRUE)",
        generate_id(),
        oid,
    )
    return oid


async def test_roles_new_row_returns_form(client, org):
    r = await client.get(f"/admin/orgs/{org}/roles/new-row/", headers=HTMX_HEADERS)
    assert r.status_code == 200
    assert "<form" in r.text
    assert 'name="title"' in r.text


async def test_roles_new_row_unknown_org_returns_404(client):
    r = await client.get(f"/admin/orgs/{generate_id()}/roles/new-row/", headers=HTMX_HEADERS)
    assert r.status_code == 404


async def test_roles_create_persists_role(client, org, db):
    r = await client.post(
        f"/admin/orgs/{org}/roles/",
        headers=HTMX_HEADERS,
        data={"title": "Executive Director"},
    )
    assert r.status_code == 200

    row = await db.fetchrow(
        "SELECT id FROM roles WHERE organization_id=$1 AND lower(title)=lower($2)"
        " AND archived_at IS NULL",
        org,
        "Executive Director",
    )
    assert row is not None


async def test_roles_create_returns_tbody_with_new_role(client, org):
    r = await client.post(
        f"/admin/orgs/{org}/roles/",
        headers=HTMX_HEADERS,
        data={"title": "Policy Director"},
    )
    assert r.status_code == 200
    assert "Policy Director" in r.text
    assert "<table" not in r.text  # tbody only, not full table


async def test_roles_create_returns_success_flash(client, org):
    r = await client.post(
        f"/admin/orgs/{org}/roles/",
        headers=HTMX_HEADERS,
        data={"title": "Communications Director"},
    )
    assert r.status_code == 200
    trigger = json.loads(r.headers["hx-trigger"])
    assert trigger["showFlash"]["level"] == "success"
    assert "Communications Director" in trigger["showFlash"]["body"]


async def test_roles_create_duplicate_returns_error_flash(client, org, db):
    rid = generate_id()
    await db.execute(
        "INSERT INTO roles (id, organization_id, title) VALUES ($1, $2, $3)",
        rid,
        org,
        "Finance Director",
    )

    r = await client.post(
        f"/admin/orgs/{org}/roles/",
        headers=HTMX_HEADERS,
        data={"title": "Finance Director"},
    )
    assert r.status_code == 200
    trigger = json.loads(r.headers["hx-trigger"])
    assert trigger["showFlash"]["level"] == "error"
    # Form must be returned (not the tbody) so user can correct input
    assert "<form" in r.text


async def test_roles_create_duplicate_case_insensitive(client, org, db):
    """Unique index is case-insensitive — 'TITLE' and 'title' are duplicates."""
    rid = generate_id()
    await db.execute(
        "INSERT INTO roles (id, organization_id, title) VALUES ($1, $2, $3)",
        rid,
        org,
        "Legal Counsel",
    )

    r = await client.post(
        f"/admin/orgs/{org}/roles/",
        headers=HTMX_HEADERS,
        data={"title": "LEGAL COUNSEL"},
    )
    assert r.status_code == 200
    trigger = json.loads(r.headers["hx-trigger"])
    assert trigger["showFlash"]["level"] == "error"


async def test_roles_create_non_htmx_redirects(client, org):
    r = await client.post(
        f"/admin/orgs/{org}/roles/",
        headers=AUTH_HEADERS,
        data={"title": "Board Member"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"].startswith("/admin/orgs/")


async def test_roles_create_unknown_org_returns_404(client):
    r = await client.post(
        f"/admin/orgs/{generate_id()}/roles/",
        headers=HTMX_HEADERS,
        data={"title": "Some Role"},
    )
    assert r.status_code == 404


async def test_roles_create_empty_title_returns_error_flash(client, org):
    """Whitespace-only title is rejected server-side."""
    r = await client.post(
        f"/admin/orgs/{org}/roles/",
        headers=HTMX_HEADERS,
        data={"title": "   "},
    )
    assert r.status_code == 200
    trigger = json.loads(r.headers["hx-trigger"])
    assert trigger["showFlash"]["level"] == "error"
    assert "<form" in r.text


async def test_roles_create_empty_title_non_htmx_redirects(client, org):
    r = await client.post(
        f"/admin/orgs/{org}/roles/",
        headers=AUTH_HEADERS,
        data={"title": "   "},
        follow_redirects=False,
    )
    assert r.status_code == 303
