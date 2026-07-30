"""Integration tests for inline assignment CRUD on person detail."""

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

AUTH_HEADERS = {"X-ExeDev-UserID": "test-user", "X-ExeDev-Email": "test@example.com"}
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


@pytest_asyncio.fixture(loop_scope="session")
async def person_id(db):
    pid = generate_id()
    await db.execute("INSERT INTO people (id) VALUES ($1)", pid)
    await db.execute(
        "INSERT INTO person_names (id, person_id, name, is_canonical) VALUES ($1, $2, $3, TRUE)",
        generate_id(),
        pid,
        "Jane Doe",
    )
    return pid


@pytest_asyncio.fixture(loop_scope="session")
async def role_id(db):
    oid = generate_id()
    await db.execute("INSERT INTO organizations (id) VALUES ($1)", oid)
    await db.execute(
        "INSERT INTO organization_names (id, organization_id, name, is_canonical)"
        " VALUES ($1, $2, $3, TRUE)",
        generate_id(),
        oid,
        "Test Org",
    )
    rid = generate_id()
    await db.execute(
        "INSERT INTO roles (id, organization_id, title) VALUES ($1, $2, $3)",
        rid,
        oid,
        "Executive Director",
    )
    return rid


# ---------------------------------------------------------------------------
# New row form
# ---------------------------------------------------------------------------


async def test_new_row_returns_form(client, person_id):
    r = await client.get(f"/admin/people/{person_id}/assignments/new-row/", headers=HTMX_HEADERS)
    assert r.status_code == 200
    assert b"<form" in r.content
    assert b"role-search" in r.content


async def test_new_row_unknown_person_returns_404(client):
    r = await client.get(
        f"/admin/people/{generate_id()}/assignments/new-row/", headers=HTMX_HEADERS
    )
    assert r.status_code == 404


async def test_new_row_start_date_named_without_visible_label(client, person_id):
    """#318: visible 'Start' label dropped to save room; start input keeps aria-label='Start'.
    'to' stays aria-hidden; end keeps its name (#259)."""
    r = await client.get(f"/admin/people/{person_id}/assignments/new-row/", headers=HTMX_HEADERS)
    assert r.status_code == 200
    body = r.text
    assert ">Start</label>" not in body
    assert 'id="start-date-input-new"' in body
    assert 'aria-label="Start"' in body
    assert re.search(r'<span aria-hidden="true"[^>]*>\s*to</span>', body)
    assert 'aria-label="End"' in body


async def test_new_row_is_current_uses_pill_toggle(client, person_id):
    r = await client.get(f"/admin/people/{person_id}/assignments/new-row/", headers=HTMX_HEADERS)
    assert r.status_code == 200
    assert b'class="toggle"' in r.content
    assert b"toggle__track" in r.content


async def test_new_row_js_disables_end_date_when_is_current_checked(client, person_id):
    r = await client.get(f"/admin/people/{person_id}/assignments/new-row/", headers=HTMX_HEADERS)
    assert r.status_code == 200
    assert b"endDt.disabled = true" in r.content
    assert b"endDt.disabled = false" in r.content


# ---------------------------------------------------------------------------
# Create assignment
# ---------------------------------------------------------------------------


async def test_create_persists_assignment(client, person_id, role_id, db):
    r = await client.post(
        f"/admin/people/{person_id}/assignments/",
        headers=HTMX_HEADERS,
        data={
            "role_id": role_id,
            "start_date": "2024-01-15",
            "end_date": "",
            "is_current": "true",
        },
    )
    assert r.status_code == 200
    row = await db.fetchrow(
        "SELECT * FROM role_assignments WHERE person_id=$1 AND role_id=$2",
        person_id,
        role_id,
    )
    assert row is not None
    assert row["is_current"] is True
    assert str(row["start_date"]) == "2024-01-15"


async def test_create_with_end_date(client, person_id, role_id, db):
    r = await client.post(
        f"/admin/people/{person_id}/assignments/",
        headers=HTMX_HEADERS,
        data={"role_id": role_id, "start_date": "2020-01-01", "end_date": "2023-12-31"},
    )
    assert r.status_code == 200
    row = await db.fetchrow(
        "SELECT * FROM role_assignments WHERE person_id=$1 AND role_id=$2",
        person_id,
        role_id,
    )
    assert row is not None
    assert str(row["end_date"]) == "2023-12-31"


async def test_create_returns_tbody_with_org_and_role(client, person_id, role_id):
    r = await client.post(
        f"/admin/people/{person_id}/assignments/",
        headers=HTMX_HEADERS,
        data={"role_id": role_id, "start_date": "2024-01-01"},
    )
    assert r.status_code == 200
    assert b"Test Org" in r.content
    assert b"Executive Director" in r.content


async def test_create_returns_success_flash(client, person_id, role_id):
    r = await client.post(
        f"/admin/people/{person_id}/assignments/",
        headers=HTMX_HEADERS,
        data={"role_id": role_id, "start_date": "2024-01-01"},
    )
    trigger = json.loads(r.headers["hx-trigger"])
    assert trigger["showFlash"]["level"] == "success"


async def test_create_tbody_includes_edit_url(client, person_id, role_id):
    r = await client.post(
        f"/admin/people/{person_id}/assignments/",
        headers=HTMX_HEADERS,
        data={"role_id": role_id, "start_date": "2024-01-01"},
    )
    assert r.status_code == 200
    assert f"/admin/people/{person_id}/assignments/".encode() in r.content
    assert b"edit-row" in r.content


async def test_create_missing_role_returns_error(client, person_id):
    r = await client.post(
        f"/admin/people/{person_id}/assignments/",
        headers=HTMX_HEADERS,
        data={"role_id": "", "start_date": "2024-01-01"},
    )
    assert r.status_code == 200
    trigger = json.loads(r.headers["hx-trigger"])
    assert trigger["showFlash"]["level"] == "error"
    assert b"<form" in r.content


async def test_create_current_with_end_date_returns_error(client, person_id, role_id):
    r = await client.post(
        f"/admin/people/{person_id}/assignments/",
        headers=HTMX_HEADERS,
        data={
            "role_id": role_id,
            "start_date": "2024-01-01",
            "end_date": "2024-06-01",
            "is_current": "true",
        },
    )
    assert r.status_code == 200
    trigger = json.loads(r.headers["hx-trigger"])
    assert trigger["showFlash"]["level"] == "error"


async def test_create_duplicate_start_date_returns_error(client, person_id, role_id, db):
    """UniqueViolationError on (person_id, role_id, start_date) duplicate."""
    await db.execute(
        """INSERT INTO role_assignments (id, person_id, role_id, is_current, start_date)
           VALUES ($1, $2, $3, FALSE, '2024-01-01')""",
        generate_id(),
        person_id,
        role_id,
    )
    r = await client.post(
        f"/admin/people/{person_id}/assignments/",
        headers=HTMX_HEADERS,
        data={"role_id": role_id, "start_date": "2024-01-01"},
    )
    assert r.status_code == 200
    trigger = json.loads(r.headers["hx-trigger"])
    assert trigger["showFlash"]["level"] == "error"
    assert b"<form" in r.content


async def test_create_non_htmx_redirects(client, person_id, role_id):
    r = await client.post(
        f"/admin/people/{person_id}/assignments/",
        headers=AUTH_HEADERS,
        data={"role_id": role_id, "start_date": "2024-01-01"},
        follow_redirects=False,
    )
    assert r.status_code == 303


# ---------------------------------------------------------------------------
# Shared fixture — existing assignment
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(loop_scope="session")
async def assignment_id(db, person_id, role_id):
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
async def archived_assignment_id(db, person_id, role_id):
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


@pytest_asyncio.fixture(loop_scope="session")
async def current_assignment_id(db, person_id, role_id):
    ra_id = generate_id()
    await db.execute(
        """INSERT INTO role_assignments (id, person_id, role_id, is_current, start_date)
           VALUES ($1, $2, $3, TRUE, '2024-01-01')""",
        ra_id,
        person_id,
        role_id,
    )
    return ra_id


# ---------------------------------------------------------------------------
# Read row
# ---------------------------------------------------------------------------


async def test_read_row_returns_org_and_role(client, person_id, assignment_id):
    r = await client.get(
        f"/admin/people/{person_id}/assignments/{assignment_id}/read-row/",
        headers=HTMX_HEADERS,
    )
    assert r.status_code == 200
    assert b"Test Org" in r.content
    assert b"Executive Director" in r.content


async def test_read_row_returns_dates(client, person_id, assignment_id):
    r = await client.get(
        f"/admin/people/{person_id}/assignments/{assignment_id}/read-row/",
        headers=HTMX_HEADERS,
    )
    assert r.status_code == 200
    assert b"2020-01-01" in r.content
    assert b"2022-12-31" in r.content


async def test_read_row_contains_edit_button(client, person_id, assignment_id):
    r = await client.get(
        f"/admin/people/{person_id}/assignments/{assignment_id}/read-row/",
        headers=HTMX_HEADERS,
    )
    assert r.status_code == 200
    assert b"edit-row" in r.content


async def test_read_row_archived_has_no_edit_button(client, person_id, archived_assignment_id):
    r = await client.get(
        f"/admin/people/{person_id}/assignments/{archived_assignment_id}/read-row/",
        headers=HTMX_HEADERS,
    )
    assert r.status_code == 200
    assert b"edit-row" not in r.content


async def test_read_row_has_open_link_to_assignment_detail(client, person_id, assignment_id):
    r = await client.get(
        f"/admin/people/{person_id}/assignments/{assignment_id}/read-row/",
        headers=HTMX_HEADERS,
    )
    assert r.status_code == 200
    assert f'href="/admin/role-assignments/{assignment_id}/"'.encode() in r.content


async def test_read_row_archived_has_open_link_to_assignment_detail(
    client, person_id, archived_assignment_id
):
    r = await client.get(
        f"/admin/people/{person_id}/assignments/{archived_assignment_id}/read-row/",
        headers=HTMX_HEADERS,
    )
    assert r.status_code == 200
    assert f'href="/admin/role-assignments/{archived_assignment_id}/"'.encode() in r.content


async def test_read_row_org_and_role_still_link_to_their_entities(
    client, person_id, role_id, assignment_id, db
):
    org_id = await db.fetchval("SELECT organization_id FROM roles WHERE id=$1", role_id)
    r = await client.get(
        f"/admin/people/{person_id}/assignments/{assignment_id}/read-row/",
        headers=HTMX_HEADERS,
    )
    assert r.status_code == 200
    assert f'href="/admin/orgs/{org_id}/"'.encode() in r.content
    assert f'href="/admin/roles/{role_id}/"'.encode() in r.content


async def test_read_row_unknown_returns_404(client, person_id):
    r = await client.get(
        f"/admin/people/{person_id}/assignments/{generate_id()}/read-row/",
        headers=HTMX_HEADERS,
    )
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# Note-presence indicator (#318) — icon only; note text never rendered inline
# ---------------------------------------------------------------------------


async def test_read_row_shows_note_indicator_when_notes_present(client, person_id, role_id, db):
    ra_id = generate_id()
    await db.execute(
        """INSERT INTO role_assignments (id, person_id, role_id, is_current, start_date, notes)
           VALUES ($1, $2, $3, FALSE, '2021-01-01', $4)""",
        ra_id,
        person_id,
        role_id,
        "housedemocrats.wa.gov citation",
    )
    r = await client.get(
        f"/admin/people/{person_id}/assignments/{ra_id}/read-row/", headers=HTMX_HEADERS
    )
    assert r.status_code == 200
    assert 'aria-label="Has notes"' in r.text


async def test_read_row_no_note_indicator_when_notes_absent(client, person_id, assignment_id):
    r = await client.get(
        f"/admin/people/{person_id}/assignments/{assignment_id}/read-row/", headers=HTMX_HEADERS
    )
    assert r.status_code == 200
    assert 'aria-label="Has notes"' not in r.text


async def test_read_row_does_not_leak_note_text(client, person_id, role_id, db):
    secret = "SENSITIVE-PROVENANCE-XYZ"
    ra_id = generate_id()
    await db.execute(
        """INSERT INTO role_assignments (id, person_id, role_id, is_current, start_date, notes)
           VALUES ($1, $2, $3, FALSE, '2021-02-02', $4)""",
        ra_id,
        person_id,
        role_id,
        secret,
    )
    r = await client.get(
        f"/admin/people/{person_id}/assignments/{ra_id}/read-row/", headers=HTMX_HEADERS
    )
    assert r.status_code == 200
    assert secret not in r.text


# ---------------------------------------------------------------------------
# Edit row GET
# ---------------------------------------------------------------------------


async def test_edit_row_get_returns_form(client, person_id, assignment_id):
    r = await client.get(
        f"/admin/people/{person_id}/assignments/{assignment_id}/edit-row/",
        headers=HTMX_HEADERS,
    )
    assert r.status_code == 200
    assert b'name="start_date"' in r.content
    assert b'name="end_date"' in r.content
    assert b'name="is_current"' in r.content


async def test_edit_row_get_uses_individual_cells(client, person_id, assignment_id):
    """Edit row must use 6 individual <td> cells (not colspan) so controls align
    with Org / Role / Start / End / Status / Actions column headers."""
    r = await client.get(
        f"/admin/people/{person_id}/assignments/{assignment_id}/edit-row/",
        headers=HTMX_HEADERS,
    )
    assert r.status_code == 200
    assert b"colspan" not in r.content
    assert r.content.count(b"<td") == 6


async def test_edit_row_date_aria_labels_match_new_row(client, person_id, assignment_id):
    """#259 CR: edit-row date accessible names align with the new-row forms (Start/End)."""
    r = await client.get(
        f"/admin/people/{person_id}/assignments/{assignment_id}/edit-row/",
        headers=HTMX_HEADERS,
    )
    assert r.status_code == 200
    body = r.text
    assert 'aria-label="Start"' in body
    assert 'aria-label="End"' in body
    assert 'aria-label="Start date"' not in body
    assert 'aria-label="End date"' not in body


async def test_edit_row_get_prepopulates_dates(client, person_id, assignment_id):
    r = await client.get(
        f"/admin/people/{person_id}/assignments/{assignment_id}/edit-row/",
        headers=HTMX_HEADERS,
    )
    assert r.status_code == 200
    assert b"2020-01-01" in r.content
    assert b"2022-12-31" in r.content


async def test_edit_row_get_shows_org_and_role(client, person_id, assignment_id):
    r = await client.get(
        f"/admin/people/{person_id}/assignments/{assignment_id}/edit-row/",
        headers=HTMX_HEADERS,
    )
    assert r.status_code == 200
    assert b"Test Org" in r.content
    assert b"Executive Director" in r.content


async def test_edit_row_get_archived_returns_409(client, person_id, archived_assignment_id):
    r = await client.get(
        f"/admin/people/{person_id}/assignments/{archived_assignment_id}/edit-row/",
        headers=HTMX_HEADERS,
    )
    assert r.status_code == 409


async def test_edit_row_get_unknown_returns_404(client, person_id):
    r = await client.get(
        f"/admin/people/{person_id}/assignments/{generate_id()}/edit-row/",
        headers=HTMX_HEADERS,
    )
    assert r.status_code == 404


async def test_edit_row_get_current_end_date_is_disabled(client, person_id, current_assignment_id):
    r = await client.get(
        f"/admin/people/{person_id}/assignments/{current_assignment_id}/edit-row/",
        headers=HTMX_HEADERS,
    )
    assert r.status_code == 200
    assert b" disabled>" in r.content


async def test_edit_row_get_non_current_end_date_not_disabled(client, person_id, assignment_id):
    r = await client.get(
        f"/admin/people/{person_id}/assignments/{assignment_id}/edit-row/",
        headers=HTMX_HEADERS,
    )
    assert r.status_code == 200
    assert b" disabled>" not in r.content


async def test_edit_row_get_is_current_uses_pill_toggle(client, person_id, assignment_id):
    r = await client.get(
        f"/admin/people/{person_id}/assignments/{assignment_id}/edit-row/",
        headers=HTMX_HEADERS,
    )
    assert r.status_code == 200
    assert b'class="toggle"' in r.content
    assert b"toggle__track" in r.content


# ---------------------------------------------------------------------------
# Edit row POST — success
# ---------------------------------------------------------------------------


async def test_edit_row_post_updates_start_date(client, person_id, assignment_id, db):
    r = await client.post(
        f"/admin/people/{person_id}/assignments/{assignment_id}/edit-row/",
        headers=HTMX_HEADERS,
        data={"start_date": "2021-03-01", "end_date": "2022-12-31"},
    )
    assert r.status_code == 200
    row = await db.fetchrow("SELECT start_date FROM role_assignments WHERE id=$1", assignment_id)
    assert str(row["start_date"]) == "2021-03-01"


async def test_edit_row_post_sets_is_current(client, person_id, assignment_id, db):
    r = await client.post(
        f"/admin/people/{person_id}/assignments/{assignment_id}/edit-row/",
        headers=HTMX_HEADERS,
        data={"start_date": "2020-01-01", "end_date": "", "is_current": "true"},
    )
    assert r.status_code == 200
    row = await db.fetchrow(
        "SELECT is_current, end_date FROM role_assignments WHERE id=$1", assignment_id
    )
    assert row["is_current"] is True
    assert row["end_date"] is None


async def test_edit_row_post_returns_all_rows(client, person_id, assignment_id):
    r = await client.post(
        f"/admin/people/{person_id}/assignments/{assignment_id}/edit-row/",
        headers=HTMX_HEADERS,
        data={"start_date": "2020-01-01", "end_date": "2022-12-31"},
    )
    assert r.status_code == 200
    assert b"Test Org" in r.content
    assert b"Executive Director" in r.content


async def test_edit_row_post_returns_success_flash(client, person_id, assignment_id):
    r = await client.post(
        f"/admin/people/{person_id}/assignments/{assignment_id}/edit-row/",
        headers=HTMX_HEADERS,
        data={"start_date": "2020-01-01", "end_date": "2022-12-31"},
    )
    trigger = json.loads(r.headers["hx-trigger"])
    assert trigger["showFlash"]["level"] == "success"


# ---------------------------------------------------------------------------
# Edit row POST — errors
# ---------------------------------------------------------------------------


async def test_edit_row_post_current_with_end_date_returns_error(client, person_id, assignment_id):
    r = await client.post(
        f"/admin/people/{person_id}/assignments/{assignment_id}/edit-row/",
        headers=HTMX_HEADERS,
        data={"start_date": "2020-01-01", "end_date": "2022-12-31", "is_current": "true"},
    )
    assert r.status_code == 200
    trigger = json.loads(r.headers["hx-trigger"])
    assert trigger["showFlash"]["level"] == "error"
    assert b'name="start_date"' in r.content


async def test_edit_row_post_bad_date_returns_error(client, person_id, assignment_id):
    r = await client.post(
        f"/admin/people/{person_id}/assignments/{assignment_id}/edit-row/",
        headers=HTMX_HEADERS,
        data={"start_date": "not-a-date", "end_date": ""},
    )
    assert r.status_code == 200
    trigger = json.loads(r.headers["hx-trigger"])
    assert trigger["showFlash"]["level"] == "error"
    assert b"not-a-date" in r.content


async def test_edit_row_post_check_violation_preserves_end_date_input(
    client, person_id, assignment_id
):
    r = await client.post(
        f"/admin/people/{person_id}/assignments/{assignment_id}/edit-row/",
        headers=HTMX_HEADERS,
        data={"start_date": "2020-01-01", "end_date": "2023-06-15", "is_current": "true"},
    )
    assert r.status_code == 200
    assert b"2023-06-15" in r.content


async def test_edit_row_post_archived_returns_409(client, person_id, archived_assignment_id):
    r = await client.post(
        f"/admin/people/{person_id}/assignments/{archived_assignment_id}/edit-row/",
        headers=HTMX_HEADERS,
        data={"start_date": "2018-01-01", "end_date": "2019-12-31"},
    )
    assert r.status_code == 409


async def test_edit_row_post_non_htmx_redirects(client, person_id, assignment_id):
    r = await client.post(
        f"/admin/people/{person_id}/assignments/{assignment_id}/edit-row/",
        headers=AUTH_HEADERS,
        data={"start_date": "2020-01-01", "end_date": "2022-12-31"},
        follow_redirects=False,
    )
    assert r.status_code == 303


async def test_edit_row_post_unknown_returns_404(client, person_id):
    r = await client.post(
        f"/admin/people/{person_id}/assignments/{generate_id()}/edit-row/",
        headers=HTMX_HEADERS,
        data={"start_date": "2020-01-01", "end_date": ""},
    )
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# Person detail page integration
# ---------------------------------------------------------------------------


async def test_person_detail_shows_role_assignments_table(client, person_id, assignment_id):
    """Person detail page must render the role assignments table with HTMX controls."""
    r = await client.get(f"/admin/people/{person_id}/", headers=HTMX_HEADERS)
    assert r.status_code == 200
    assert b"person-assignments-table" in r.content


async def test_person_detail_shows_add_assignment_button(client, person_id):
    r = await client.get(f"/admin/people/{person_id}/", headers=HTMX_HEADERS)
    assert r.status_code == 200
    assert b"Add assignment" in r.content


async def test_person_detail_role_title_links_to_role_detail(
    client, person_id, assignment_id, role_id
):
    r = await client.get(f"/admin/people/{person_id}/", headers=HTMX_HEADERS)
    assert r.status_code == 200
    assert f"/admin/roles/{role_id}/".encode() in r.content


async def test_person_detail_hides_add_button_when_archived(client, db):
    pid = generate_id()
    await db.execute("INSERT INTO people (id, archived_at) VALUES ($1, NOW())", pid)
    await db.execute(
        "INSERT INTO person_names (id, person_id, name, is_canonical) VALUES ($1, $2, $3, TRUE)",
        generate_id(),
        pid,
        "Archived Person",
    )
    r = await client.get(f"/admin/people/{pid}/", headers=HTMX_HEADERS)
    assert r.status_code == 200
    assert b"Add assignment" not in r.content


# ---------------------------------------------------------------------------
# Archive assignment
# ---------------------------------------------------------------------------


async def test_archive_soft_deletes_assignment(client, person_id, assignment_id, db):
    r = await client.post(
        f"/admin/people/{person_id}/assignments/{assignment_id}/archive/",
        headers=HTMX_HEADERS,
    )
    assert r.status_code == 200
    row = await db.fetchrow(
        "SELECT id, archived_at FROM role_assignments WHERE id=$1", assignment_id
    )
    assert row is not None
    assert row["archived_at"] is not None


async def test_archive_returns_sorted_tbody(client, person_id, role_id, db):
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
        f"/admin/people/{person_id}/assignments/{ra_arch}/archive/",
        headers=HTMX_HEADERS,
    )
    assert r.status_code == 200
    assert b"2024-01-01" in r.content
    assert b"2020-01-01" in r.content


async def test_archive_returns_success_flash(client, person_id, assignment_id):
    r = await client.post(
        f"/admin/people/{person_id}/assignments/{assignment_id}/archive/",
        headers=HTMX_HEADERS,
    )
    trigger = json.loads(r.headers["hx-trigger"])
    assert trigger["showFlash"]["level"] == "success"
    assert trigger["showFlash"]["body"] == "Assignment archived."


async def test_archive_already_archived_returns_409(client, person_id, archived_assignment_id):
    """Archiving an already-archived row is rejected with 409 (idempotency guard)."""
    r = await client.post(
        f"/admin/people/{person_id}/assignments/{archived_assignment_id}/archive/",
        headers=HTMX_HEADERS,
    )
    assert r.status_code == 409
    assert r.json()["detail"] == "Role assignment is already archived"


async def test_archive_unknown_returns_404(client, person_id):
    r = await client.post(
        f"/admin/people/{person_id}/assignments/{generate_id()}/archive/",
        headers=HTMX_HEADERS,
    )
    assert r.status_code == 404


async def test_archive_wrong_person_returns_404(client, person_id, assignment_id, db):
    other_pid = generate_id()
    await db.execute("INSERT INTO people (id) VALUES ($1)", other_pid)
    r = await client.post(
        f"/admin/people/{other_pid}/assignments/{assignment_id}/archive/",
        headers=HTMX_HEADERS,
    )
    assert r.status_code == 404


async def test_archive_non_htmx_redirects(client, person_id, assignment_id):
    r = await client.post(
        f"/admin/people/{person_id}/assignments/{assignment_id}/archive/",
        headers=AUTH_HEADERS,
        follow_redirects=False,
    )
    assert r.status_code == 303


async def test_read_row_has_archive_button(client, person_id, assignment_id):
    r = await client.get(
        f"/admin/people/{person_id}/assignments/{assignment_id}/read-row/",
        headers=HTMX_HEADERS,
    )
    assert r.status_code == 200
    assert b"hx-post" in r.content
    assert b"/archive/" in r.content
    assert b"hx-confirm" in r.content
    assert b"Archive" in r.content
    assert b"hx-delete" not in r.content


async def test_read_row_archived_has_no_archive_button(client, person_id, archived_assignment_id):
    """Archived rows show no archive or delete button; only Open."""
    r = await client.get(
        f"/admin/people/{person_id}/assignments/{archived_assignment_id}/read-row/",
        headers=HTMX_HEADERS,
    )
    assert r.status_code == 200
    assert b"hx-post" not in r.content
    assert b"hx-delete" not in r.content
    assert b"/archive/" not in r.content


async def test_inline_hard_delete_route_removed(client, person_id, assignment_id):
    """Inline DELETE route is gone; permanent delete only from RA detail page."""
    r = await client.delete(
        f"/admin/people/{person_id}/assignments/{assignment_id}/",
        headers=HTMX_HEADERS,
    )
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# Citations indicator (#341) — reuses the module's `assignment_id` fixture
# ---------------------------------------------------------------------------


async def _add_citation(db, entity_type: str, entity_id: str, url: str, archived: bool = False):
    await db.execute(
        "INSERT INTO citations (id, entity_type, entity_id, url, archived_at)"
        " VALUES ($1, $2, $3, $4, CASE WHEN $5 THEN now() END)",
        generate_id(),
        entity_type,
        entity_id,
        url,
        archived,
    )


async def test_read_row_shows_citations_indicator(client, db, person_id, assignment_id):
    await _add_citation(db, "role_assignment", assignment_id, "https://example.com/a")
    await _add_citation(db, "role_assignment", assignment_id, "https://example.com/b")
    r = await client.get(
        f"/admin/people/{person_id}/assignments/{assignment_id}/read-row/",
        headers=HTMX_HEADERS,
    )
    assert r.status_code == 200
    body = r.text
    assert 'class="citation-indicator"' in body
    assert 'aria-label="2 citations"' in body
    assert "📚" in body
    assert f'href="/admin/role-assignments/{assignment_id}/"' in body


async def test_read_row_omits_indicator_without_citations(client, person_id, assignment_id):
    r = await client.get(
        f"/admin/people/{person_id}/assignments/{assignment_id}/read-row/",
        headers=HTMX_HEADERS,
    )
    assert r.status_code == 200
    assert "citation-indicator" not in r.text


async def test_indicator_excludes_archived_citations(client, db, person_id, assignment_id):
    await _add_citation(db, "role_assignment", assignment_id, "https://example.com/a")
    await _add_citation(
        db, "role_assignment", assignment_id, "https://example.com/x", archived=True
    )
    r = await client.get(
        f"/admin/people/{person_id}/assignments/{assignment_id}/read-row/",
        headers=HTMX_HEADERS,
    )
    assert r.status_code == 200
    assert 'aria-label="1 citation"' in r.text
    assert 'aria-label="1 citations"' not in r.text


async def test_person_detail_assignments_table_shows_indicator(
    client, db, person_id, assignment_id
):
    """Batch path: the detail-page assignments table carries the count (no N+1)."""
    await _add_citation(db, "role_assignment", assignment_id, "https://example.com/a")
    r = await client.get(f"/admin/people/{person_id}/", headers=AUTH_HEADERS)
    assert r.status_code == 200
    assert 'aria-label="1 citation"' in r.text
