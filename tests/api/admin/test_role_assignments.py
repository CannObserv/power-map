"""Integration tests for admin role assignments views."""

import json

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
    "X-ExeDev-UserID": "usr_test",
    "X-ExeDev-Email": "admin@test.com",
}


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


@pytest_asyncio.fixture(loop_scope="session")
async def org_id(db):
    oid = generate_id()
    await db.execute("INSERT INTO organizations (id) VALUES ($1)", oid)
    await db.execute(
        "INSERT INTO organization_names (id, organization_id, name, is_canonical)"
        " VALUES ($1, $2, 'Test Org', TRUE)",
        generate_id(),
        oid,
    )
    yield oid


@pytest_asyncio.fixture(loop_scope="session")
async def person_id(db):
    pid = generate_id()
    await db.execute("INSERT INTO people (id) VALUES ($1)", pid)
    await db.execute(
        "INSERT INTO person_names (id, person_id, name, is_canonical)"
        " VALUES ($1, $2, 'Test Person', TRUE)",
        generate_id(),
        pid,
    )
    yield pid


@pytest_asyncio.fixture(loop_scope="session")
async def role_id(db, org_id):
    rid = generate_id()
    await db.execute(
        "INSERT INTO roles (id, organization_id, title) VALUES ($1, $2, 'Test Role')",
        rid,
        org_id,
    )
    yield rid


@pytest_asyncio.fixture(loop_scope="session")
async def ra_id(db, person_id, role_id):
    raid = generate_id()
    await db.execute(
        "INSERT INTO role_assignments (id, person_id, role_id, is_current)"
        " VALUES ($1, $2, $3, TRUE)",
        raid,
        person_id,
        role_id,
    )
    yield raid


async def test_role_assignments_list_returns_200(client):
    response = await client.get("/admin/role-assignments/", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert "assignment" in response.text.lower()


async def test_role_assignments_list_redirects_unauthenticated(client):
    response = await client.get("/admin/role-assignments/", follow_redirects=False)
    assert response.status_code in (302, 307)
    assert "/__exe.dev/login" in response.headers["location"]


async def test_ra_detail_returns_200(client, ra_id):
    response = await client.get(f"/admin/role-assignments/{ra_id}/", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert "Test Person" in response.text


async def test_ra_detail_404_for_unknown(client):
    response = await client.get(f"/admin/role-assignments/{generate_id()}/", headers=AUTH_HEADERS)
    assert response.status_code == 404


async def test_create_ra_post_redirects(client, person_id, role_id):
    response = await client.post(
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


async def test_create_ra_with_is_current_and_end_date_returns_error(client, person_id, role_id):
    response = await client.post(
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


async def test_archive_ra(client, ra_id):
    response = await client.post(
        f"/admin/role-assignments/{ra_id}/archive/",
        headers=AUTH_HEADERS,
        follow_redirects=False,
    )
    assert response.status_code in (302, 303)


async def test_archive_ra_redirects_with_flash_query(client, ra_id):
    """Archive redirects to detail with ?flash=archived so the detail view can render a flash."""
    response = await client.post(
        f"/admin/role-assignments/{ra_id}/archive/",
        headers=AUTH_HEADERS,
        follow_redirects=False,
    )
    assert response.status_code in (302, 303)
    assert response.headers["location"] == f"/admin/role-assignments/{ra_id}/?flash=archived"


async def test_archived_flash_renders_on_detail(client, ra_id):
    """Detail page with ?flash=archived renders the archived success flash."""
    response = await client.get(
        f"/admin/role-assignments/{ra_id}/?flash=archived", headers=AUTH_HEADERS
    )
    assert response.status_code == 200
    assert "Assignment archived." in response.text
    assert "flash--success" in response.text
    assert response.headers.get("HX-Replace-Url", "").endswith(f"/admin/role-assignments/{ra_id}/")


async def test_deleted_flash_renders_on_list(client):
    """List with ?flash=deleted renders the deleted success flash."""
    response = await client.get("/admin/role-assignments/?flash=deleted", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert "Assignment deleted." in response.text
    assert "flash--success" in response.text


async def test_archive_already_archived_returns_409(client, db, ra_id):
    """Re-archiving an already-archived row is rejected with 409 (idempotency guard)."""
    await db.execute("UPDATE role_assignments SET archived_at = NOW() WHERE id = $1", ra_id)
    response = await client.post(
        f"/admin/role-assignments/{ra_id}/archive/",
        headers=AUTH_HEADERS,
        follow_redirects=False,
    )
    assert response.status_code == 409
    assert response.json()["detail"] == "Role assignment is already archived"


async def test_unarchive_ra(client, db, ra_id):
    """Unarchiving a role assignment clears archived_at and redirects to detail."""
    await db.execute("UPDATE role_assignments SET archived_at = NOW() WHERE id = $1", ra_id)
    response = await client.post(
        f"/admin/role-assignments/{ra_id}/unarchive/",
        headers=AUTH_HEADERS,
        follow_redirects=False,
    )
    assert response.status_code in (302, 303)
    assert response.headers["location"] == f"/admin/role-assignments/{ra_id}/?flash=unarchived"
    row = await db.fetchrow("SELECT archived_at FROM role_assignments WHERE id = $1", ra_id)
    assert row["archived_at"] is None


async def test_archive_and_unarchive_ra_htmx_return_hx_location(client, db, ra_id):
    """HTMX archive/unarchive return 204 + HX-Location to detail with flash (#287).

    The Danger Zone controls are bare ``hx-post`` buttons, so both handlers must
    carry the ``is_htmx`` branch — a bare 303 would be followed by htmx into a
    ``hx-swap="none"`` and leave the page stale.
    """
    hx = {**AUTH_HEADERS, "HX-Request": "true"}
    archived = await client.post(f"/admin/role-assignments/{ra_id}/archive/", headers=hx)
    assert archived.status_code == 204
    assert archived.headers["HX-Location"] == f"/admin/role-assignments/{ra_id}/?flash=archived"
    assert (
        await db.fetchval("SELECT archived_at FROM role_assignments WHERE id = $1", ra_id)
        is not None
    )

    restored = await client.post(f"/admin/role-assignments/{ra_id}/unarchive/", headers=hx)
    assert restored.status_code == 204
    assert restored.headers["HX-Location"] == f"/admin/role-assignments/{ra_id}/?flash=unarchived"
    assert (
        await db.fetchval("SELECT archived_at FROM role_assignments WHERE id = $1", ra_id) is None
    )


async def test_unarchive_ra_not_found_returns_404(client):
    """Unarchiving a non-existent RA returns 404."""
    response = await client.post(
        "/admin/role-assignments/nonexistent/unarchive/",
        headers=AUTH_HEADERS,
        follow_redirects=False,
    )
    assert response.status_code == 404


async def test_unarchive_ra_not_archived_returns_409(client, db, ra_id):
    """Unarchiving an active (non-archived) RA returns 409."""
    response = await client.post(
        f"/admin/role-assignments/{ra_id}/unarchive/",
        headers=AUTH_HEADERS,
        follow_redirects=False,
    )
    assert response.status_code == 409
    assert response.json()["detail"] == "Role assignment is not archived"


async def test_unarchive_ra_identity_collision_htmx_flashes_warning(client, db, ra_id, person_id):
    """An identity slot taken while archived rejects with a flash, not a 500 (#424).

    ``uq_role_assignment_person_role_start`` is partial on ``archived_at IS
    NULL``, so archiving an assignment frees its (person, role, start_date)
    slot for a new one. Restoring the old one then violates the index.
    """
    role_of = await db.fetchval("SELECT role_id FROM role_assignments WHERE id = $1", ra_id)
    await db.execute("UPDATE role_assignments SET archived_at = NOW() WHERE id = $1", ra_id)
    await db.execute(
        "INSERT INTO role_assignments (id, person_id, role_id, is_current)"
        " VALUES ($1, $2, $3, TRUE)",
        generate_id(),
        person_id,
        role_of,
    )
    response = await client.post(
        f"/admin/role-assignments/{ra_id}/unarchive/",
        headers={**AUTH_HEADERS, "HX-Request": "true"},
    )
    assert response.status_code == 204
    assert "HX-Location" not in response.headers
    trigger = json.loads(response.headers["HX-Trigger"])
    assert trigger["showFlash"]["level"] == "warning"
    assert (
        await db.fetchval("SELECT archived_at FROM role_assignments WHERE id = $1", ra_id)
        is not None
    )


async def test_unarchive_ra_identity_collision_non_htmx_redirects_with_flash(
    client, db, ra_id, person_id
):
    """Non-HTMX collision redirects to detail with the shared ``exists`` flash key."""
    role_of = await db.fetchval("SELECT role_id FROM role_assignments WHERE id = $1", ra_id)
    await db.execute("UPDATE role_assignments SET archived_at = NOW() WHERE id = $1", ra_id)
    await db.execute(
        "INSERT INTO role_assignments (id, person_id, role_id, is_current)"
        " VALUES ($1, $2, $3, TRUE)",
        generate_id(),
        person_id,
        role_of,
    )
    response = await client.post(
        f"/admin/role-assignments/{ra_id}/unarchive/",
        headers=AUTH_HEADERS,
        follow_redirects=False,
    )
    assert response.status_code in (302, 303)
    assert response.headers["location"] == f"/admin/role-assignments/{ra_id}/?flash=exists"
    assert (
        await db.fetchval("SELECT archived_at FROM role_assignments WHERE id = $1", ra_id)
        is not None
    )


async def test_exists_flash_renders_on_ra_detail(client, ra_id):
    """The collision redirect's ``exists`` key resolves on the assignment detail page."""
    response = await client.get(
        f"/admin/role-assignments/{ra_id}/?flash=exists", headers=AUTH_HEADERS
    )
    assert response.status_code == 200
    assert "That already exists." in response.text
    assert "flash--warning" in response.text


async def test_unarchived_flash_renders_on_detail(client, ra_id):
    """Detail page with ?flash=unarchived renders the unarchived success flash."""
    response = await client.get(
        f"/admin/role-assignments/{ra_id}/?flash=unarchived", headers=AUTH_HEADERS
    )
    assert response.status_code == 200
    assert "Assignment unarchived." in response.text
    assert "flash--success" in response.text
    assert "HX-Replace-Url" in response.headers
    assert "flash" not in response.headers["HX-Replace-Url"]


async def test_detail_unknown_flash_key_ignored(client, ra_id):
    """GET detail with ?flash=bogus returns 200 with no flash and no HX-Replace-Url."""
    response = await client.get(
        f"/admin/role-assignments/{ra_id}/?flash=bogus", headers=AUTH_HEADERS
    )
    assert response.status_code == 200
    assert "flash--success" not in response.text
    assert "HX-Replace-Url" not in response.headers


async def test_detail_shows_unarchive_button_when_archived(client, db, ra_id):
    """Detail page for an archived RA shows an Unarchive button."""
    await db.execute("UPDATE role_assignments SET archived_at = NOW() WHERE id = $1", ra_id)
    response = await client.get(f"/admin/role-assignments/{ra_id}/", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert f"/admin/role-assignments/{ra_id}/unarchive/" in response.text
    assert "Unarchive" in response.text


async def test_hard_delete_requires_archive(client, ra_id):
    response = await client.delete(f"/admin/role-assignments/{ra_id}/", headers=AUTH_HEADERS)
    assert response.status_code == 409


async def test_hard_delete_archived_ra(client, db, ra_id):
    """Delete → list must use HX-Redirect (full browser navigation), not HX-Location.

    HX-Location fires a client-side ``htmx.ajax`` GET that carries ``HX-Request``,
    so the list route returns its ``_region.html`` partial and HTMX swaps the
    table-only fragment into ``<body>`` — no header/nav (#376). HX-Redirect does a
    real ``window.location`` navigation → clean GET → full ``list.html``.
    """
    await db.execute("UPDATE role_assignments SET archived_at = NOW() WHERE id = $1", ra_id)
    response = await client.delete(
        f"/admin/role-assignments/{ra_id}/",
        headers={**AUTH_HEADERS, "HX-Request": "true"},
    )
    assert response.status_code == 200
    assert response.headers.get("HX-Redirect") == "/admin/role-assignments/?flash=deleted"
    assert "HX-Location" not in response.headers


async def test_hard_delete_archived_ra_writes_tombstone(client, db, ra_id):
    """Hard delete of an archived role assignment writes a deleted_entities
    tombstone and propagates a 'deleted' entity_changes row (issue #277)."""
    await db.execute("UPDATE role_assignments SET archived_at = NOW() WHERE id = $1", ra_id)
    response = await client.delete(
        f"/admin/role-assignments/{ra_id}/",
        headers=AUTH_HEADERS,
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert (
        await db.fetchval(
            "SELECT 1 FROM deleted_entities WHERE entity_type='role_assignment' AND entity_id=$1",
            ra_id,
        )
        == 1
    )
    assert (
        await db.fetchval(
            "SELECT 1 FROM entity_changes"
            " WHERE entity_type='role_assignment' AND entity_id=$1 AND change_kind='deleted'",
            ra_id,
        )
        == 1
    )


async def test_detail_delete_button_has_no_legacy_push_url(client, db, ra_id):
    """Delete button relies on the server HX-Redirect (#376), not hx-target/hx-push-url."""
    await db.execute("UPDATE role_assignments SET archived_at = NOW() WHERE id = $1", ra_id)
    response = await client.get(f"/admin/role-assignments/{ra_id}/", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert "Delete permanently" in response.text
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
        unique_name,
        org_id,
    )
    await db.execute(
        "INSERT INTO organization_acronyms (id, organization_id, acronym, is_canonical)"
        " VALUES ($1, $2, 'TO', TRUE)",
        generate_id(),
        org_id,
    )
    ra_id = generate_id()
    await db.execute(
        "INSERT INTO role_assignments (id, person_id, role_id, is_current)"
        " VALUES ($1, $2, $3, TRUE)",
        ra_id,
        person_id,
        role_id,
    )
    response = await client.get(f"/admin/role-assignments/?q={org_id}", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert f"{unique_name} (TO)" in response.text
    assert response.text.count(f'href="/admin/role-assignments/{ra_id}/"') == 1, (
        "assignment must appear exactly once"
    )


async def test_ra_list_htmx_boost_returns_full_page(client):
    """Boosted navigation must return the full page layout, not a bare rows partial."""
    response = await client.get(
        "/admin/role-assignments/",
        headers={**AUTH_HEADERS, "HX-Request": "true", "HX-Boosted": "true"},
    )
    assert response.status_code == 200
    assert "admin-layout" in response.text


async def test_ra_list_htmx_request_returns_rows_partial(client):
    """Non-boosted HTMX request (filter/pagination) must return the rows partial only."""
    response = await client.get(
        "/admin/role-assignments/",
        headers={**AUTH_HEADERS, "HX-Request": "true"},
    )
    assert response.status_code == 200
    assert "admin-layout" not in response.text


async def test_ra_list_shows_person_name(client, ra_id):
    """RA list must show canonical person name via v_person_display_names."""
    response = await client.get("/admin/role-assignments/", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert "Test Person" in response.text


async def test_ra_list_uses_composed_format(client, ra_id):
    """List row must render 'Person – Role @ Org' (issue #98)."""
    response = await client.get("/admin/role-assignments/", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert "Test Person \u2013 Test Role @ Test Org" in response.text
    assert "@ Test Role (Test Org)" not in response.text


async def test_ra_detail_uses_composed_format(client, ra_id):
    """Detail h1 and <title> must render 'Person – Role @ Org' (issue #98)."""
    response = await client.get(f"/admin/role-assignments/{ra_id}/", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert "Test Person \u2013 Test Role @ Test Org" in response.text
    assert (
        "<title>Test Person \u2013 Test Role @ Test Org \u2014 Assignment \u2014 Power Map</title>"
        in response.text
    )
    assert "@ Test Role (Test Org)" not in response.text


async def test_ra_list_renders_unnamed_fallback_for_missing_person_name(client, db, role_id):
    """List must render '(unnamed) – Role @ Org' when person has no canonical name."""
    pid = generate_id()
    raid = generate_id()
    await db.execute("INSERT INTO people (id) VALUES ($1)", pid)
    await db.execute(
        "INSERT INTO role_assignments (id, person_id, role_id, is_current)"
        " VALUES ($1, $2, $3, TRUE)",
        raid,
        pid,
        role_id,
    )
    response = await client.get("/admin/role-assignments/", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert "(unnamed) \u2013 Test Role @ Test Org" in response.text


async def test_ra_detail_renders_unnamed_fallback_for_missing_org_name(client, db, person_id):
    """Detail h1 must render 'Person – Role @ (unnamed)' when org has no canonical name."""
    oid = generate_id()
    rid = generate_id()
    raid = generate_id()
    await db.execute("INSERT INTO organizations (id) VALUES ($1)", oid)
    await db.execute(
        "INSERT INTO roles (id, organization_id, title) VALUES ($1, $2, 'Nameless Role')",
        rid,
        oid,
    )
    await db.execute(
        "INSERT INTO role_assignments (id, person_id, role_id, is_current)"
        " VALUES ($1, $2, $3, TRUE)",
        raid,
        person_id,
        rid,
    )
    response = await client.get(f"/admin/role-assignments/{raid}/", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert "Test Person \u2013 Nameless Role @ (unnamed)" in response.text


async def test_ra_list_search_matches_person_name(client, ra_id):
    """Search filter must match against v_person_display_names.display_name."""
    response = await client.get("/admin/role-assignments/?q=Test+Person", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert "Test Person" in response.text


async def test_ra_list_search_no_match_excludes_person_name(client, ra_id):
    """Non-matching search must not return rows for the person."""
    response = await client.get("/admin/role-assignments/?q=NoSuchPersonXYZ", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert "Test Person" not in response.text


async def test_ra_detail_shows_person_name(client, ra_id):
    """RA detail must show canonical person name via v_person_display_names."""
    response = await client.get(f"/admin/role-assignments/{ra_id}/", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert "Test Person" in response.text


async def test_ra_form_shows_person_in_dropdown(client, person_id):
    """New RA form must include canonical person name in the people dropdown."""
    response = await client.get("/admin/role-assignments/new/", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert "Test Person" in response.text


async def test_ra_detail_uses_entity_section_wrapper(client, ra_id):
    """Detail layout must wrap content in <section class="entity-section">."""
    response = await client.get(f"/admin/role-assignments/{ra_id}/", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert 'class="entity-section"' in response.text


async def test_ra_detail_drops_detail_grid_dl(client, ra_id):
    """Legacy <dl class="detail-grid"> must be gone."""
    response = await client.get(f"/admin/role-assignments/{ra_id}/", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert "detail-grid" not in response.text


async def test_ra_detail_metadata_footer(client, ra_id):
    """Metadata footer must render ID + Created muted line (not a grid row)."""
    response = await client.get(f"/admin/role-assignments/{ra_id}/", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert "Metadata" in response.text
    assert f"<code>{ra_id}</code>" in response.text


async def test_ra_detail_status_field_group_label(client, ra_id):
    """Status renders in a field-group-label row, not a <dt>."""
    response = await client.get(f"/admin/role-assignments/{ra_id}/", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert 'class="field-group-label"' in response.text
    assert "Status" in response.text


async def test_list_shows_citations_indicator(client, db, ra_id):
    """#341: assignments list rows surface active-citation counts."""
    await db.execute(
        "INSERT INTO citations (id, entity_type, entity_id, url)"
        " VALUES ($1, 'role_assignment', $2, 'https://example.com/a')",
        generate_id(),
        ra_id,
    )
    response = await client.get("/admin/role-assignments/", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert 'class="citation-indicator"' in response.text
    assert 'aria-label="1 citation"' in response.text


async def test_list_omits_citations_indicator_when_none(client, ra_id):
    response = await client.get("/admin/role-assignments/", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert "citation-indicator" not in response.text
