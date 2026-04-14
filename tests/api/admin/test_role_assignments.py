"""Integration tests for admin role assignments views."""

import os

import asyncpg
import pytest
from fastapi.testclient import TestClient

from src.api.main import app
from src.core.db import apply_schema, generate_id

pytestmark = pytest.mark.integration

AUTH_HEADERS = {
    "X-ExeDev-UserID": "usr_test",
    "X-ExeDev-Email": "admin@test.com",
}


@pytest.fixture
async def db():
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        pytest.skip("DATABASE_URL not set")
    conn = await asyncpg.connect(dsn)
    try:
        await apply_schema(conn)
        yield conn
    finally:
        await conn.close()


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture
async def org_id(db):
    oid = generate_id()
    await db.execute("INSERT INTO organizations (id) VALUES ($1)", oid)
    await db.execute(
        "INSERT INTO organization_names (id, organization_id, name, is_canonical)"
        " VALUES ($1, $2, 'Test Org', TRUE)",
        generate_id(), oid,
    )
    yield oid
    await db.execute("DELETE FROM organization_acronyms WHERE organization_id = $1", oid)
    await db.execute("DELETE FROM organization_names WHERE organization_id = $1", oid)
    await db.execute("DELETE FROM organizations WHERE id = $1", oid)


@pytest.fixture
async def person_id(db):
    pid = generate_id()
    await db.execute("INSERT INTO people (id) VALUES ($1)", pid)
    await db.execute(
        "INSERT INTO person_names (id, person_id, name, is_canonical)"
        " VALUES ($1, $2, 'Test Person', TRUE)",
        generate_id(), pid,
    )
    yield pid
    await db.execute("DELETE FROM person_names WHERE person_id = $1", pid)
    await db.execute("DELETE FROM people WHERE id = $1", pid)


@pytest.fixture
async def role_id(db, org_id):
    rid = generate_id()
    await db.execute(
        "INSERT INTO roles (id, organization_id, title) VALUES ($1, $2, 'Test Role')",
        rid, org_id,
    )
    yield rid
    await db.execute("DELETE FROM role_assignments WHERE role_id = $1", rid)
    await db.execute("DELETE FROM roles WHERE id = $1", rid)


@pytest.fixture
async def ra_id(db, person_id, role_id):
    raid = generate_id()
    await db.execute(
        "INSERT INTO role_assignments (id, person_id, role_id, is_current)"
        " VALUES ($1, $2, $3, TRUE)",
        raid, person_id, role_id,
    )
    yield raid
    await db.execute("DELETE FROM role_assignments WHERE id = $1", raid)


def test_role_assignments_list_returns_200(client):
    response = client.get("/admin/role-assignments/", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert "assignment" in response.text.lower()


def test_role_assignments_list_redirects_unauthenticated(client):
    response = client.get("/admin/role-assignments/", follow_redirects=False)
    assert response.status_code in (302, 307)
    assert "/__exe.dev/login" in response.headers["location"]


def test_ra_detail_returns_200(client, ra_id):
    response = client.get(f"/admin/role-assignments/{ra_id}/", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert "Test Person" in response.text


def test_ra_detail_404_for_unknown(client):
    response = client.get(f"/admin/role-assignments/{generate_id()}/", headers=AUTH_HEADERS)
    assert response.status_code == 404


def test_create_ra_post_redirects(client, person_id, role_id):
    response = client.post(
        "/admin/role-assignments/new/",
        headers=AUTH_HEADERS,
        data={
            "person_id": person_id,
            "role_id": role_id,
            "is_current": "true",
            "start_date": "",
            "end_date": "",
            "notes": "",
        },
        follow_redirects=False,
    )
    assert response.status_code in (302, 303)


def test_create_ra_with_is_current_and_end_date_returns_error(client, person_id, role_id):
    response = client.post(
        "/admin/role-assignments/new/",
        headers=AUTH_HEADERS,
        data={
            "person_id": person_id,
            "role_id": role_id,
            "is_current": "true",
            "start_date": "",
            "end_date": "2024-01-01",
            "notes": "",
        },
        follow_redirects=False,
    )
    # Should re-render form with error (200) or return 422
    assert response.status_code in (200, 422)
    if response.status_code == 200:
        assert "end date" in response.text.lower() or "current" in response.text.lower()


def test_archive_ra(client, ra_id):
    response = client.post(
        f"/admin/role-assignments/{ra_id}/archive/",
        headers=AUTH_HEADERS,
        follow_redirects=False,
    )
    assert response.status_code in (302, 303)


def test_archive_ra_redirects_with_flash_query(client, ra_id):
    """Archive redirects to detail with ?flash=archived so the detail view can render a flash."""
    response = client.post(
        f"/admin/role-assignments/{ra_id}/archive/",
        headers=AUTH_HEADERS,
        follow_redirects=False,
    )
    assert response.status_code in (302, 303)
    assert response.headers["location"] == f"/admin/role-assignments/{ra_id}/?flash=archived"


def test_archived_flash_renders_on_detail(client, ra_id):
    """Detail page with ?flash=archived renders the archived success flash."""
    response = client.get(
        f"/admin/role-assignments/{ra_id}/?flash=archived", headers=AUTH_HEADERS
    )
    assert response.status_code == 200
    assert "Assignment archived." in response.text
    assert "flash--success" in response.text
    assert response.headers.get("HX-Replace-Url", "").endswith(
        f"/admin/role-assignments/{ra_id}/"
    )


def test_deleted_flash_renders_on_list(client):
    """List with ?flash=deleted renders the deleted success flash."""
    response = client.get(
        "/admin/role-assignments/?flash=deleted", headers=AUTH_HEADERS
    )
    assert response.status_code == 200
    assert "Assignment deleted." in response.text
    assert "flash--success" in response.text


def test_hard_delete_requires_archive(client, ra_id):
    response = client.delete(f"/admin/role-assignments/{ra_id}/", headers=AUTH_HEADERS)
    assert response.status_code == 409


async def test_hard_delete_archived_ra(client, db, ra_id):
    await db.execute("UPDATE role_assignments SET archived_at = NOW() WHERE id = $1", ra_id)
    response = client.delete(
        f"/admin/role-assignments/{ra_id}/",
        headers={**AUTH_HEADERS, "HX-Request": "true"},
    )
    assert response.status_code == 204
    assert response.headers.get("HX-Location") == "/admin/role-assignments/?flash=deleted"


async def test_detail_delete_button_has_no_legacy_push_url(client, db, ra_id):
    """Delete button relies on server HX-Location redirect, not hx-target/hx-push-url."""
    await db.execute(
        "UPDATE role_assignments SET archived_at = NOW() WHERE id = $1", ra_id
    )
    response = client.get(f"/admin/role-assignments/{ra_id}/", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert 'Delete permanently' in response.text
    assert 'hx-target="body"' not in response.text
    assert "hx-push-url" not in response.text


async def test_ra_list_shows_formatted_org_name_for_org_with_acronym(
    client, db, org_id, person_id, role_id
):
    """List must show 'Name (Acronym)' and no duplicate rows.

    Validates that both _LIST_SELECT (SELECT clause) and the ra_list WHERE condition
    use the correct dn.display_name alias after the refactor.
    """
    # Use org_id as unique suffix so this search matches only this test's data.
    unique_name = f"UniqueOrg {org_id}"
    await db.execute(
        "UPDATE organization_names SET name = $1"
        " WHERE organization_id = $2 AND is_canonical = TRUE",
        unique_name, org_id,
    )
    await db.execute(
        "INSERT INTO organization_acronyms (id, organization_id, acronym, is_canonical)"
        " VALUES ($1, $2, 'TO', TRUE)",
        generate_id(), org_id,
    )
    ra_id = generate_id()
    await db.execute(
        "INSERT INTO role_assignments (id, person_id, role_id, is_current)"
        " VALUES ($1, $2, $3, TRUE)",
        ra_id, person_id, role_id,
    )
    try:
        response = client.get(
            f"/admin/role-assignments/?q={org_id}", headers=AUTH_HEADERS
        )
        assert response.status_code == 200
        assert f"{unique_name} (TO)" in response.text
        assert response.text.count(f'href="/admin/role-assignments/{ra_id}/"') == 1, \
            "assignment must appear exactly once"
    finally:
        await db.execute(
            "DELETE FROM organization_acronyms WHERE organization_id = $1",
            org_id,
        )


def test_ra_list_htmx_boost_returns_full_page(client):
    """Boosted navigation must return the full page layout, not a bare rows partial."""
    response = client.get(
        "/admin/role-assignments/",
        headers={**AUTH_HEADERS, "HX-Request": "true", "HX-Boosted": "true"},
    )
    assert response.status_code == 200
    assert "admin-layout" in response.text


def test_ra_list_htmx_request_returns_rows_partial(client):
    """Non-boosted HTMX request (filter/pagination) must return the rows partial only."""
    response = client.get(
        "/admin/role-assignments/",
        headers={**AUTH_HEADERS, "HX-Request": "true"},
    )
    assert response.status_code == 200
    assert "admin-layout" not in response.text


def test_ra_list_shows_person_name(client, ra_id):
    """RA list must show canonical person name via v_person_display_names."""
    response = client.get("/admin/role-assignments/", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert "Test Person" in response.text


def test_ra_list_uses_composed_format(client, ra_id):
    """List row must render 'Person – Role @ Org' (issue #98)."""
    response = client.get("/admin/role-assignments/", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert "Test Person \u2013 Test Role @ Test Org" in response.text
    assert "@ Test Role (Test Org)" not in response.text


def test_ra_detail_uses_composed_format(client, ra_id):
    """Detail h1 and <title> must render 'Person – Role @ Org' (issue #98)."""
    response = client.get(f"/admin/role-assignments/{ra_id}/", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert "Test Person \u2013 Test Role @ Test Org" in response.text
    assert (
        "<title>Test Person \u2013 Test Role @ Test Org \u2014 Assignment \u2014 Power Map</title>"
        in response.text
    )
    assert "@ Test Role (Test Org)" not in response.text


async def test_ra_list_renders_unnamed_fallback_for_missing_person_name(
    client, db, role_id
):
    """List must render '(unnamed) – Role @ Org' when person has no canonical name."""
    pid = generate_id()
    raid = generate_id()
    await db.execute("INSERT INTO people (id) VALUES ($1)", pid)
    await db.execute(
        "INSERT INTO role_assignments (id, person_id, role_id, is_current)"
        " VALUES ($1, $2, $3, TRUE)",
        raid, pid, role_id,
    )
    try:
        response = client.get("/admin/role-assignments/", headers=AUTH_HEADERS)
        assert response.status_code == 200
        assert "(unnamed) \u2013 Test Role @ Test Org" in response.text
    finally:
        await db.execute("DELETE FROM role_assignments WHERE id = $1", raid)
        await db.execute("DELETE FROM people WHERE id = $1", pid)


async def test_ra_detail_renders_unnamed_fallback_for_missing_org_name(
    client, db, person_id
):
    """Detail h1 must render 'Person – Role @ (unnamed)' when org has no canonical name."""
    oid = generate_id()
    rid = generate_id()
    raid = generate_id()
    await db.execute("INSERT INTO organizations (id) VALUES ($1)", oid)
    await db.execute(
        "INSERT INTO roles (id, organization_id, title) VALUES ($1, $2, 'Nameless Role')",
        rid, oid,
    )
    await db.execute(
        "INSERT INTO role_assignments (id, person_id, role_id, is_current)"
        " VALUES ($1, $2, $3, TRUE)",
        raid, person_id, rid,
    )
    try:
        response = client.get(f"/admin/role-assignments/{raid}/", headers=AUTH_HEADERS)
        assert response.status_code == 200
        assert "Test Person \u2013 Nameless Role @ (unnamed)" in response.text
    finally:
        await db.execute("DELETE FROM role_assignments WHERE id = $1", raid)
        await db.execute("DELETE FROM roles WHERE id = $1", rid)
        await db.execute("DELETE FROM organizations WHERE id = $1", oid)


def test_ra_list_search_matches_person_name(client, ra_id):
    """Search filter must match against v_person_display_names.display_name."""
    response = client.get(
        "/admin/role-assignments/?q=Test+Person", headers=AUTH_HEADERS
    )
    assert response.status_code == 200
    assert "Test Person" in response.text


def test_ra_list_search_no_match_excludes_person_name(client, ra_id):
    """Non-matching search must not return rows for the person."""
    response = client.get(
        "/admin/role-assignments/?q=NoSuchPersonXYZ", headers=AUTH_HEADERS
    )
    assert response.status_code == 200
    assert "Test Person" not in response.text


def test_ra_detail_shows_person_name(client, ra_id):
    """RA detail must show canonical person name via v_person_display_names."""
    response = client.get(f"/admin/role-assignments/{ra_id}/", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert "Test Person" in response.text


def test_ra_form_shows_person_in_dropdown(client, person_id):
    """New RA form must include canonical person name in the people dropdown."""
    response = client.get("/admin/role-assignments/new/", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert "Test Person" in response.text


def test_ra_detail_uses_entity_section_wrapper(client, ra_id):
    """Detail layout must wrap content in <section class="entity-section">."""
    response = client.get(f"/admin/role-assignments/{ra_id}/", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert 'class="entity-section"' in response.text


def test_ra_detail_drops_detail_grid_dl(client, ra_id):
    """Legacy <dl class="detail-grid"> must be gone."""
    response = client.get(f"/admin/role-assignments/{ra_id}/", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert 'detail-grid' not in response.text


def test_ra_detail_metadata_footer(client, ra_id):
    """Metadata footer must render ID + Created muted line (not a grid row)."""
    response = client.get(f"/admin/role-assignments/{ra_id}/", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert "Metadata" in response.text
    assert f"<code>{ra_id}</code>" in response.text


def test_ra_detail_status_field_group_label(client, ra_id):
    """Status renders in a field-group-label row, not a <dt>."""
    response = client.get(f"/admin/role-assignments/{ra_id}/", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert 'class="field-group-label"' in response.text
    assert "Status" in response.text
