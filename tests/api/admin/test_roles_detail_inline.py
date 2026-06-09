"""Integration tests for role detail inline editing routes."""

import datetime
import json

import asyncpg
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


async def _make_org(db, name: str) -> str:
    oid = generate_id()
    await db.execute("INSERT INTO organizations (id) VALUES ($1)", oid)
    await db.execute(
        "INSERT INTO organization_names (id, organization_id, name, is_canonical)"
        " VALUES ($1, $2, $3, TRUE)",
        generate_id(),
        oid,
        name,
    )
    return oid


@pytest_asyncio.fixture(loop_scope="session")
async def role_id(db):
    oid = await _make_org(db, "Test Org")
    rid = generate_id()
    await db.execute(
        "INSERT INTO roles (id, organization_id, title, notes) VALUES ($1, $2, $3, $4)",
        rid,
        oid,
        "Executive Director",
        "Some notes",
    )
    return rid


# ---------------------------------------------------------------------------
# Schema constraint: chk_role_date_order
# ---------------------------------------------------------------------------


async def test_chk_role_date_order_rejects_inverted_dates(db):
    """established_on > abolished_on must raise CheckViolationError."""
    oid = await _make_org(db, "Boundary Org")
    rid = generate_id()
    await db.execute(
        "INSERT INTO roles (id, organization_id, title) VALUES ($1, $2, $3)",
        rid,
        oid,
        "Test Role",
    )
    with pytest.raises(asyncpg.exceptions.CheckViolationError):
        await db.execute(
            "UPDATE roles SET established_on=$1, abolished_on=$2 WHERE id=$3",
            datetime.date(2020, 1, 1),
            datetime.date(2010, 1, 1),
            rid,
        )


async def test_chk_role_date_order_allows_same_date(db):
    """established_on == abolished_on is valid (single-day role)."""
    oid = await _make_org(db, "Same Day Org")
    rid = generate_id()
    await db.execute(
        "INSERT INTO roles (id, organization_id, title) VALUES ($1, $2, $3)",
        rid,
        oid,
        "One Day Role",
    )
    await db.execute(
        "UPDATE roles SET established_on=$1, abolished_on=$2 WHERE id=$3",
        datetime.date(2020, 6, 15),
        datetime.date(2020, 6, 15),
        rid,
    )
    row = await db.fetchrow("SELECT established_on, abolished_on FROM roles WHERE id=$1", rid)
    assert row["established_on"] == datetime.date(2020, 6, 15)
    assert row["abolished_on"] == datetime.date(2020, 6, 15)


# ---------------------------------------------------------------------------
# Org inline
# ---------------------------------------------------------------------------


async def test_org_read_returns_partial(client, role_id):
    r = await client.get(f"/admin/roles/{role_id}/inline/org/", headers=HTMX_HEADERS)
    assert r.status_code == 200
    assert b"org-field" in r.content


async def test_org_edit_returns_form(client, role_id):
    r = await client.get(f"/admin/roles/{role_id}/inline/org/edit/", headers=HTMX_HEADERS)
    assert r.status_code == 200
    assert b"org-search" in r.content


async def test_org_post_updates_org(client, role_id, db):
    new_org = await _make_org(db, "New Org")
    r = await client.post(
        f"/admin/roles/{role_id}/inline/org/",
        data={"organization_id": new_org},
        headers=HTMX_HEADERS,
    )
    assert r.status_code == 200
    row = await db.fetchrow("SELECT organization_id FROM roles WHERE id=$1", role_id)
    assert row["organization_id"] == new_org


async def test_org_post_returns_flash(client, role_id, db):
    new_org = await _make_org(db, "Flash Org")
    r = await client.post(
        f"/admin/roles/{role_id}/inline/org/",
        data={"organization_id": new_org},
        headers=HTMX_HEADERS,
    )
    trigger = json.loads(r.headers["hx-trigger"])
    assert trigger["showFlash"]["level"] == "success"


async def test_org_post_empty_returns_error(client, role_id):
    r = await client.post(
        f"/admin/roles/{role_id}/inline/org/",
        data={"organization_id": ""},
        headers=HTMX_HEADERS,
    )
    assert r.status_code == 200
    trigger = json.loads(r.headers["hx-trigger"])
    assert trigger["showFlash"]["level"] == "error"
    assert b"org-search" in r.content


# ---------------------------------------------------------------------------
# Title inline
# ---------------------------------------------------------------------------


async def test_title_read_returns_partial(client, role_id):
    r = await client.get(f"/admin/roles/{role_id}/inline/title/", headers=HTMX_HEADERS)
    assert r.status_code == 200
    assert b"title-field" in r.content


async def test_title_edit_returns_form(client, role_id):
    r = await client.get(f"/admin/roles/{role_id}/inline/title/edit/", headers=HTMX_HEADERS)
    assert r.status_code == 200
    assert b'name="title"' in r.content


async def test_title_post_updates_title(client, role_id, db):
    r = await client.post(
        f"/admin/roles/{role_id}/inline/title/",
        data={"title": "Chief of Staff"},
        headers=HTMX_HEADERS,
    )
    assert r.status_code == 200
    row = await db.fetchrow("SELECT title FROM roles WHERE id=$1", role_id)
    assert row["title"] == "Chief of Staff"


async def test_title_post_returns_flash(client, role_id):
    r = await client.post(
        f"/admin/roles/{role_id}/inline/title/",
        data={"title": "New Title"},
        headers=HTMX_HEADERS,
    )
    trigger = json.loads(r.headers["hx-trigger"])
    assert trigger["showFlash"]["level"] == "success"


async def test_title_post_empty_returns_error(client, role_id):
    r = await client.post(
        f"/admin/roles/{role_id}/inline/title/",
        data={"title": "   "},
        headers=HTMX_HEADERS,
    )
    assert r.status_code == 200
    trigger = json.loads(r.headers["hx-trigger"])
    assert trigger["showFlash"]["level"] == "error"
    assert b'name="title"' in r.content


# ---------------------------------------------------------------------------
# Notes inline
# ---------------------------------------------------------------------------


async def test_notes_read_returns_partial(client, role_id):
    r = await client.get(f"/admin/roles/{role_id}/inline/notes/", headers=HTMX_HEADERS)
    assert r.status_code == 200
    assert b"notes-field" in r.content


async def test_notes_edit_returns_form(client, role_id):
    r = await client.get(f"/admin/roles/{role_id}/inline/notes/edit/", headers=HTMX_HEADERS)
    assert r.status_code == 200
    assert b"notes-textarea" in r.content


async def test_notes_post_saves_value(client, role_id, db):
    r = await client.post(
        f"/admin/roles/{role_id}/inline/notes/",
        data={"notes": "Updated notes"},
        headers=HTMX_HEADERS,
    )
    assert r.status_code == 200
    row = await db.fetchrow("SELECT notes FROM roles WHERE id=$1", role_id)
    assert row["notes"] == "Updated notes"


async def test_notes_post_whitespace_to_null(client, role_id, db):
    r = await client.post(
        f"/admin/roles/{role_id}/inline/notes/",
        data={"notes": "   "},
        headers=HTMX_HEADERS,
    )
    assert r.status_code == 200
    row = await db.fetchrow("SELECT notes FROM roles WHERE id=$1", role_id)
    assert row["notes"] is None


async def test_notes_post_returns_read_partial(client, role_id):
    r = await client.post(
        f"/admin/roles/{role_id}/inline/notes/",
        data={"notes": "hello"},
        headers=HTMX_HEADERS,
    )
    assert r.status_code == 200
    assert b"notes-field" in r.content
    assert b"notes-textarea" not in r.content


async def test_notes_post_returns_flash(client, role_id):
    r = await client.post(
        f"/admin/roles/{role_id}/inline/notes/",
        data={"notes": "test"},
        headers=HTMX_HEADERS,
    )
    trigger = json.loads(r.headers["hx-trigger"])
    assert trigger["showFlash"]["level"] == "success"


# ---------------------------------------------------------------------------
# 404 handling
# ---------------------------------------------------------------------------


async def test_inline_routes_return_404_for_missing_role(client):
    fake_id = generate_id()
    for path in [
        f"/admin/roles/{fake_id}/inline/org/",
        f"/admin/roles/{fake_id}/inline/title/",
        f"/admin/roles/{fake_id}/inline/notes/",
        f"/admin/roles/{fake_id}/inline/dates/",
        f"/admin/roles/{fake_id}/inline/dates/edit/",
    ]:
        r = await client.get(path, headers=HTMX_HEADERS)
        assert r.status_code == 404, f"Expected 404 for {path}"


# ---------------------------------------------------------------------------
# Dates inline
# ---------------------------------------------------------------------------


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


async def test_dates_read_returns_partial(client, role_id):
    r = await client.get(f"/admin/roles/{role_id}/inline/dates/", headers=HTMX_HEADERS)
    assert r.status_code == 200
    assert b"dates-field" in r.content


async def test_dates_edit_returns_form(client, role_id):
    r = await client.get(f"/admin/roles/{role_id}/inline/dates/edit/", headers=HTMX_HEADERS)
    assert r.status_code == 200
    assert b"established_on" in r.content
    assert b"abolished_on" in r.content


async def test_dates_post_saves_both_dates(client, role_id, db):
    r = await client.post(
        f"/admin/roles/{role_id}/inline/dates/",
        data={"established_on": "2010-01-01", "abolished_on": "2020-12-31"},
        headers=HTMX_HEADERS,
    )
    assert r.status_code == 200
    row = await db.fetchrow("SELECT established_on, abolished_on FROM roles WHERE id=$1", role_id)
    assert str(row["established_on"]) == "2010-01-01"
    assert str(row["abolished_on"]) == "2020-12-31"


async def test_dates_post_clears_dates(client, role_id, db):
    await db.execute(
        "UPDATE roles SET established_on=$1, abolished_on=$2 WHERE id=$3",
        datetime.date(2010, 1, 1),
        datetime.date(2020, 12, 31),
        role_id,
    )
    r = await client.post(
        f"/admin/roles/{role_id}/inline/dates/",
        data={"established_on": "", "abolished_on": ""},
        headers=HTMX_HEADERS,
    )
    assert r.status_code == 200
    row = await db.fetchrow("SELECT established_on, abolished_on FROM roles WHERE id=$1", role_id)
    assert row["established_on"] is None
    assert row["abolished_on"] is None


async def test_dates_post_rejects_inverted_order(client, role_id):
    r = await client.post(
        f"/admin/roles/{role_id}/inline/dates/",
        data={"established_on": "2020-01-01", "abolished_on": "2010-01-01"},
        headers=HTMX_HEADERS,
    )
    assert r.status_code == 200
    trigger = json.loads(r.headers["hx-trigger"])
    assert trigger["showFlash"]["level"] == "error"
    assert b"established_on" in r.content  # form re-rendered


async def test_dates_post_rejects_when_assignments_outside_bounds(client, role_id, db):
    """Saving bounds that would exclude an existing assignment must fail."""
    # Create a person and assignment with start_date in 2005
    pid = await _make_person(db, "Early Bird")
    await db.execute(
        "INSERT INTO role_assignments (id, person_id, role_id, is_current, start_date)"
        " VALUES ($1, $2, $3, FALSE, $4)",
        generate_id(),
        pid,
        role_id,
        datetime.date(2005, 3, 1),
    )
    r = await client.post(
        f"/admin/roles/{role_id}/inline/dates/",
        data={"established_on": "2010-01-01", "abolished_on": ""},
        headers=HTMX_HEADERS,
    )
    assert r.status_code == 200
    trigger = json.loads(r.headers["hx-trigger"])
    assert trigger["showFlash"]["level"] == "error"
    assert b"established_on" in r.content  # form re-rendered


async def test_dates_post_returns_success_flash(client, role_id):
    r = await client.post(
        f"/admin/roles/{role_id}/inline/dates/",
        data={"established_on": "2010-01-01", "abolished_on": ""},
        headers=HTMX_HEADERS,
    )
    assert r.status_code == 200
    trigger = json.loads(r.headers["hx-trigger"])
    assert trigger["showFlash"]["level"] == "success"
