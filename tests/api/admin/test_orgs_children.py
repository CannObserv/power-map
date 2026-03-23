"""Integration tests for org hierarchy children CRUD."""

import asyncio
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
def parent_and_child():
    dsn = _dsn()
    pid, cid = generate_id(), generate_id()

    async def setup():
        conn = await asyncpg.connect(dsn)
        await apply_schema(conn)
        try:
            await conn.execute("INSERT INTO organizations (id) VALUES ($1)", pid)
            await conn.execute("INSERT INTO organizations (id) VALUES ($1)", cid)
        finally:
            await conn.close()

    async def teardown():
        conn = await asyncpg.connect(dsn)
        try:
            await conn.execute(
                "UPDATE organizations SET parent_id=NULL WHERE parent_id=$1 OR id=$1", pid
            )
            await conn.execute("DELETE FROM organizations WHERE id=$1", cid)
            await conn.execute("DELETE FROM organizations WHERE id=$1", pid)
        finally:
            await conn.close()

    asyncio.run(setup())
    yield pid, cid
    asyncio.run(teardown())


def test_add_child_sets_parent_id(client, parent_and_child):
    pid, cid = parent_and_child
    r = client.post(
        f"/admin/orgs/{pid}/children/",
        headers=HTMX_HEADERS,
        data={"child_id": cid},
    )
    assert r.status_code == 200

    async def check():
        conn = await asyncpg.connect(_dsn())
        await apply_schema(conn)
        try:
            row = await conn.fetchrow("SELECT parent_id FROM organizations WHERE id=$1", cid)
            assert row["parent_id"] == pid
        finally:
            await conn.close()

    asyncio.run(check())


def test_remove_child_clears_parent_id(client, parent_and_child):
    pid, cid = parent_and_child
    # First set the parent
    client.post(f"/admin/orgs/{pid}/children/", headers=HTMX_HEADERS, data={"child_id": cid})
    # Now unlink
    r = client.delete(f"/admin/orgs/{pid}/children/{cid}/", headers=HTMX_HEADERS)
    assert r.status_code == 200

    async def check():
        conn = await asyncpg.connect(_dsn())
        await apply_schema(conn)
        try:
            row = await conn.fetchrow("SELECT parent_id FROM organizations WHERE id=$1", cid)
            assert row["parent_id"] is None
        finally:
            await conn.close()

    asyncio.run(check())


def test_circular_child_returns_422(client, parent_and_child):
    pid, _ = parent_and_child
    # Try to make pid its own child
    r = client.post(
        f"/admin/orgs/{pid}/children/",
        headers=HTMX_HEADERS,
        data={"child_id": pid},
    )
    assert r.status_code == 422
