"""Integration tests for role_assignment identifiers CRUD (#326).

Only role_assignment (not role) carries identifiers — entity_identifier_types
seeds `role_wa_pdc` (public) and `pm_assignment_id` (internal) for it. The admin
picker excludes internal types, so only `role_wa_pdc` is offered.
"""

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
async def assignment_and_type(db):
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
    t = await db.fetchrow(
        "SELECT id FROM entity_identifier_types"
        " WHERE entity_type='role_assignment' AND slug='role_wa_pdc'"
    )
    return raid, t["id"]


async def test_new_row_offers_only_public_types(client, assignment_and_type):
    raid, _ = assignment_and_type
    r = await client.get(
        f"/admin/role-assignments/{raid}/identifiers/new-row/", headers=HTMX_HEADERS
    )
    assert r.status_code == 200
    assert "<form" in r.text
    assert "WA PDC" in r.text
    assert "PM Assignment" not in r.text  # internal type excluded


async def test_create_and_detail_renders(client, assignment_and_type):
    raid, type_id = assignment_and_type
    r = await client.post(
        f"/admin/role-assignments/{raid}/identifiers/",
        headers=HTMX_HEADERS,
        data={"entity_identifier_type_id": type_id, "value": "12345"},
    )
    assert r.status_code == 200
    assert "12345" in r.text

    d = await client.get(f"/admin/role-assignments/{raid}/", headers=AUTH_HEADERS)
    assert d.status_code == 200
    assert "12345" in d.text
    assert f"/admin/role-assignments/{raid}/identifiers/new-row/" in d.text


async def test_delete_unknown_404(client, assignment_and_type):
    raid, _ = assignment_and_type
    r = await client.delete(
        f"/admin/role-assignments/{raid}/identifiers/{generate_id()}/", headers=HTMX_HEADERS
    )
    assert r.status_code == 404
