"""Integration tests for inline assignment create on role detail."""

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
HTMX_HEADERS = {**AUTH_HEADERS, "HX-Request": "true"}


@pytest.fixture
async def db():
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
async def client(db):
    async def _get_db_override():
        yield db

    app.dependency_overrides[get_db] = _get_db_override
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c
    app.dependency_overrides.pop(get_db, None)


async def _make_org(db, name: str) -> str:
    oid = generate_id()
    await db.execute("INSERT INTO organizations (id) VALUES ($1)", oid)
    await db.execute(
        "INSERT INTO organization_names (id, organization_id, name, is_canonical)"
        " VALUES ($1, $2, $3, TRUE)",
        generate_id(), oid, name,
    )
    return oid


async def _make_person(db, name: str) -> str:
    pid = generate_id()
    await db.execute("INSERT INTO people (id) VALUES ($1)", pid)
    await db.execute(
        "INSERT INTO person_names (id, person_id, name, is_canonical)"
        " VALUES ($1, $2, $3, TRUE)",
        generate_id(), pid, name,
    )
    return pid


@pytest.fixture
async def role_id(db):
    oid = await _make_org(db, "Test Org")
    rid = generate_id()
    await db.execute(
        "INSERT INTO roles (id, organization_id, title) VALUES ($1, $2, $3)",
        rid, oid, "Executive Director",
    )
    return rid


@pytest.fixture
async def person_id(db):
    return await _make_person(db, "Jane Doe")


# ---------------------------------------------------------------------------
# New row form
# ---------------------------------------------------------------------------


async def test_new_row_returns_form(client, role_id):
    r = await client.get(
        f"/admin/roles/{role_id}/assignments/new-row/", headers=HTMX_HEADERS
    )
    assert r.status_code == 200
    assert b"<form" in r.content
    assert b"person-search" in r.content


async def test_new_row_unknown_role_returns_404(client):
    r = await client.get(
        f"/admin/roles/{generate_id()}/assignments/new-row/", headers=HTMX_HEADERS
    )
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# Create assignment
# ---------------------------------------------------------------------------


async def test_create_persists_assignment(client, role_id, person_id, db):
    r = await client.post(
        f"/admin/roles/{role_id}/assignments/",
        headers=HTMX_HEADERS,
        data={
            "person_id": person_id,
            "start_date": "2024-01-15",
            "end_date": "",
            "is_current": "true",
        },
    )
    assert r.status_code == 200
    row = await db.fetchrow(
        "SELECT * FROM role_assignments WHERE role_id=$1 AND person_id=$2",
        role_id, person_id,
    )
    assert row is not None
    assert row["is_current"] is True
    assert str(row["start_date"]) == "2024-01-15"
    assert row["end_date"] is None


async def test_create_with_end_date(client, role_id, person_id, db):
    r = await client.post(
        f"/admin/roles/{role_id}/assignments/",
        headers=HTMX_HEADERS,
        data={
            "person_id": person_id,
            "start_date": "2020-01-01",
            "end_date": "2023-12-31",
        },
    )
    assert r.status_code == 200
    row = await db.fetchrow(
        "SELECT * FROM role_assignments WHERE role_id=$1 AND person_id=$2",
        role_id, person_id,
    )
    assert row is not None
    assert row["is_current"] is False
    assert str(row["end_date"]) == "2023-12-31"


async def test_create_returns_tbody(client, role_id, person_id):
    r = await client.post(
        f"/admin/roles/{role_id}/assignments/",
        headers=HTMX_HEADERS,
        data={"person_id": person_id, "start_date": "2024-01-01"},
    )
    assert r.status_code == 200
    assert b"Jane" in r.content  # person name appears in tbody


async def test_create_returns_success_flash(client, role_id, person_id):
    r = await client.post(
        f"/admin/roles/{role_id}/assignments/",
        headers=HTMX_HEADERS,
        data={"person_id": person_id, "start_date": "2024-01-01"},
    )
    trigger = json.loads(r.headers["hx-trigger"])
    assert trigger["showFlash"]["level"] == "success"


async def test_create_missing_person_returns_error(client, role_id):
    r = await client.post(
        f"/admin/roles/{role_id}/assignments/",
        headers=HTMX_HEADERS,
        data={"person_id": "", "start_date": "2024-01-01"},
    )
    assert r.status_code == 200
    trigger = json.loads(r.headers["hx-trigger"])
    assert trigger["showFlash"]["level"] == "error"
    assert b"<form" in r.content


async def test_create_current_with_end_date_returns_error(client, role_id, person_id):
    """CHECK constraint: is_current=TRUE + end_date set should fail."""
    r = await client.post(
        f"/admin/roles/{role_id}/assignments/",
        headers=HTMX_HEADERS,
        data={
            "person_id": person_id,
            "start_date": "2024-01-01",
            "end_date": "2024-06-01",
            "is_current": "true",
        },
    )
    assert r.status_code == 200
    trigger = json.loads(r.headers["hx-trigger"])
    assert trigger["showFlash"]["level"] == "error"


async def test_create_non_htmx_redirects(client, role_id, person_id):
    r = await client.post(
        f"/admin/roles/{role_id}/assignments/",
        headers=AUTH_HEADERS,
        data={"person_id": person_id, "start_date": "2024-01-01"},
        follow_redirects=False,
    )
    assert r.status_code == 303


async def test_create_tbody_includes_edit_url(client, role_id, person_id):
    """_assignment_rows.html must have role_id in context so edit-row URLs render."""
    r = await client.post(
        f"/admin/roles/{role_id}/assignments/",
        headers=HTMX_HEADERS,
        data={"person_id": person_id, "start_date": "2024-01-01"},
    )
    assert r.status_code == 200
    assert f"/admin/roles/{role_id}/assignments/".encode() in r.content
    assert b"edit-row" in r.content


# ---------------------------------------------------------------------------
# Fixtures — existing assignment
# ---------------------------------------------------------------------------


@pytest.fixture
async def assignment_id(db, role_id, person_id):
    ra_id = generate_id()
    await db.execute(
        """INSERT INTO role_assignments (id, person_id, role_id, is_current, start_date, end_date)
           VALUES ($1, $2, $3, FALSE, '2020-01-01', '2022-12-31')""",
        ra_id, person_id, role_id,
    )
    return ra_id


# ---------------------------------------------------------------------------
# Read row
# ---------------------------------------------------------------------------


async def test_read_row_returns_person_name(client, role_id, assignment_id):
    r = await client.get(
        f"/admin/roles/{role_id}/assignments/{assignment_id}/read-row/",
        headers=HTMX_HEADERS,
    )
    assert r.status_code == 200
    assert b"Jane" in r.content


async def test_read_row_returns_dates(client, role_id, assignment_id):
    r = await client.get(
        f"/admin/roles/{role_id}/assignments/{assignment_id}/read-row/",
        headers=HTMX_HEADERS,
    )
    assert r.status_code == 200
    assert b"2020-01-01" in r.content
    assert b"2022-12-31" in r.content


async def test_read_row_contains_edit_button(client, role_id, assignment_id):
    r = await client.get(
        f"/admin/roles/{role_id}/assignments/{assignment_id}/read-row/",
        headers=HTMX_HEADERS,
    )
    assert r.status_code == 200
    assert b"edit-row" in r.content


async def test_read_row_archived_returns_200(client, role_id, archived_assignment_id):
    r = await client.get(
        f"/admin/roles/{role_id}/assignments/{archived_assignment_id}/read-row/",
        headers=HTMX_HEADERS,
    )
    assert r.status_code == 200
    assert b"Jane" in r.content


async def test_read_row_archived_has_no_edit_button(client, role_id, archived_assignment_id):
    r = await client.get(
        f"/admin/roles/{role_id}/assignments/{archived_assignment_id}/read-row/",
        headers=HTMX_HEADERS,
    )
    assert r.status_code == 200
    assert b"edit-row" not in r.content


async def test_read_row_unknown_returns_404(client, role_id):
    r = await client.get(
        f"/admin/roles/{role_id}/assignments/{generate_id()}/read-row/",
        headers=HTMX_HEADERS,
    )
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# Edit row GET
# ---------------------------------------------------------------------------


async def test_edit_row_get_returns_form(client, role_id, assignment_id):
    r = await client.get(
        f"/admin/roles/{role_id}/assignments/{assignment_id}/edit-row/",
        headers=HTMX_HEADERS,
    )
    assert r.status_code == 200
    assert b'name="start_date"' in r.content
    assert b'name="end_date"' in r.content
    assert b'name="is_current"' in r.content


async def test_edit_row_get_uses_column_cells(client, role_id, assignment_id):
    """Edit row must use individual <td> cells, not a single colspan, so controls
    align with Person / Start / End / Status / Actions column headers."""
    r = await client.get(
        f"/admin/roles/{role_id}/assignments/{assignment_id}/edit-row/",
        headers=HTMX_HEADERS,
    )
    assert r.status_code == 200
    assert b"colspan" not in r.content
    assert r.content.count(b"<td") == 5


async def test_edit_row_get_prepopulates_dates(client, role_id, assignment_id):
    r = await client.get(
        f"/admin/roles/{role_id}/assignments/{assignment_id}/edit-row/",
        headers=HTMX_HEADERS,
    )
    assert r.status_code == 200
    assert b"2020-01-01" in r.content
    assert b"2022-12-31" in r.content


async def test_edit_row_get_shows_person_name(client, role_id, assignment_id):
    r = await client.get(
        f"/admin/roles/{role_id}/assignments/{assignment_id}/edit-row/",
        headers=HTMX_HEADERS,
    )
    assert r.status_code == 200
    assert b"Jane" in r.content


async def test_edit_row_get_unknown_returns_404(client, role_id):
    r = await client.get(
        f"/admin/roles/{role_id}/assignments/{generate_id()}/edit-row/",
        headers=HTMX_HEADERS,
    )
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# Edit row POST — success
# ---------------------------------------------------------------------------


async def test_edit_row_post_updates_start_date(client, role_id, assignment_id, db):
    r = await client.post(
        f"/admin/roles/{role_id}/assignments/{assignment_id}/edit-row/",
        headers=HTMX_HEADERS,
        data={"start_date": "2021-03-01", "end_date": "2022-12-31"},
    )
    assert r.status_code == 200
    row = await db.fetchrow(
        "SELECT start_date FROM role_assignments WHERE id=$1", assignment_id
    )
    assert str(row["start_date"]) == "2021-03-01"


async def test_edit_row_post_updates_end_date(client, role_id, assignment_id, db):
    r = await client.post(
        f"/admin/roles/{role_id}/assignments/{assignment_id}/edit-row/",
        headers=HTMX_HEADERS,
        data={"start_date": "2020-01-01", "end_date": "2023-06-30"},
    )
    assert r.status_code == 200
    row = await db.fetchrow(
        "SELECT end_date FROM role_assignments WHERE id=$1", assignment_id
    )
    assert str(row["end_date"]) == "2023-06-30"


async def test_edit_row_post_sets_is_current(client, role_id, assignment_id, db):
    r = await client.post(
        f"/admin/roles/{role_id}/assignments/{assignment_id}/edit-row/",
        headers=HTMX_HEADERS,
        data={"start_date": "2020-01-01", "end_date": "", "is_current": "true"},
    )
    assert r.status_code == 200
    row = await db.fetchrow(
        "SELECT is_current, end_date FROM role_assignments WHERE id=$1", assignment_id
    )
    assert row["is_current"] is True
    assert row["end_date"] is None


async def test_edit_row_post_returns_all_rows(client, role_id, assignment_id):
    r = await client.post(
        f"/admin/roles/{role_id}/assignments/{assignment_id}/edit-row/",
        headers=HTMX_HEADERS,
        data={"start_date": "2020-01-01", "end_date": "2022-12-31"},
    )
    assert r.status_code == 200
    assert b"Jane" in r.content


async def test_edit_row_post_returns_success_flash(client, role_id, assignment_id):
    r = await client.post(
        f"/admin/roles/{role_id}/assignments/{assignment_id}/edit-row/",
        headers=HTMX_HEADERS,
        data={"start_date": "2020-01-01", "end_date": "2022-12-31"},
    )
    trigger = json.loads(r.headers["hx-trigger"])
    assert trigger["showFlash"]["level"] == "success"


# ---------------------------------------------------------------------------
# Edit row POST — errors
# ---------------------------------------------------------------------------


async def test_edit_row_post_current_with_end_date_returns_error(
    client, role_id, assignment_id
):
    r = await client.post(
        f"/admin/roles/{role_id}/assignments/{assignment_id}/edit-row/",
        headers=HTMX_HEADERS,
        data={
            "start_date": "2020-01-01",
            "end_date": "2022-12-31",
            "is_current": "true",
        },
    )
    assert r.status_code == 200
    trigger = json.loads(r.headers["hx-trigger"])
    assert trigger["showFlash"]["level"] == "error"
    assert b'name="start_date"' in r.content


async def test_edit_row_post_bad_date_returns_error(client, role_id, assignment_id):
    r = await client.post(
        f"/admin/roles/{role_id}/assignments/{assignment_id}/edit-row/",
        headers=HTMX_HEADERS,
        data={"start_date": "not-a-date", "end_date": ""},
    )
    assert r.status_code == 200
    trigger = json.loads(r.headers["hx-trigger"])
    assert trigger["showFlash"]["level"] == "error"
    assert b'name="start_date"' in r.content


async def test_edit_row_post_bad_date_preserves_submitted_start_date(
    client, role_id, assignment_id
):
    r = await client.post(
        f"/admin/roles/{role_id}/assignments/{assignment_id}/edit-row/",
        headers=HTMX_HEADERS,
        data={"start_date": "not-a-date", "end_date": ""},
    )
    assert r.status_code == 200
    assert b"not-a-date" in r.content


async def test_edit_row_post_check_violation_preserves_end_date_input(
    client, role_id, assignment_id
):
    # Submit end_date that differs from DB value (2022-12-31) to prove re-render
    # uses submitted value, not stale ra.end_date.
    r = await client.post(
        f"/admin/roles/{role_id}/assignments/{assignment_id}/edit-row/",
        headers=HTMX_HEADERS,
        data={"start_date": "2020-01-01", "end_date": "2023-06-15", "is_current": "true"},
    )
    assert r.status_code == 200
    assert b"2023-06-15" in r.content


async def test_edit_row_post_non_htmx_redirects(client, role_id, assignment_id):
    r = await client.post(
        f"/admin/roles/{role_id}/assignments/{assignment_id}/edit-row/",
        headers=AUTH_HEADERS,
        data={"start_date": "2020-01-01", "end_date": "2022-12-31"},
        follow_redirects=False,
    )
    assert r.status_code == 303


async def test_edit_row_post_unknown_returns_404(client, role_id):
    r = await client.post(
        f"/admin/roles/{role_id}/assignments/{generate_id()}/edit-row/",
        headers=HTMX_HEADERS,
        data={"start_date": "2020-01-01", "end_date": ""},
    )
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# Archived assignment guard
# ---------------------------------------------------------------------------


@pytest.fixture
async def archived_assignment_id(db, role_id, person_id):
    ra_id = generate_id()
    await db.execute(
        """INSERT INTO role_assignments
               (id, person_id, role_id, is_current, start_date, end_date, archived_at)
           VALUES ($1, $2, $3, FALSE, '2018-01-01', '2019-12-31', NOW())""",
        ra_id, person_id, role_id,
    )
    return ra_id


async def test_edit_row_get_archived_returns_409(
    client, role_id, archived_assignment_id
):
    r = await client.get(
        f"/admin/roles/{role_id}/assignments/{archived_assignment_id}/edit-row/",
        headers=HTMX_HEADERS,
    )
    assert r.status_code == 409


async def test_edit_row_post_archived_returns_409(
    client, role_id, archived_assignment_id
):
    r = await client.post(
        f"/admin/roles/{role_id}/assignments/{archived_assignment_id}/edit-row/",
        headers=HTMX_HEADERS,
        data={"start_date": "2018-01-01", "end_date": "2019-12-31"},
    )
    assert r.status_code == 409


# ---------------------------------------------------------------------------
# End-date disabled state — visual styling
# ---------------------------------------------------------------------------


@pytest.fixture
async def current_assignment_id(db, role_id, person_id):
    ra_id = generate_id()
    await db.execute(
        """INSERT INTO role_assignments (id, person_id, role_id, is_current, start_date)
           VALUES ($1, $2, $3, TRUE, '2024-01-01')""",
        ra_id, person_id, role_id,
    )
    return ra_id


async def test_edit_row_get_current_end_date_uses_muted_class(
    client, role_id, current_assignment_id
):
    """When is_current=True the end_date input must use input--muted class for
    visual disabled state, NOT the HTML disabled attribute (which hides the
    browser calendar icon entirely instead of just muting it)."""
    r = await client.get(
        f"/admin/roles/{role_id}/assignments/{current_assignment_id}/edit-row/",
        headers=HTMX_HEADERS,
    )
    assert r.status_code == 200
    assert b'class="input--muted"' in r.content
    assert b" disabled" not in r.content


async def test_edit_row_get_non_current_end_date_no_muted_class(
    client, role_id, assignment_id
):
    """When is_current=False the end_date input must NOT have input--muted class."""
    r = await client.get(
        f"/admin/roles/{role_id}/assignments/{assignment_id}/edit-row/",
        headers=HTMX_HEADERS,
    )
    assert r.status_code == 200
    assert b'class="input--muted"' not in r.content


async def test_edit_row_get_is_current_uses_pill_toggle(client, role_id, assignment_id):
    """is_current control must be a pill toggle (.toggle / toggle__track), not a
    bare checkbox, consistent with other boolean fields in the admin UI."""
    r = await client.get(
        f"/admin/roles/{role_id}/assignments/{assignment_id}/edit-row/",
        headers=HTMX_HEADERS,
    )
    assert r.status_code == 200
    assert b'class="toggle"' in r.content
    assert b"toggle__track" in r.content
