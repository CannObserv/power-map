"""Integration tests for org hierarchy children CRUD."""

import json

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient

from src.api.main import app
from src.core.db import generate_id

pytestmark = [
    pytest.mark.integration,
]
AUTH_HEADERS = {"X-ExeDev-UserID": "usr_test", "X-ExeDev-Email": "admin@test.com"}
HTMX_HEADERS = {**AUTH_HEADERS, "HX-Request": "true"}


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


@pytest_asyncio.fixture(loop_scope="session")
async def parent_and_child(db_pool):
    pid, cid = generate_id(), generate_id()

    async with db_pool.acquire() as conn:
        await conn.execute("INSERT INTO organizations (id) VALUES ($1)", pid)
        await conn.execute("INSERT INTO organizations (id) VALUES ($1)", cid)

    yield pid, cid

    async with db_pool.acquire() as conn:
        await conn.execute(
            "UPDATE organizations SET parent_id=NULL WHERE parent_id=$1 OR id=$1", pid
        )
        await conn.execute("DELETE FROM organizations WHERE id=$1", cid)
        await conn.execute("DELETE FROM organizations WHERE id=$1", pid)


async def test_child_form_row_has_form_group(client, parent_and_child):
    pid, _ = parent_and_child
    r = client.get(f"/admin/orgs/{pid}/children/new-row/", headers=HTMX_HEADERS)
    assert r.status_code == 200
    assert "form-group" in r.text


async def test_add_child_sets_parent_id(client, parent_and_child, db_pool):
    pid, cid = parent_and_child
    r = client.post(
        f"/admin/orgs/{pid}/children/",
        headers=HTMX_HEADERS,
        data={"child_id": cid},
    )
    assert r.status_code == 200

    async with db_pool.acquire() as conn:
        row = await conn.fetchrow("SELECT parent_id FROM organizations WHERE id=$1", cid)
    assert row["parent_id"] == pid


async def test_remove_child_clears_parent_id(client, parent_and_child, db_pool):
    pid, cid = parent_and_child
    # First set the parent
    client.post(f"/admin/orgs/{pid}/children/", headers=HTMX_HEADERS, data={"child_id": cid})
    # Now unlink
    r = client.delete(f"/admin/orgs/{pid}/children/{cid}/", headers=HTMX_HEADERS)
    assert r.status_code == 200

    async with db_pool.acquire() as conn:
        row = await conn.fetchrow("SELECT parent_id FROM organizations WHERE id=$1", cid)
    assert row["parent_id"] is None


async def test_circular_child_returns_422(client, parent_and_child):
    pid, _ = parent_and_child
    # Try to make pid its own child
    r = client.post(
        f"/admin/orgs/{pid}/children/",
        headers=HTMX_HEADERS,
        data={"child_id": pid},
    )
    assert r.status_code == 422


async def test_add_child_returns_success_flash(client, parent_and_child):
    pid, cid = parent_and_child
    r = client.post(
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
    client.post(f"/admin/orgs/{pid}/children/", headers=HTMX_HEADERS, data={"child_id": cid})
    r = client.delete(f"/admin/orgs/{pid}/children/{cid}/", headers=HTMX_HEADERS)
    assert r.status_code == 200
    trigger = json.loads(r.headers["hx-trigger"])
    assert trigger["showFlash"]["level"] == "info"
