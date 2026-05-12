"""Integration tests for people search typeahead."""

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from src.api.admin.deps import get_db
from src.api.main import app
from src.core.db import generate_id

pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
]

AUTH_HEADERS = {
    "X-ExeDev-UserID": "test-user",
    "X-ExeDev-Email": "test@example.com",
}
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
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    app.dependency_overrides.pop(get_db, None)


async def _make_person(db, name: str) -> str:
    pid = generate_id()
    await db.execute("INSERT INTO people (id) VALUES ($1)", pid)
    await db.execute(
        "INSERT INTO person_names (id, person_id, name, is_canonical) VALUES ($1, $2, $3, TRUE)",
        generate_id(),
        pid,
        name,
    )
    return pid


async def test_search_returns_matching_person(client, db):
    await _make_person(db, "Jane Doe")
    r = await client.get("/admin/people/search/", params={"q": "Jane"}, headers=HTMX_HEADERS)
    assert r.status_code == 200
    assert b"Jane" in r.content
    assert b"data-id" in r.content


async def test_search_empty_query_returns_empty(client, db):
    await _make_person(db, "Alice Smith")
    r = await client.get("/admin/people/search/", params={"q": ""}, headers=HTMX_HEADERS)
    assert r.status_code == 200
    assert b"data-id" not in r.content


async def test_search_no_match_returns_empty(client, db):
    r = await client.get(
        "/admin/people/search/", params={"q": "zzzznotfound"}, headers=HTMX_HEADERS
    )
    assert r.status_code == 200
    assert b"data-id" not in r.content
