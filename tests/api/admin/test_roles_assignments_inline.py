"""Integration tests for inline assignment create on role detail."""

import json
import os

import asyncpg
import pytest
from httpx import ASGITransport, AsyncClient

from src.api.admin.deps import get_db
from src.api.main import app
from src.core.db import apply_schema, generate_id

pytestmark = pytest.mark.integration

AUTH_HEADERS = {
    "X-ExeDev-UserID": "test-user",
    "X-ExeDev-Email": "test@example.com",
}
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
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c
    app.dependency_overrides.pop(get_db, None)


async def _make_org(db, name: str) -> str:
    oid = generate_id()
    await db.execute("INSERT INTO organizations (id) VALUES ($1)", oid)
    await db.execute(
        "INSERT INTO organization_names (id, organization_id, name, is_canonical)"
        " VALUES ($1, $2, $3, TRUE)",
        generate_id(), oid, name,
    )
    return oid


async def _make_person(db, name: str) -> str:
    pid = generate_id()
    await db.execute("INSERT INTO people (id) VALUES ($1)", pid)
    await db.execute(
        "INSERT INTO person_names (id, person_id, name, is_canonical)"
        " VALUES ($1, $2, $3, TRUE)",
        generate_id(), pid, name,
    )
    return pid


@pytest.fixture
async def role_id(db):
    oid = await _make_org(db, "Test Org")
    rid = generate_id()
    await db.execute(
        "INSERT INTO roles (id, organization_id, title) VALUES ($1, $2, $3)",
        rid, oid, "Executive Director",
    )
    return rid


@pytest.fixture
async def person_id(db):
    return await _make_person(db, "Jane Doe")


# ---------------------------------------------------------------------------
# New row form
# ---------------------------------------------------------------------------


async def test_new_row_returns_form(client, role_id):
    r = await client.get(
        f"/admin/roles/{role_id}/assignments/new-row/", headers=HTMX_HEADERS
    )
    assert r.status_code == 200
    assert b"<form" in r.content
    assert b"person-search" in r.content


async def test_new_row_unknown_role_returns_404(client):
    r = await client.get(
        f"/admin/roles/{generate_id()}/assignments/new-row/", headers=HTMX_HEADERS
    )
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# Create assignment
# ---------------------------------------------------------------------------


async def test_create_persists_assignment(client, role_id, person_id, db):
    r = await client.post(
        f"/admin/roles/{role_id}/assignments/",
        headers=HTMX_HEADERS,
        data={
            "person_id": person_id,
            "start_date": "2024-01-15",
            "end_date": "",
            "is_current": "true",
        },
    )
    assert r.status_code == 200
    row = await db.fetchrow(
        "SELECT * FROM role_assignments WHERE role_id=$1 AND person_id=$2",
        role_id, person_id,
    )
    assert row is not None
    assert row["is_current"] is True
    assert str(row["start_date"]) == "2024-01-15"
    assert row["end_date"] is None


async def test_create_with_end_date(client, role_id, person_id, db):
    r = await client.post(
        f"/admin/roles/{role_id}/assignments/",
        headers=HTMX_HEADERS,
        data={
            "person_id": person_id,
            "start_date": "2020-01-01",
            "end_date": "2023-12-31",
        },
    )
    assert r.status_code == 200
    row = await db.fetchrow(
        "SELECT * FROM role_assignments WHERE role_id=$1 AND person_id=$2",
        role_id, person_id,
    )
    assert row is not None
    assert row["is_current"] is False
    assert str(row["end_date"]) == "2023-12-31"


async def test_create_returns_tbody(client, role_id, person_id):
    r = await client.post(
        f"/admin/roles/{role_id}/assignments/",
        headers=HTMX_HEADERS,
        data={"person_id": person_id, "start_date": "2024-01-01"},
    )
    assert r.status_code == 200
    assert b"Jane" in r.content  # person name appears in tbody


async def test_create_returns_success_flash(client, role_id, person_id):
    r = await client.post(
        f"/admin/roles/{role_id}/assignments/",
        headers=HTMX_HEADERS,
        data={"person_id": person_id, "start_date": "2024-01-01"},
    )
    trigger = json.loads(r.headers["hx-trigger"])
    assert trigger["showFlash"]["level"] == "success"


async def test_create_missing_person_returns_error(client, role_id):
    r = await client.post(
        f"/admin/roles/{role_id}/assignments/",
        headers=HTMX_HEADERS,
        data={"person_id": "", "start_date": "2024-01-01"},
    )
    assert r.status_code == 200
    trigger = json.loads(r.headers["hx-trigger"])
    assert trigger["showFlash"]["level"] == "error"
    assert b"<form" in r.content


async def test_create_current_with_end_date_returns_error(client, role_id, person_id):
    """CHECK constraint: is_current=TRUE + end_date set should fail."""
    r = await client.post(
        f"/admin/roles/{role_id}/assignments/",
        headers=HTMX_HEADERS,
        data={
            "person_id": person_id,
            "start_date": "2024-01-01",
            "end_date": "2024-06-01",
            "is_current": "true",
        },
    )
    assert r.status_code == 200
    trigger = json.loads(r.headers["hx-trigger"])
    assert trigger["showFlash"]["level"] == "error"


async def test_create_non_htmx_redirects(client, role_id, person_id):
    r = await client.post(
        f"/admin/roles/{role_id}/assignments/",
        headers=AUTH_HEADERS,
        data={"person_id": person_id, "start_date": "2024-01-01"},
        follow_redirects=False,
    )
    assert r.status_code == 303
