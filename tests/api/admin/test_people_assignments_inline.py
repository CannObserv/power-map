"""Integration tests for inline assignment CRUD on person detail."""
import json
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
async def person_id(db):
    pid = generate_id()
    await db.execute("INSERT INTO people (id) VALUES ($1)", pid)
    await db.execute(
        "INSERT INTO person_names (id, person_id, name, is_canonical) VALUES ($1, $2, $3, TRUE)",
        generate_id(), pid, "Jane Doe",
    )
    return pid


@pytest.fixture
async def role_id(db):
    oid = generate_id()
    await db.execute("INSERT INTO organizations (id) VALUES ($1)", oid)
    await db.execute(
        "INSERT INTO organization_names (id, organization_id, name, is_canonical)"
        " VALUES ($1, $2, $3, TRUE)",
        generate_id(), oid, "Test Org",
    )
    rid = generate_id()
    await db.execute(
        "INSERT INTO roles (id, organization_id, title) VALUES ($1, $2, $3)",
        rid, oid, "Executive Director",
    )
    return rid


# ---------------------------------------------------------------------------
# New row form
# ---------------------------------------------------------------------------


async def test_new_row_returns_form(client, person_id):
    r = await client.get(
        f"/admin/people/{person_id}/assignments/new-row/", headers=HTMX_HEADERS
    )
    assert r.status_code == 200
    assert b"<form" in r.content
    assert b"role-search" in r.content


async def test_new_row_unknown_person_returns_404(client):
    r = await client.get(
        f"/admin/people/{generate_id()}/assignments/new-row/", headers=HTMX_HEADERS
    )
    assert r.status_code == 404


async def test_new_row_is_current_uses_pill_toggle(client, person_id):
    r = await client.get(
        f"/admin/people/{person_id}/assignments/new-row/", headers=HTMX_HEADERS
    )
    assert r.status_code == 200
    assert b'class="toggle"' in r.content
    assert b"toggle__track" in r.content


async def test_new_row_js_disables_end_date_when_is_current_checked(client, person_id):
    r = await client.get(
        f"/admin/people/{person_id}/assignments/new-row/", headers=HTMX_HEADERS
    )
    assert r.status_code == 200
    assert b"endDt.disabled = true" in r.content
    assert b"endDt.disabled = false" in r.content


# ---------------------------------------------------------------------------
# Create assignment
# ---------------------------------------------------------------------------


async def test_create_persists_assignment(client, person_id, role_id, db):
    r = await client.post(
        f"/admin/people/{person_id}/assignments/",
        headers=HTMX_HEADERS,
        data={
            "role_id": role_id,
            "start_date": "2024-01-15",
            "end_date": "",
            "is_current": "true",
        },
    )
    assert r.status_code == 200
    row = await db.fetchrow(
        "SELECT * FROM role_assignments WHERE person_id=$1 AND role_id=$2",
        person_id, role_id,
    )
    assert row is not None
    assert row["is_current"] is True
    assert str(row["start_date"]) == "2024-01-15"


async def test_create_with_end_date(client, person_id, role_id, db):
    r = await client.post(
        f"/admin/people/{person_id}/assignments/",
        headers=HTMX_HEADERS,
        data={"role_id": role_id, "start_date": "2020-01-01", "end_date": "2023-12-31"},
    )
    assert r.status_code == 200
    row = await db.fetchrow(
        "SELECT * FROM role_assignments WHERE person_id=$1 AND role_id=$2",
        person_id, role_id,
    )
    assert row is not None
    assert str(row["end_date"]) == "2023-12-31"


async def test_create_returns_tbody_with_org_and_role(client, person_id, role_id):
    r = await client.post(
        f"/admin/people/{person_id}/assignments/",
        headers=HTMX_HEADERS,
        data={"role_id": role_id, "start_date": "2024-01-01"},
    )
    assert r.status_code == 200
    assert b"Test Org" in r.content
    assert b"Executive Director" in r.content


async def test_create_returns_success_flash(client, person_id, role_id):
    r = await client.post(
        f"/admin/people/{person_id}/assignments/",
        headers=HTMX_HEADERS,
        data={"role_id": role_id, "start_date": "2024-01-01"},
    )
    trigger = json.loads(r.headers["hx-trigger"])
    assert trigger["showFlash"]["level"] == "success"


async def test_create_tbody_includes_edit_url(client, person_id, role_id):
    r = await client.post(
        f"/admin/people/{person_id}/assignments/",
        headers=HTMX_HEADERS,
        data={"role_id": role_id, "start_date": "2024-01-01"},
    )
    assert r.status_code == 200
    assert f"/admin/people/{person_id}/assignments/".encode() in r.content
    assert b"edit-row" in r.content


async def test_create_missing_role_returns_error(client, person_id):
    r = await client.post(
        f"/admin/people/{person_id}/assignments/",
        headers=HTMX_HEADERS,
        data={"role_id": "", "start_date": "2024-01-01"},
    )
    assert r.status_code == 200
    trigger = json.loads(r.headers["hx-trigger"])
    assert trigger["showFlash"]["level"] == "error"
    assert b"<form" in r.content


async def test_create_current_with_end_date_returns_error(client, person_id, role_id):
    r = await client.post(
        f"/admin/people/{person_id}/assignments/",
        headers=HTMX_HEADERS,
        data={
            "role_id": role_id,
            "start_date": "2024-01-01",
            "end_date": "2024-06-01",
            "is_current": "true",
        },
    )
    assert r.status_code == 200
    trigger = json.loads(r.headers["hx-trigger"])
    assert trigger["showFlash"]["level"] == "error"


async def test_create_duplicate_start_date_returns_error(client, person_id, role_id, db):
    """UniqueViolationError on (person_id, role_id, start_date) duplicate."""
    await db.execute(
        """INSERT INTO role_assignments (id, person_id, role_id, is_current, start_date)
           VALUES ($1, $2, $3, FALSE, '2024-01-01')""",
        generate_id(), person_id, role_id,
    )
    r = await client.post(
        f"/admin/people/{person_id}/assignments/",
        headers=HTMX_HEADERS,
        data={"role_id": role_id, "start_date": "2024-01-01"},
    )
    assert r.status_code == 200
    trigger = json.loads(r.headers["hx-trigger"])
    assert trigger["showFlash"]["level"] == "error"
    assert b"<form" in r.content


async def test_create_non_htmx_redirects(client, person_id, role_id):
    r = await client.post(
        f"/admin/people/{person_id}/assignments/",
        headers=AUTH_HEADERS,
        data={"role_id": role_id, "start_date": "2024-01-01"},
        follow_redirects=False,
    )
    assert r.status_code == 303
