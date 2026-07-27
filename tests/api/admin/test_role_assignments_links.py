"""Integration tests for role_assignment links CRUD (#326)."""

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from src.api.admin.deps import get_db
from src.api.main import app
from src.core.db import generate_id

pytestmark = [pytest.mark.integration]
AUTH_HEADERS = {"X-ExeDev-UserID": "usr_test", "X-ExeDev-Email": "admin@test.com"}
HTMX_HEADERS = {**AUTH_HEADERS, "HX-Request": "true"}


@pytest_asyncio.fixture(loop_scope="session")
async def db(db_pool):
    async with db_pool.acquire() as conn:
        tr = conn.transaction()
        await tr.start()
        try:
            yield conn
        finally:
            await tr.rollback()


@pytest_asyncio.fixture(loop_scope="session")
async def client(db):
    async def _get_db_override():
        yield db

    app.dependency_overrides[get_db] = _get_db_override
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test", follow_redirects=True
    ) as c:
        yield c
    app.dependency_overrides.pop(get_db, None)


@pytest_asyncio.fixture(loop_scope="session")
async def assignment_link_type(db):
    oid, rid, pid, raid = (generate_id() for _ in range(4))
    await db.execute("INSERT INTO organizations (id) VALUES ($1)", oid)
    await db.execute(
        "INSERT INTO roles (id, organization_id, title) VALUES ($1, $2, 'Director')", rid, oid
    )
    await db.execute("INSERT INTO people (id) VALUES ($1)", pid)
    await db.execute(
        "INSERT INTO role_assignments (id, person_id, role_id, is_current)"
        " VALUES ($1, $2, $3, TRUE)",
        raid,
        pid,
        rid,
    )
    lt = await db.fetchrow("SELECT id FROM link_types ORDER BY display_name LIMIT 1")
    return raid, lt["id"]


async def test_new_row_returns_form(client, assignment_link_type):
    raid, _ = assignment_link_type
    r = await client.get(f"/admin/role-assignments/{raid}/links/new-row/", headers=HTMX_HEADERS)
    assert r.status_code == 200
    assert "<form" in r.text


async def test_create_and_detail_renders(client, assignment_link_type):
    raid, lt_id = assignment_link_type
    r = await client.post(
        f"/admin/role-assignments/{raid}/links/",
        headers=HTMX_HEADERS,
        data={"url": "https://example.org/dir", "link_type_id": lt_id, "is_active": "true"},
    )
    assert r.status_code == 200
    assert "https://example.org/dir" in r.text

    d = await client.get(f"/admin/role-assignments/{raid}/", headers=AUTH_HEADERS)
    assert d.status_code == 200
    assert "https://example.org/dir" in d.text
    assert f"/admin/role-assignments/{raid}/links/new-row/" in d.text
