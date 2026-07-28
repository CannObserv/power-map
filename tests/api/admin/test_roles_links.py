"""Integration tests for role links CRUD (#326)."""

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
async def role_link_type(db):
    oid, rid = generate_id(), generate_id()
    await db.execute("INSERT INTO organizations (id) VALUES ($1)", oid)
    await db.execute(
        "INSERT INTO roles (id, organization_id, title) VALUES ($1, $2, 'Chair')", rid, oid
    )
    lt = await db.fetchrow("SELECT id FROM link_types ORDER BY display_name LIMIT 1")
    return rid, lt["id"]


async def test_new_row_returns_form(client, role_link_type):
    rid, _ = role_link_type
    r = await client.get(f"/admin/roles/{rid}/links/new-row/", headers=HTMX_HEADERS)
    assert r.status_code == 200
    assert "<form" in r.text


async def test_create_and_detail_renders(client, role_link_type):
    rid, lt_id = role_link_type
    r = await client.post(
        f"/admin/roles/{rid}/links/",
        headers=HTMX_HEADERS,
        data={"url": "https://example.org/chair", "link_type_id": lt_id, "is_active": "true"},
    )
    assert r.status_code == 200
    assert "https://example.org/chair" in r.text

    d = await client.get(f"/admin/roles/{rid}/", headers=AUTH_HEADERS)
    assert d.status_code == 200
    assert "https://example.org/chair" in d.text
    assert f"/admin/roles/{rid}/links/new-row/" in d.text


async def test_delete_unknown_404(client, role_link_type):
    rid, _ = role_link_type
    r = await client.delete(f"/admin/roles/{rid}/links/{generate_id()}/", headers=HTMX_HEADERS)
    assert r.status_code == 404
