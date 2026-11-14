"""Integration tests for role identifiers CRUD (#808).

A role definition carries identifiers since #808: entity_identifier_types seeds
`role_cannabis_observer` (public) and `pm_role_id` (internal) for it. The admin
picker excludes internal types, so only the public one is offered.
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
async def role_and_type(db):
    oid, rid = generate_id(), generate_id()
    await db.execute("INSERT INTO organizations (id) VALUES ($1)", oid)
    await db.execute(
        "INSERT INTO roles (id, organization_id, title) VALUES ($1, $2, 'Director')", rid, oid
    )
    type_id = await db.fetchval(
        "SELECT id FROM entity_identifier_types WHERE slug='role_cannabis_observer'"
    )
    return rid, type_id


async def test_new_row_offers_only_public_types(client, role_and_type):
    rid, _ = role_and_type
    r = await client.get(f"/admin/roles/{rid}/identifiers/new-row/", headers=HTMX_HEADERS)
    assert r.status_code == 200
    assert "<form" in r.text
    assert "Cannabis Observer" in r.text
    assert "PM Role" not in r.text  # internal type excluded
    assert "WA PDC" not in r.text  # role_wa_pdc keys an assignment, not a role


async def test_create_and_detail_renders(client, role_and_type):
    rid, type_id = role_and_type
    r = await client.post(
        f"/admin/roles/{rid}/identifiers/",
        headers=HTMX_HEADERS,
        data={"entity_identifier_type_id": type_id, "value": "01SITEROLEULID"},
    )
    assert r.status_code == 200
    assert "01SITEROLEULID" in r.text

    d = await client.get(f"/admin/roles/{rid}/", headers=AUTH_HEADERS)
    assert d.status_code == 200
    assert "01SITEROLEULID" in d.text
    assert f"/admin/roles/{rid}/identifiers/new-row/" in d.text


async def test_create_refuses_an_assignment_type(client, db, role_and_type):
    """#617: the picker is the only filter, so a crafted post of another entity type's slug 400s."""
    rid, _ = role_and_type
    ra_type = await db.fetchval("SELECT id FROM entity_identifier_types WHERE slug='role_wa_pdc'")
    r = await client.post(
        f"/admin/roles/{rid}/identifiers/",
        headers=HTMX_HEADERS,
        data={"entity_identifier_type_id": ra_type, "value": "123"},
    )
    assert r.status_code == 400


async def test_delete_unknown_404(client, role_and_type):
    rid, _ = role_and_type
    r = await client.delete(
        f"/admin/roles/{rid}/identifiers/{generate_id()}/", headers=HTMX_HEADERS
    )
    assert r.status_code == 404
