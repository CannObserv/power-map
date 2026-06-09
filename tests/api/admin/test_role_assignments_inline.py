"""Integration tests for inline editing on the role assignment detail page."""

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient

from src.api.main import app
from src.core.db import generate_id

pytestmark = [
    pytest.mark.integration,
]

AUTH_HEADERS = {
    "X-ExeDev-UserID": "usr_test",
    "X-ExeDev-Email": "admin@test.com",
}


@pytest_asyncio.fixture(loop_scope="session")
async def db(db_pool):
    async with db_pool.acquire() as conn:
        yield conn


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


@pytest_asyncio.fixture(loop_scope="session")
async def org_id(db):
    oid = generate_id()
    await db.execute("INSERT INTO organizations (id) VALUES ($1)", oid)
    await db.execute(
        "INSERT INTO organization_names (id, organization_id, name, is_canonical)"
        " VALUES ($1, $2, 'Inline Org', TRUE)",
        generate_id(),
        oid,
    )
    yield oid
    await db.execute("DELETE FROM organization_names WHERE organization_id = $1", oid)
    await db.execute("DELETE FROM organizations WHERE id = $1", oid)


@pytest_asyncio.fixture(loop_scope="session")
async def person_id(db):
    pid = generate_id()
    await db.execute("INSERT INTO people (id) VALUES ($1)", pid)
    await db.execute(
        "INSERT INTO person_names (id, person_id, name, is_canonical)"
        " VALUES ($1, $2, 'Inline Person', TRUE)",
        generate_id(),
        pid,
    )
    yield pid
    await db.execute("DELETE FROM person_names WHERE person_id = $1", pid)
    await db.execute("DELETE FROM people WHERE id = $1", pid)


@pytest_asyncio.fixture(loop_scope="session")
async def role_id(db, org_id):
    rid = generate_id()
    await db.execute(
        "INSERT INTO roles (id, organization_id, title) VALUES ($1, $2, 'Inline Role')",
        rid,
        org_id,
    )
    yield rid
    await db.execute("DELETE FROM role_assignments WHERE role_id = $1", rid)
    await db.execute("DELETE FROM roles WHERE id = $1", rid)


@pytest_asyncio.fixture(loop_scope="session")
async def ra_id(db, person_id, role_id):
    raid = generate_id()
    await db.execute(
        "INSERT INTO role_assignments (id, person_id, role_id, is_current, notes)"
        " VALUES ($1, $2, $3, TRUE, 'original notes')",
        raid,
        person_id,
        role_id,
    )
    yield raid
    await db.execute("DELETE FROM role_assignments WHERE id = $1", raid)


# ---------------------------------------------------------------------------
# is_current toggle
# ---------------------------------------------------------------------------


async def test_is_current_toggle_off(client, ra_id):
    """POST with no value = unchecked = is_current set to FALSE."""
    response = client.post(
        f"/admin/role-assignments/{ra_id}/inline/is_current/",
        headers={**AUTH_HEADERS, "HX-Request": "true"},
        data={},
    )
    assert response.status_code == 200
    assert "Former" in response.text or "badge--inactive" in response.text


async def test_is_current_toggle_on(client, db, ra_id):
    """POST with value=true = checked = is_current set to TRUE."""
    await db.execute("UPDATE role_assignments SET is_current=FALSE WHERE id=$1", ra_id)
    response = client.post(
        f"/admin/role-assignments/{ra_id}/inline/is_current/",
        headers={**AUTH_HEADERS, "HX-Request": "true"},
        data={"is_current": "true"},
    )
    assert response.status_code == 200
    assert "Current" in response.text or "badge--active" in response.text


async def test_is_current_toggle_rejects_when_end_date_set(client, db, ra_id):
    """Flipping is_current=true with end_date set violates CHECK; re-render prior state + error."""
    await db.execute(
        "UPDATE role_assignments SET is_current=FALSE, end_date='2024-01-01' WHERE id=$1",
        ra_id,
    )
    response = client.post(
        f"/admin/role-assignments/{ra_id}/inline/is_current/",
        headers={**AUTH_HEADERS, "HX-Request": "true"},
        data={"is_current": "true"},
    )
    assert response.status_code == 200
    # flash_trigger carries error; toggle re-rendered as unchecked
    assert "HX-Trigger" in response.headers
    assert (
        "error" in response.headers["HX-Trigger"].lower()
        or "end date" in response.headers["HX-Trigger"].lower()
    )
    row = await db.fetchrow("SELECT is_current FROM role_assignments WHERE id=$1", ra_id)
    assert row["is_current"] is False


async def test_is_current_non_htmx_end_date_set_returns_400(client, db, ra_id):
    """Non-HTMX CHECK violation on is_current raises 400 instead of silent redirect."""
    await db.execute(
        "UPDATE role_assignments SET is_current=FALSE, end_date='2024-01-01' WHERE id=$1",
        ra_id,
    )
    response = client.post(
        f"/admin/role-assignments/{ra_id}/inline/is_current/",
        headers=AUTH_HEADERS,
        data={"is_current": "true"},
        follow_redirects=False,
    )
    assert response.status_code == 400
    assert "end date" in response.text.lower()


async def test_is_current_toggle_disabled_when_archived(client, db, ra_id):
    """Archived RA: toggle rendered with `disabled` attribute in the detail page."""
    await db.execute("UPDATE role_assignments SET archived_at=NOW() WHERE id=$1", ra_id)
    response = client.get(f"/admin/role-assignments/{ra_id}/", headers=AUTH_HEADERS)
    assert response.status_code == 200
    # disabled attribute present on the is_current checkbox
    assert 'name="is_current"' in response.text
    assert "disabled" in response.text


# ---------------------------------------------------------------------------
# Dates inline
# ---------------------------------------------------------------------------


async def test_dates_read_partial(client, ra_id):
    response = client.get(f"/admin/role-assignments/{ra_id}/inline/dates/", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert "dates-field" in response.text


async def test_dates_edit_partial(client, ra_id):
    response = client.get(
        f"/admin/role-assignments/{ra_id}/inline/dates/edit/", headers=AUTH_HEADERS
    )
    assert response.status_code == 200
    assert 'name="start_date"' in response.text
    assert 'name="end_date"' in response.text


async def test_dates_post_updates_start_date(client, db, ra_id):
    await db.execute("UPDATE role_assignments SET is_current=FALSE WHERE id=$1", ra_id)
    response = client.post(
        f"/admin/role-assignments/{ra_id}/inline/dates/",
        headers={**AUTH_HEADERS, "HX-Request": "true"},
        data={"start_date": "2020-01-01", "end_date": "2021-12-31"},
    )
    assert response.status_code == 200
    row = await db.fetchrow("SELECT start_date, end_date FROM role_assignments WHERE id=$1", ra_id)
    assert str(row["start_date"]) == "2020-01-01"
    assert str(row["end_date"]) == "2021-12-31"


async def test_dates_post_rejects_end_date_when_current(client, ra_id):
    """CHECK violation: is_current=true with end_date set → inline error re-render."""
    response = client.post(
        f"/admin/role-assignments/{ra_id}/inline/dates/",
        headers={**AUTH_HEADERS, "HX-Request": "true"},
        data={"start_date": "2020-01-01", "end_date": "2021-12-31"},
    )
    assert response.status_code == 200
    assert "alert--error" in response.text
    # repopulated values
    assert "2020-01-01" in response.text
    assert "2021-12-31" in response.text


async def test_dates_non_htmx_check_violation_returns_400(client, ra_id):
    """Non-HTMX CHECK violation on dates raises 400 instead of silent redirect."""
    response = client.post(
        f"/admin/role-assignments/{ra_id}/inline/dates/",
        headers=AUTH_HEADERS,
        data={"start_date": "2020-01-01", "end_date": "2021-12-31"},
        follow_redirects=False,
    )
    assert response.status_code == 400
    assert "end date" in response.text.lower()


async def test_dates_edit_hidden_when_archived(client, db, ra_id):
    """Archived RA: edit button on dates read partial is hidden."""
    await db.execute("UPDATE role_assignments SET archived_at=NOW() WHERE id=$1", ra_id)
    response = client.get(f"/admin/role-assignments/{ra_id}/inline/dates/", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert "dates/edit/" not in response.text


# ---------------------------------------------------------------------------
# Notes inline
# ---------------------------------------------------------------------------


async def test_notes_read_partial(client, ra_id):
    response = client.get(f"/admin/role-assignments/{ra_id}/inline/notes/", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert "notes-field" in response.text
    assert "original notes" in response.text


async def test_notes_edit_partial(client, ra_id):
    response = client.get(
        f"/admin/role-assignments/{ra_id}/inline/notes/edit/", headers=AUTH_HEADERS
    )
    assert response.status_code == 200
    assert 'id="notes-textarea"' in response.text


async def test_notes_post_saves(client, db, ra_id):
    response = client.post(
        f"/admin/role-assignments/{ra_id}/inline/notes/",
        headers={**AUTH_HEADERS, "HX-Request": "true"},
        data={"notes": "new notes here"},
    )
    assert response.status_code == 200
    row = await db.fetchrow("SELECT notes FROM role_assignments WHERE id=$1", ra_id)
    assert row["notes"] == "new notes here"


async def test_notes_post_whitespace_to_null(client, db, ra_id):
    client.post(
        f"/admin/role-assignments/{ra_id}/inline/notes/",
        headers={**AUTH_HEADERS, "HX-Request": "true"},
        data={"notes": "   "},
    )
    row = await db.fetchrow("SELECT notes FROM role_assignments WHERE id=$1", ra_id)
    assert row["notes"] is None


async def test_notes_edit_hidden_when_archived(client, db, ra_id):
    await db.execute("UPDATE role_assignments SET archived_at=NOW() WHERE id=$1", ra_id)
    response = client.get(f"/admin/role-assignments/{ra_id}/inline/notes/", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert "notes/edit/" not in response.text


# ---------------------------------------------------------------------------
# /edit/ routes removed
# ---------------------------------------------------------------------------


async def test_legacy_edit_page_removed(client, ra_id):
    """Full-page /edit/ form routes are gone."""
    response = client.get(f"/admin/role-assignments/{ra_id}/edit/", headers=AUTH_HEADERS)
    assert response.status_code == 404


async def test_legacy_edit_post_removed(client, ra_id, person_id, role_id):
    response = client.post(
        f"/admin/role-assignments/{ra_id}/edit/",
        headers=AUTH_HEADERS,
        data={
            "person_id": person_id,
            "role_id": role_id,
            "is_current": "true",
            "start_date": "",
            "end_date": "",
            "notes": "",
        },
    )
    assert response.status_code == 404


async def test_detail_has_no_edit_link(client, ra_id):
    """Page header Edit button is gone."""
    response = client.get(f"/admin/role-assignments/{ra_id}/", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert f"/admin/role-assignments/{ra_id}/edit/" not in response.text
