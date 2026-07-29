"""Integration tests for admin citation CRUD (#319).

Exercises the shared factory (src/api/admin/_citations_shared.py) through the
person router; the other four entity routers are identical instantiations.
"""

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from src.api.admin.deps import get_db
from src.api.main import app
from src.core.db import generate_id

pytestmark = [pytest.mark.integration]

AUTH = {"X-ExeDev-UserID": "usr_test", "X-ExeDev-Email": "admin@test.com"}
HTMX = {**AUTH, "HX-Request": "true"}


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
async def person(db) -> str:
    pid = generate_id()
    await db.execute("INSERT INTO people (id) VALUES ($1)", pid)
    return pid


def _base(pid: str) -> str:
    return f"/admin/people/{pid}/citations"


async def test_create_renders_row(client, db, person):
    r = await client.post(
        f"{_base(person)}/",
        headers=HTMX,
        data={"field_name": "notes", "url": "https://s/a", "title": "Src A"},
    )
    assert r.status_code == 200, r.text
    assert "Src A" in r.text
    row = await db.fetchrow("SELECT * FROM citations WHERE entity_id=$1", person)
    assert row["url"] == "https://s/a"
    assert row["field_name"] == "notes"


async def test_create_unknown_field_rejected(client, db, person):
    r = await client.post(
        f"{_base(person)}/",
        headers=HTMX,
        data={"field_name": "bogus", "url": "https://s/a"},
    )
    assert r.status_code == 200
    assert "not a citable field" in r.text
    assert await db.fetchval("SELECT count(*) FROM citations WHERE entity_id=$1", person) == 0


async def test_create_requires_url_or_title(client, db, person):
    r = await client.post(f"{_base(person)}/", headers=HTMX, data={"field_name": "notes"})
    assert r.status_code == 200
    assert "at least a URL or a title" in r.text
    assert await db.fetchval("SELECT count(*) FROM citations WHERE entity_id=$1", person) == 0


async def test_duplicate_identity_flagged(client, db, person):
    await client.post(f"{_base(person)}/", headers=HTMX, data={"url": "https://s/dup"})
    r = await client.post(f"{_base(person)}/", headers=HTMX, data={"url": "https://s/dup"})
    assert "already exists" in r.text
    assert await db.fetchval("SELECT count(*) FROM citations WHERE entity_id=$1", person) == 1


async def test_edit_updates_payload(client, db, person):
    cid = generate_id()
    await db.execute(
        "INSERT INTO citations (id, entity_type, entity_id, url, title)"
        " VALUES ($1,'person',$2,'https://s/x','old')",
        cid,
        person,
    )
    r = await client.post(
        f"{_base(person)}/{cid}/edit-row/",
        headers=HTMX,
        data={"url": "https://s/x", "title": "new"},
    )
    assert r.status_code == 200
    assert (await db.fetchval("SELECT title FROM citations WHERE id=$1", cid)) == "new"


async def test_delete_removes_row(client, db, person):
    cid = generate_id()
    await db.execute(
        "INSERT INTO citations (id, entity_type, entity_id, url, title)"
        " VALUES ($1,'person',$2,'https://s/x','t')",
        cid,
        person,
    )
    r = await client.request("DELETE", f"{_base(person)}/{cid}/", headers=HTMX)
    assert r.status_code == 200
    assert await db.fetchval("SELECT count(*) FROM citations WHERE id=$1", cid) == 0


async def test_requires_admin_auth(client, db, person):
    # No auth headers → get_admin_user issues a 307 login redirect (don't follow
    # it to the login stub) and nothing is written.
    r = await client.post(f"{_base(person)}/", data={"url": "https://s/x"}, follow_redirects=False)
    assert r.status_code == 307
    assert await db.fetchval("SELECT count(*) FROM citations WHERE entity_id=$1", person) == 0


async def test_detail_page_shows_citations_panel(client, db, person):
    await db.execute(
        "INSERT INTO citations (id, entity_type, entity_id, url, title)"
        " VALUES ($1,'person',$2,'https://s/panel','Panel Src')",
        generate_id(),
        person,
    )
    r = await client.get(f"/admin/people/{person}/", headers=AUTH)
    assert r.status_code == 200
    assert "Citations" in r.text
    assert "Panel Src" in r.text
