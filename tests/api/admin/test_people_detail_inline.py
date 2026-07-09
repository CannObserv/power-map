"""Integration tests for people detail inline routes: notes and pronouns."""

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from src.api.admin.deps import get_db
from src.api.main import app
from src.core.db import generate_id

pytestmark = [
    pytest.mark.integration,
]

AUTH_HEADERS = {
    "X-ExeDev-UserID": "test-user",
    "X-ExeDev-Email": "test@example.com",
}
HTMX_HEADERS = {**AUTH_HEADERS, "HX-Request": "true"}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


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
async def person_id(db):
    """Insert a minimal person with a canonical name; return their id."""
    pid = generate_id()
    await db.execute("INSERT INTO people (id) VALUES ($1)", pid)
    await db.execute(
        "INSERT INTO person_names (id, person_id, name, is_canonical) VALUES ($1, $2, $3, TRUE)",
        generate_id(),
        pid,
        "Test Person",
    )
    return pid


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


# ---------------------------------------------------------------------------
# Notes — archived guard
# ---------------------------------------------------------------------------


async def test_notes_read_shows_edit_when_not_archived(client, person_id):
    r = await client.get(f"/admin/people/{person_id}/inline/notes/", headers=HTMX_HEADERS)
    assert r.status_code == 200
    assert b"inline/notes/edit/" in r.content


async def test_notes_read_hides_edit_when_archived(client, person_id, db):
    await db.execute("UPDATE people SET archived_at=NOW() WHERE id=$1", person_id)
    r = await client.get(f"/admin/people/{person_id}/inline/notes/", headers=HTMX_HEADERS)
    assert r.status_code == 200
    assert b"notes-field" in r.content
    assert b"inline/notes/edit/" not in r.content


# ---------------------------------------------------------------------------
# Pronouns — archived guard
# ---------------------------------------------------------------------------


async def test_pronouns_read_shows_edit_when_not_archived(client, person_id):
    r = await client.get(f"/admin/people/{person_id}/inline/pronouns/", headers=HTMX_HEADERS)
    assert r.status_code == 200
    assert b"inline/pronouns/edit/" in r.content


async def test_pronouns_read_hides_edit_when_archived(client, person_id, db):
    await db.execute("UPDATE people SET archived_at=NOW() WHERE id=$1", person_id)
    r = await client.get(f"/admin/people/{person_id}/inline/pronouns/", headers=HTMX_HEADERS)
    assert r.status_code == 200
    assert b"pronouns-field" in r.content
    assert b"inline/pronouns/edit/" not in r.content
