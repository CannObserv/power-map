"""Integration tests for people detail inline routes: notes and pronouns."""

import os

import asyncpg
import pytest
from httpx import ASGITransport, AsyncClient

from src.api.admin.deps import get_db
from src.api.main import app
from src.core.db import apply_schema, generate_id

pytestmark = pytest.mark.integration

AUTH_HEADERS = {
    "X-ExeDev-UserID": "test-user",
    "X-ExeDev-Email": "test@example.com",
}
HTMX_HEADERS = {**AUTH_HEADERS, "HX-Request": "true"}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def db():
    """Live connection wrapped in a rolled-back transaction."""
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        pytest.skip("DATABASE_URL not set")
    conn = await asyncpg.connect(dsn)
    try:
        await apply_schema(conn)
        tr = conn.transaction()
        await tr.start()
        yield conn
        await tr.rollback()
    finally:
        await conn.close()


@pytest.fixture
async def person_id(db):
    """Insert a minimal person with a canonical name; return their id."""
    pid = generate_id()
    await db.execute("INSERT INTO people (id) VALUES ($1)", pid)
    await db.execute(
        "INSERT INTO person_names (id, person_id, name, is_canonical)"
        " VALUES ($1, $2, $3, TRUE)",
        generate_id(),
        pid,
        "Test Person",
    )
    return pid


@pytest.fixture
async def client(db):
    """AsyncClient with app, overriding get_db to use the test connection."""

    async def _get_db_override():
        yield db

    app.dependency_overrides[get_db] = _get_db_override
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c
    app.dependency_overrides.pop(get_db, None)


# ---------------------------------------------------------------------------
# Notes — archived guard
# ---------------------------------------------------------------------------


async def test_notes_read_shows_edit_when_not_archived(client, person_id):
    r = await client.get(
        f"/admin/people/{person_id}/inline/notes/", headers=HTMX_HEADERS
    )
    assert r.status_code == 200
    assert b"inline/notes/edit/" in r.content


async def test_notes_read_hides_edit_when_archived(client, person_id, db):
    await db.execute(
        "UPDATE people SET archived_at=NOW() WHERE id=$1", person_id
    )
    r = await client.get(
        f"/admin/people/{person_id}/inline/notes/", headers=HTMX_HEADERS
    )
    assert r.status_code == 200
    assert b"notes-field" in r.content
    assert b"inline/notes/edit/" not in r.content


# ---------------------------------------------------------------------------
# Pronouns — archived guard
# ---------------------------------------------------------------------------


async def test_pronouns_read_shows_edit_when_not_archived(client, person_id):
    r = await client.get(
        f"/admin/people/{person_id}/inline/pronouns/", headers=HTMX_HEADERS
    )
    assert r.status_code == 200
    assert b"inline/pronouns/edit/" in r.content


async def test_pronouns_read_hides_edit_when_archived(client, person_id, db):
    await db.execute(
        "UPDATE people SET archived_at=NOW() WHERE id=$1", person_id
    )
    r = await client.get(
        f"/admin/people/{person_id}/inline/pronouns/", headers=HTMX_HEADERS
    )
    assert r.status_code == 200
    assert b"pronouns-field" in r.content
    assert b"inline/pronouns/edit/" not in r.content
