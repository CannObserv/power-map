"""Integration tests for inline assignment create on role detail."""

import datetime as dt
import json
import re

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
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test", follow_redirects=True
    ) as c:
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


@pytest_asyncio.fixture(loop_scope="session")
async def role_id(db):
    oid = await _make_org(db, "Test Org")
    rid = generate_id()
    await db.execute(
        "INSERT INTO roles (id, organization_id, title) VALUES ($1, $2, $3)",
        rid,
        oid,
        "Executive Director",
    )
    return rid


@pytest_asyncio.fixture(loop_scope="session")
async def bounded_role_id(db):
    """Role with established_on=2010-01-01, abolished_on=2020-12-31."""
    oid = await _make_org(db, "Bounded Org")
    rid = generate_id()
    await db.execute(
        "INSERT INTO roles (id, organization_id, title, established_on, abolished_on)"
        " VALUES ($1, $2, $3, $4, $5)",
        rid,
        oid,
        "Bounded Director",
        dt.date(2010, 1, 1),
        dt.date(2020, 12, 31),
    )
    return rid


@pytest_asyncio.fixture(loop_scope="session")
async def person_id(db):
    return await _make_person(db, "Jane Doe")


# ---------------------------------------------------------------------------
# New row form
# ---------------------------------------------------------------------------


async def test_new_row_returns_form(client, role_id):
    r = await client.get(f"/admin/roles/{role_id}/assignments/new-row/", headers=HTMX_HEADERS)
    assert r.status_code == 200
    assert b"<form" in r.content
    assert b"person-search" in r.content


async def test_new_row_unknown_role_returns_404(client):
    r = await client.get(f"/admin/roles/{generate_id()}/assignments/new-row/", headers=HTMX_HEADERS)
    assert r.status_code == 404


async def test_new_row_labels_start_end_dates(client, role_id):
    """#259: visible 'Start' label (row-scoped for/id) + aria-hidden 'to'; end keeps a name."""
    r = await client.get(f"/admin/roles/{role_id}/assignments/new-row/", headers=HTMX_HEADERS)
    assert r.status_code == 200
    body = r.text
    assert '<label for="start-date-input-new"' in body
    assert ">Start</label>" in body
    assert 'id="start-date-input-new"' in body
    assert re.search(r'<span aria-hidden="true"[^>]*>\s*to</span>', body)
    assert 'aria-label="End"' in body


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
        role_id,
        person_id,
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
        role_id,
        person_id,
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


@pytest_asyncio.fixture(loop_scope="session")
async def assignment_id(db, role_id, person_id):
    ra_id = generate_id()
    await db.execute(
        """INSERT INTO role_assignments (id, person_id, role_id, is_current, start_date, end_date)
           VALUES ($1, $2, $3, FALSE, '2020-01-01', '2022-12-31')""",
        ra_id,
        person_id,
        role_id,
    )
    return ra_id


@pytest_asyncio.fixture(loop_scope="session")
async def bounded_assignment_id(db, bounded_role_id, person_id):
    """Assignment on a role with established/abolished bounds (2010–2020)."""
    ra_id = generate_id()
    await db.execute(
        """INSERT INTO role_assignments (id, person_id, role_id, is_current, start_date, end_date)
           VALUES ($1, $2, $3, FALSE, '2012-01-01', '2015-12-31')""",
        ra_id,
        person_id,
        bounded_role_id,
    )
    return ra_id


@pytest_asyncio.fixture(loop_scope="session")
async def current_bounded_assignment_id(db, bounded_role_id, person_id):
    """Current (open-ended) assignment on a bounded role — end date disabled."""
    ra_id = generate_id()
    await db.execute(
        """INSERT INTO role_assignments (id, person_id, role_id, is_current, start_date, end_date)
           VALUES ($1, $2, $3, TRUE, '2012-01-01', NULL)""",
        ra_id,
        person_id,
        bounded_role_id,
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


async def test_read_row_has_open_link_to_assignment_detail(client, role_id, assignment_id):
    r = await client.get(
        f"/admin/roles/{role_id}/assignments/{assignment_id}/read-row/",
        headers=HTMX_HEADERS,
    )
    assert r.status_code == 200
    assert f'href="/admin/role-assignments/{assignment_id}/"'.encode() in r.content


async def test_read_row_archived_has_open_link_to_assignment_detail(
    client, role_id, archived_assignment_id
):
    r = await client.get(
        f"/admin/roles/{role_id}/assignments/{archived_assignment_id}/read-row/",
        headers=HTMX_HEADERS,
    )
    assert r.status_code == 200
    assert f'href="/admin/role-assignments/{archived_assignment_id}/"'.encode() in r.content


async def test_read_row_person_name_still_links_to_person(
    client, role_id, assignment_id, person_id
):
    r = await client.get(
        f"/admin/roles/{role_id}/assignments/{assignment_id}/read-row/",
        headers=HTMX_HEADERS,
    )
    assert r.status_code == 200
    assert f'href="/admin/people/{person_id}/"'.encode() in r.content


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


async def test_edit_row_date_aria_labels_match_new_row(client, role_id, assignment_id):
    """#259 CR: edit-row date accessible names align with the new-row forms (Start/End)."""
    r = await client.get(
        f"/admin/roles/{role_id}/assignments/{assignment_id}/edit-row/",
        headers=HTMX_HEADERS,
    )
    assert r.status_code == 200
    body = r.text
    assert 'aria-label="Start"' in body
    assert 'aria-label="End"' in body
    assert 'aria-label="Start date"' not in body
    assert 'aria-label="End date"' not in body


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


async def test_edit_row_get_bounded_role_surfaces_range_accessibly(
    client, bounded_role_id, bounded_assignment_id
):
    """#243: the role established/abolished window must be conveyed via an
    accessible mechanism (visible hint linked by aria-describedby), not `title`."""
    r = await client.get(
        f"/admin/roles/{bounded_role_id}/assignments/{bounded_assignment_id}/edit-row/",
        headers=HTMX_HEADERS,
    )
    assert r.status_code == 200
    # No inaccessible title attribute (STYLE §12 ban).
    assert b"title=" not in r.content
    # Visible hint with the role window on both date inputs.
    assert r.content.count(b"form-group__hint") == 2
    assert b"2010-01-01" in r.content
    assert b"2020-12-31" in r.content
    # Each date input references its hint via aria-describedby AND that target id
    # actually exists — the association is the whole point of the fix.
    start_target = f'id="start-date-range-hint-{bounded_assignment_id}"'.encode()
    end_target = f'id="end-date-range-hint-{bounded_assignment_id}"'.encode()
    assert f'aria-describedby="start-date-range-hint-{bounded_assignment_id}"'.encode() in r.content
    assert f'aria-describedby="end-date-range-hint-{bounded_assignment_id}"'.encode() in r.content
    assert start_target in r.content
    assert end_target in r.content


async def test_edit_row_get_current_bounded_role_keeps_both_hints(
    client, bounded_role_id, current_bounded_assignment_id
):
    """#243: the range hint renders on both date inputs even when the assignment is
    current. The end-date input is disabled at render but the Current checkbox can
    re-enable it client-side, so the hint must already be present and linked."""
    r = await client.get(
        f"/admin/roles/{bounded_role_id}/assignments/{current_bounded_assignment_id}/edit-row/",
        headers=HTMX_HEADERS,
    )
    assert r.status_code == 200
    assert r.content.count(b"form-group__hint") == 2
    assert b'aria-describedby="start-date-range-hint-' in r.content
    assert b'aria-describedby="end-date-range-hint-' in r.content
    # End-date input is disabled at initial render (current assignment).
    assert b"disabled" in r.content


async def test_edit_row_get_unbounded_role_has_no_range_hint(client, role_id, assignment_id):
    """#243: a role without bounds emits no hint, no aria-describedby, no title."""
    r = await client.get(
        f"/admin/roles/{role_id}/assignments/{assignment_id}/edit-row/",
        headers=HTMX_HEADERS,
    )
    assert r.status_code == 200
    assert b"title=" not in r.content
    assert b"form-group__hint" not in r.content
    assert b"aria-describedby" not in r.content


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
    row = await db.fetchrow("SELECT start_date FROM role_assignments WHERE id=$1", assignment_id)
    assert str(row["start_date"]) == "2021-03-01"


async def test_edit_row_post_updates_end_date(client, role_id, assignment_id, db):
    r = await client.post(
        f"/admin/roles/{role_id}/assignments/{assignment_id}/edit-row/",
        headers=HTMX_HEADERS,
        data={"start_date": "2020-01-01", "end_date": "2023-06-30"},
    )
    assert r.status_code == 200
    row = await db.fetchrow("SELECT end_date FROM role_assignments WHERE id=$1", assignment_id)
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


async def test_edit_row_post_current_with_end_date_returns_error(client, role_id, assignment_id):
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


async def test_edit_row_post_duplicate_start_date_returns_error(client, role_id, person_id, db):
    """Changing start_date to collide with an existing assignment returns an error flash."""
    # ra1 holds 2020-01-01; editing ra2 to the same date should hit the unique index
    ra1 = generate_id()
    await db.execute(
        "INSERT INTO role_assignments (id, person_id, role_id, is_current, start_date)"
        " VALUES ($1, $2, $3, FALSE, '2020-01-01')",
        ra1,
        person_id,
        role_id,
    )
    ra2 = generate_id()
    await db.execute(
        "INSERT INTO role_assignments (id, person_id, role_id, is_current, start_date)"
        " VALUES ($1, $2, $3, FALSE, '2021-06-01')",
        ra2,
        person_id,
        role_id,
    )
    r = await client.post(
        f"/admin/roles/{role_id}/assignments/{ra2}/edit-row/",
        headers=HTMX_HEADERS,
        data={"start_date": "2020-01-01", "end_date": "", "is_current": ""},
    )
    assert r.status_code == 200
    trigger = json.loads(r.headers["hx-trigger"])
    assert trigger["showFlash"]["level"] == "error"
    assert b'name="start_date"' in r.content


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


@pytest_asyncio.fixture(loop_scope="session")
async def archived_assignment_id(db, role_id, person_id):
    ra_id = generate_id()
    await db.execute(
        """INSERT INTO role_assignments
               (id, person_id, role_id, is_current, start_date, end_date, archived_at)
           VALUES ($1, $2, $3, FALSE, '2018-01-01', '2019-12-31', NOW())""",
        ra_id,
        person_id,
        role_id,
    )
    return ra_id


async def test_edit_row_get_archived_returns_409(client, role_id, archived_assignment_id):
    r = await client.get(
        f"/admin/roles/{role_id}/assignments/{archived_assignment_id}/edit-row/",
        headers=HTMX_HEADERS,
    )
    assert r.status_code == 409


async def test_edit_row_post_archived_returns_409(client, role_id, archived_assignment_id):
    r = await client.post(
        f"/admin/roles/{role_id}/assignments/{archived_assignment_id}/edit-row/",
        headers=HTMX_HEADERS,
        data={"start_date": "2018-01-01", "end_date": "2019-12-31"},
    )
    assert r.status_code == 409


# ---------------------------------------------------------------------------
# Edit-row visual behaviour
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(loop_scope="session")
async def current_assignment_id(db, role_id, person_id):
    ra_id = generate_id()
    await db.execute(
        """INSERT INTO role_assignments (id, person_id, role_id, is_current, start_date)
           VALUES ($1, $2, $3, TRUE, '2024-01-01')""",
        ra_id,
        person_id,
        role_id,
    )
    return ra_id


async def test_edit_row_get_current_end_date_is_disabled(client, role_id, current_assignment_id):
    """When is_current=True the end_date input must carry the disabled attribute."""
    r = await client.get(
        f"/admin/roles/{role_id}/assignments/{current_assignment_id}/edit-row/",
        headers=HTMX_HEADERS,
    )
    assert r.status_code == 200
    assert b" disabled>" in r.content


async def test_edit_row_get_non_current_end_date_not_disabled(client, role_id, assignment_id):
    """When is_current=False the end_date input must NOT have the disabled attribute."""
    r = await client.get(
        f"/admin/roles/{role_id}/assignments/{assignment_id}/edit-row/",
        headers=HTMX_HEADERS,
    )
    assert r.status_code == 200
    # Match the HTML attribute, not occurrences in the inline JS
    assert b" disabled>" not in r.content


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


async def test_new_row_is_current_uses_pill_toggle(client, role_id):
    """New-assignment form row must use a pill toggle for is_current, consistent
    with the edit-row pattern."""
    r = await client.get(f"/admin/roles/{role_id}/assignments/new-row/", headers=HTMX_HEADERS)
    assert r.status_code == 200
    assert b'class="toggle"' in r.content
    assert b"toggle__track" in r.content


async def test_new_row_js_disables_end_date_when_is_current_checked(client, role_id):
    """new-row inline script must wire the is_current toggle to disable end_date.
    Verifies the coupling JS is present; runtime behaviour is browser-only."""
    r = await client.get(f"/admin/roles/{role_id}/assignments/new-row/", headers=HTMX_HEADERS)
    assert r.status_code == 200
    assert b"endDt.disabled = true" in r.content
    assert b"endDt.disabled = false" in r.content


# ---------------------------------------------------------------------------
# Archive assignment
# ---------------------------------------------------------------------------


async def test_archive_soft_deletes_assignment(client, role_id, assignment_id, db):
    r = await client.post(
        f"/admin/roles/{role_id}/assignments/{assignment_id}/archive/",
        headers=HTMX_HEADERS,
    )
    assert r.status_code == 200
    row = await db.fetchrow(
        "SELECT id, archived_at FROM role_assignments WHERE id=$1", assignment_id
    )
    assert row is not None
    assert row["archived_at"] is not None


async def test_archive_returns_sorted_tbody(client, role_id, person_id, db):
    """After archive, full tbody is returned so rows stay sorted."""
    ra_keep = generate_id()
    await db.execute(
        """INSERT INTO role_assignments (id, person_id, role_id, is_current, start_date)
           VALUES ($1, $2, $3, TRUE, '2024-01-01')""",
        ra_keep,
        person_id,
        role_id,
    )
    ra_arch = generate_id()
    await db.execute(
        """INSERT INTO role_assignments (id, person_id, role_id, is_current, start_date, end_date)
           VALUES ($1, $2, $3, FALSE, '2020-01-01', '2023-12-31')""",
        ra_arch,
        person_id,
        role_id,
    )
    r = await client.post(
        f"/admin/roles/{role_id}/assignments/{ra_arch}/archive/",
        headers=HTMX_HEADERS,
    )
    assert r.status_code == 200
    assert b"2024-01-01" in r.content
    assert b"2020-01-01" in r.content


async def test_archive_returns_success_flash(client, role_id, assignment_id):
    r = await client.post(
        f"/admin/roles/{role_id}/assignments/{assignment_id}/archive/",
        headers=HTMX_HEADERS,
    )
    trigger = json.loads(r.headers["hx-trigger"])
    assert trigger["showFlash"]["level"] == "success"
    assert trigger["showFlash"]["body"] == "Assignment archived."


async def test_archive_already_archived_returns_409(client, role_id, archived_assignment_id):
    """Archiving an already-archived row is rejected with 409 (idempotency guard)."""
    r = await client.post(
        f"/admin/roles/{role_id}/assignments/{archived_assignment_id}/archive/",
        headers=HTMX_HEADERS,
    )
    assert r.status_code == 409
    assert r.json()["detail"] == "Role assignment is already archived"


async def test_archive_unknown_returns_404(client, role_id):
    r = await client.post(
        f"/admin/roles/{role_id}/assignments/{generate_id()}/archive/",
        headers=HTMX_HEADERS,
    )
    assert r.status_code == 404


async def test_archive_wrong_role_returns_404(client, role_id, assignment_id, db):
    oid = await _make_org(db, "Other Org")
    other_rid = generate_id()
    await db.execute(
        "INSERT INTO roles (id, organization_id, title) VALUES ($1, $2, $3)",
        other_rid,
        oid,
        "Other Role",
    )
    r = await client.post(
        f"/admin/roles/{other_rid}/assignments/{assignment_id}/archive/",
        headers=HTMX_HEADERS,
    )
    assert r.status_code == 404


async def test_archive_non_htmx_redirects(client, role_id, assignment_id):
    r = await client.post(
        f"/admin/roles/{role_id}/assignments/{assignment_id}/archive/",
        headers=AUTH_HEADERS,
        follow_redirects=False,
    )
    assert r.status_code == 303


async def test_read_row_has_archive_button(client, role_id, assignment_id):
    r = await client.get(
        f"/admin/roles/{role_id}/assignments/{assignment_id}/read-row/",
        headers=HTMX_HEADERS,
    )
    assert r.status_code == 200
    assert b"hx-post" in r.content
    assert b"/archive/" in r.content
    assert b"hx-confirm" in r.content
    assert b"Archive" in r.content
    assert b"hx-delete" not in r.content


async def test_read_row_archived_has_no_archive_button(client, role_id, archived_assignment_id):
    """Archived rows show no archive or delete button; only Open."""
    r = await client.get(
        f"/admin/roles/{role_id}/assignments/{archived_assignment_id}/read-row/",
        headers=HTMX_HEADERS,
    )
    assert r.status_code == 200
    assert b"hx-post" not in r.content
    assert b"hx-delete" not in r.content
    assert b"/archive/" not in r.content


async def test_inline_hard_delete_route_removed(client, role_id, assignment_id):
    """Inline DELETE route is gone; permanent delete only from RA detail page."""
    r = await client.delete(
        f"/admin/roles/{role_id}/assignments/{assignment_id}/",
        headers=HTMX_HEADERS,
    )
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# Boundary enforcement: create
# ---------------------------------------------------------------------------


async def test_create_start_before_established_returns_error(client, bounded_role_id, person_id):
    r = await client.post(
        f"/admin/roles/{bounded_role_id}/assignments/",
        headers=HTMX_HEADERS,
        data={"person_id": person_id, "start_date": "2009-12-31", "end_date": ""},
    )
    assert r.status_code == 200
    trigger = json.loads(r.headers["hx-trigger"])
    assert trigger["showFlash"]["level"] == "error"
    assert b"<form" in r.content


async def test_create_end_after_abolished_returns_error(client, bounded_role_id, person_id):
    r = await client.post(
        f"/admin/roles/{bounded_role_id}/assignments/",
        headers=HTMX_HEADERS,
        data={
            "person_id": person_id,
            "start_date": "2015-01-01",
            "end_date": "2021-01-01",
        },
    )
    assert r.status_code == 200
    trigger = json.loads(r.headers["hx-trigger"])
    assert trigger["showFlash"]["level"] == "error"
    assert b"<form" in r.content


async def test_create_within_bounds_succeeds(client, bounded_role_id, person_id, db):
    r = await client.post(
        f"/admin/roles/{bounded_role_id}/assignments/",
        headers=HTMX_HEADERS,
        data={
            "person_id": person_id,
            "start_date": "2015-01-01",
            "end_date": "2019-12-31",
        },
    )
    assert r.status_code == 200
    row = await db.fetchrow(
        "SELECT id FROM role_assignments WHERE role_id=$1 AND person_id=$2",
        bounded_role_id,
        person_id,
    )
    assert row is not None


# ---------------------------------------------------------------------------
# Boundary enforcement: edit
# ---------------------------------------------------------------------------


async def _make_assignment(db, role_id, person_id) -> str:
    aid = generate_id()
    await db.execute(
        "INSERT INTO role_assignments (id, person_id, role_id, is_current)"
        " VALUES ($1, $2, $3, FALSE)",
        aid,
        person_id,
        role_id,
    )
    return aid


async def test_edit_start_before_established_returns_error(client, bounded_role_id, person_id, db):
    aid = await _make_assignment(db, bounded_role_id, person_id)
    r = await client.post(
        f"/admin/roles/{bounded_role_id}/assignments/{aid}/edit-row/",
        headers=HTMX_HEADERS,
        data={"start_date": "2009-06-01", "end_date": "", "is_current": ""},
    )
    assert r.status_code == 200
    trigger = json.loads(r.headers["hx-trigger"])
    assert trigger["showFlash"]["level"] == "error"


async def test_edit_end_after_abolished_returns_error(client, bounded_role_id, person_id, db):
    aid = await _make_assignment(db, bounded_role_id, person_id)
    r = await client.post(
        f"/admin/roles/{bounded_role_id}/assignments/{aid}/edit-row/",
        headers=HTMX_HEADERS,
        data={"start_date": "2015-01-01", "end_date": "2021-06-01", "is_current": ""},
    )
    assert r.status_code == 200
    trigger = json.loads(r.headers["hx-trigger"])
    assert trigger["showFlash"]["level"] == "error"
