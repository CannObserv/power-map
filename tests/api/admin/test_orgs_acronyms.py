"""Integration tests for organization acronym CRUD routes.

Run with:
    TEST_DATABASE_URL=postgres://... uv run pytest -m integration -v \
        tests/api/admin/test_orgs_acronyms.py
"""

import json
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
async def org_id(db):
    """Insert a minimal org with a canonical name; return its id."""
    oid = generate_id()
    await db.execute("INSERT INTO organizations (id) VALUES ($1)", oid)
    await db.execute(
        "INSERT INTO organization_names (id, organization_id, name, is_canonical)"
        " VALUES ($1, $2, $3, TRUE)",
        generate_id(),
        oid,
        "Test Org",
    )
    return oid


@pytest.fixture
async def client(db):
    """AsyncClient with the app, overriding get_db to use the test connection."""

    async def _get_db_override():
        yield db

    app.dependency_overrides[get_db] = _get_db_override
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c
    app.dependency_overrides.pop(get_db, None)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_acronym_new_row_returns_form(client, org_id):
    r = await client.get(
        f"/admin/orgs/{org_id}/acronyms/new-row/", headers=AUTH_HEADERS
    )
    assert r.status_code == 200
    assert b"acronym" in r.content.lower()


async def test_acronym_create(client, org_id, db):
    r = await client.post(
        f"/admin/orgs/{org_id}/acronyms/",
        data={"acronym": "TABC", "is_canonical": "true"},
        headers={**AUTH_HEADERS, "HX-Request": "true"},
    )
    assert r.status_code == 200
    assert b"TABC" in r.content
    row = await db.fetchrow(
        "SELECT * FROM organization_acronyms WHERE organization_id=$1", org_id
    )
    assert row is not None
    assert row["acronym"] == "TABC"
    assert row["is_canonical"] is True


async def test_acronym_read_row(client, org_id, db):
    aid = generate_id()
    await db.execute(
        "INSERT INTO organization_acronyms (id, organization_id, acronym, is_canonical)"
        " VALUES ($1, $2, $3, FALSE)",
        aid,
        org_id,
        "ALT",
    )
    r = await client.get(
        f"/admin/orgs/{org_id}/acronyms/{aid}/read-row/", headers=AUTH_HEADERS
    )
    assert r.status_code == 200
    assert b"ALT" in r.content


async def test_acronym_edit_row_get(client, org_id, db):
    aid = generate_id()
    await db.execute(
        "INSERT INTO organization_acronyms (id, organization_id, acronym, is_canonical)"
        " VALUES ($1, $2, $3, FALSE)",
        aid,
        org_id,
        "ALT",
    )
    r = await client.get(
        f"/admin/orgs/{org_id}/acronyms/{aid}/edit-row/", headers=AUTH_HEADERS
    )
    assert r.status_code == 200
    assert b"ALT" in r.content


async def test_acronym_edit_row_post(client, org_id, db):
    aid = generate_id()
    await db.execute(
        "INSERT INTO organization_acronyms (id, organization_id, acronym, is_canonical)"
        " VALUES ($1, $2, $3, FALSE)",
        aid,
        org_id,
        "OLD",
    )
    r = await client.post(
        f"/admin/orgs/{org_id}/acronyms/{aid}/edit-row/",
        data={"acronym": "NEW", "is_canonical": ""},
        headers={**AUTH_HEADERS, "HX-Request": "true"},
    )
    assert r.status_code == 200
    assert b"NEW" in r.content
    row = await db.fetchrow(
        "SELECT acronym FROM organization_acronyms WHERE id=$1", aid
    )
    assert row["acronym"] == "NEW"


async def test_acronym_delete(client, org_id, db):
    aid = generate_id()
    await db.execute(
        "INSERT INTO organization_acronyms (id, organization_id, acronym, is_canonical)"
        " VALUES ($1, $2, $3, FALSE)",
        aid,
        org_id,
        "DEL",
    )
    r = await client.delete(
        f"/admin/orgs/{org_id}/acronyms/{aid}/",
        headers={**AUTH_HEADERS, "HX-Request": "true"},
    )
    assert r.status_code == 200
    row = await db.fetchrow(
        "SELECT id FROM organization_acronyms WHERE id=$1", aid
    )
    assert row is None


async def test_acronym_404_on_unknown_org(client):
    r = await client.get(
        "/admin/orgs/NONEXISTENT/acronyms/new-row/", headers=AUTH_HEADERS
    )
    assert r.status_code == 404


async def test_acronym_create_demotes_existing_canonical(client, org_id, db):
    """Creating a canonical acronym must demote any existing canonical."""
    # Insert an existing canonical acronym
    existing_id = generate_id()
    await db.execute(
        "INSERT INTO organization_acronyms (id, organization_id, acronym, is_canonical)"
        " VALUES ($1, $2, 'OLD', TRUE)",
        existing_id,
        org_id,
    )
    r = await client.post(
        f"/admin/orgs/{org_id}/acronyms/",
        data={"acronym": "NEW", "is_canonical": "true"},
        headers={**AUTH_HEADERS, "HX-Request": "true"},
    )
    assert r.status_code == 200
    old = await db.fetchrow(
        "SELECT is_canonical FROM organization_acronyms WHERE id=$1", existing_id
    )
    assert old["is_canonical"] is False, "existing canonical must be demoted"


async def test_acronym_edit_promotes_and_demotes(client, org_id, db):
    """Editing a non-canonical acronym to canonical must demote the existing one."""
    canonical_id = generate_id()
    other_id = generate_id()
    await db.execute(
        "INSERT INTO organization_acronyms (id, organization_id, acronym, is_canonical)"
        " VALUES ($1, $2, 'CANON', TRUE)",
        canonical_id,
        org_id,
    )
    await db.execute(
        "INSERT INTO organization_acronyms (id, organization_id, acronym, is_canonical)"
        " VALUES ($1, $2, 'ALT', FALSE)",
        other_id,
        org_id,
    )
    r = await client.post(
        f"/admin/orgs/{org_id}/acronyms/{other_id}/edit-row/",
        data={"acronym": "ALT", "is_canonical": "true"},
        headers={**AUTH_HEADERS, "HX-Request": "true"},
    )
    assert r.status_code == 200
    old = await db.fetchrow(
        "SELECT is_canonical FROM organization_acronyms WHERE id=$1", canonical_id
    )
    promoted = await db.fetchrow(
        "SELECT is_canonical FROM organization_acronyms WHERE id=$1", other_id
    )
    assert old["is_canonical"] is False, "old canonical must be demoted"
    assert promoted["is_canonical"] is True, "edited row must be promoted"


async def test_acronym_edit_returns_tbody(client, org_id, db):
    """Edit response must return all rows (tbody innerHTML), not just the edited row."""
    aid1 = generate_id()
    aid2 = generate_id()
    await db.execute(
        "INSERT INTO organization_acronyms (id, organization_id, acronym, is_canonical)"
        " VALUES ($1, $2, 'FIRST', TRUE)",
        aid1,
        org_id,
    )
    await db.execute(
        "INSERT INTO organization_acronyms (id, organization_id, acronym, is_canonical)"
        " VALUES ($1, $2, 'SECOND', FALSE)",
        aid2,
        org_id,
    )
    r = await client.post(
        f"/admin/orgs/{org_id}/acronyms/{aid2}/edit-row/",
        data={"acronym": "EDITED", "is_canonical": ""},
        headers={**AUTH_HEADERS, "HX-Request": "true"},
    )
    assert r.status_code == 200
    # Both rows must appear — only a full tbody response includes both
    assert f'id="acronym-row-{aid1}"' in r.text
    assert f'id="acronym-row-{aid2}"' in r.text
    assert "<table" not in r.text  # tbody rows only


async def test_acronym_redirects_without_auth(client, org_id):
    r = await client.get(
        f"/admin/orgs/{org_id}/acronyms/new-row/", follow_redirects=False
    )
    assert r.status_code == 307


async def test_acronym_form_row_canonical_toggle_has_aria_label(client, org_id):
    r = await client.get(
        f"/admin/orgs/{org_id}/acronyms/new-row/", headers=AUTH_HEADERS
    )
    assert r.status_code == 200
    assert b'aria-label="Canonical"' in r.content


async def test_acronym_create_returns_success_flash(client, org_id):
    r = await client.post(
        f"/admin/orgs/{org_id}/acronyms/",
        data={"acronym": "FLASH", "is_canonical": ""},
        headers={**AUTH_HEADERS, "HX-Request": "true"},
    )
    assert r.status_code == 200
    trigger = json.loads(r.headers["hx-trigger"])
    assert trigger["showFlash"]["level"] == "success"


async def test_acronym_edit_returns_success_flash(client, org_id, db):
    aid = generate_id()
    await db.execute(
        "INSERT INTO organization_acronyms (id, organization_id, acronym, is_canonical)"
        " VALUES ($1, $2, 'OLD', FALSE)",
        aid,
        org_id,
    )
    r = await client.post(
        f"/admin/orgs/{org_id}/acronyms/{aid}/edit-row/",
        data={"acronym": "NEW", "is_canonical": ""},
        headers={**AUTH_HEADERS, "HX-Request": "true"},
    )
    assert r.status_code == 200
    trigger = json.loads(r.headers["hx-trigger"])
    assert trigger["showFlash"]["level"] == "success"


async def test_acronym_delete_returns_info_flash(client, org_id, db):
    aid = generate_id()
    await db.execute(
        "INSERT INTO organization_acronyms (id, organization_id, acronym, is_canonical)"
        " VALUES ($1, $2, 'DEL', FALSE)",
        aid,
        org_id,
    )
    r = await client.delete(
        f"/admin/orgs/{org_id}/acronyms/{aid}/",
        headers={**AUTH_HEADERS, "HX-Request": "true"},
    )
    assert r.status_code == 200
    trigger = json.loads(r.headers["hx-trigger"])
    assert trigger["showFlash"]["level"] == "info"
