"""Integration tests for org hierarchy children CRUD."""

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
async def parent_and_child(db):
    pid, cid = generate_id(), generate_id()

    await db.execute("INSERT INTO organizations (id) VALUES ($1)", pid)
    await db.execute("INSERT INTO organizations (id) VALUES ($1)", cid)

    return pid, cid


async def test_child_form_row_has_form_group(client, parent_and_child):
    pid, _ = parent_and_child
    r = await client.get(f"/admin/orgs/{pid}/children/new-row/", headers=HTMX_HEADERS)
    assert r.status_code == 200
    assert "form-group" in r.text


async def test_add_child_sets_parent_id(client, parent_and_child, db):
    pid, cid = parent_and_child
    r = await client.post(
        f"/admin/orgs/{pid}/children/",
        headers=HTMX_HEADERS,
        data={"child_id": cid},
    )
    assert r.status_code == 200

    row = await db.fetchrow("SELECT parent_id FROM organizations WHERE id=$1", cid)
    assert row["parent_id"] == pid


async def test_remove_child_clears_parent_id(client, parent_and_child, db):
    pid, cid = parent_and_child
    # First set the parent
    await client.post(f"/admin/orgs/{pid}/children/", headers=HTMX_HEADERS, data={"child_id": cid})
    # Now unlink
    r = await client.delete(f"/admin/orgs/{pid}/children/{cid}/", headers=HTMX_HEADERS)
    assert r.status_code == 200

    row = await db.fetchrow("SELECT parent_id FROM organizations WHERE id=$1", cid)
    assert row["parent_id"] is None


async def test_circular_child_returns_422(client, parent_and_child):
    pid, _ = parent_and_child
    # Try to make pid its own child
    r = await client.post(
        f"/admin/orgs/{pid}/children/",
        headers=HTMX_HEADERS,
        data={"child_id": pid},
    )
    assert r.status_code == 422


async def test_add_child_returns_success_flash(client, parent_and_child):
    pid, cid = parent_and_child
    r = await client.post(
        f"/admin/orgs/{pid}/children/",
        headers=HTMX_HEADERS,
        data={"child_id": cid},
    )
    assert r.status_code == 200
    trigger = json.loads(r.headers["hx-trigger"])
    assert trigger["showFlash"]["level"] == "success"
    assert "linked as child" in trigger["showFlash"]["body"]


async def test_remove_child_returns_info_flash(client, parent_and_child):
    pid, cid = parent_and_child
    await client.post(f"/admin/orgs/{pid}/children/", headers=HTMX_HEADERS, data={"child_id": cid})
    r = await client.delete(f"/admin/orgs/{pid}/children/{cid}/", headers=HTMX_HEADERS)
    assert r.status_code == 200
    trigger = json.loads(r.headers["hx-trigger"])
    assert trigger["showFlash"]["level"] == "info"
