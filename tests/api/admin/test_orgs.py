"""Integration tests for admin organizations views.

Uses the lifespan-less client pattern (#288): an ``AsyncClient`` over
``ASGITransport`` with ``get_db`` overridden to a single BEGIN/ROLLBACK-wrapped
connection from the session pool. No app lifespan → no per-test
``asyncpg.create_pool`` (~170 ms) and true rollback-per-test isolation, so data
fixtures need no manual teardown. Reference: ``test_orgs_detail_inline.py``.
"""

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
    """Insert an org, yield its ID. Rolled back with the test transaction."""
    oid = generate_id()
    await db.execute("INSERT INTO organizations (id) VALUES ($1)", oid)
    await db.execute(
        "INSERT INTO organization_names (id, organization_id, name, is_canonical)"
        " VALUES ($1, $2, 'Test Org', TRUE)",
        generate_id(),
        oid,
    )
    return oid


async def test_orgs_list_returns_200(client):
    response = await client.get("/admin/orgs/", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert "organizations" in response.text.lower()


async def test_orgs_list_redirects_unauthenticated(client):
    response = await client.get("/admin/orgs/", follow_redirects=False)
    assert response.status_code in (302, 307)
    assert "/__exe.dev/login" in response.headers["location"]


async def test_org_detail_returns_200(client, org_id):
    response = await client.get(f"/admin/orgs/{org_id}/", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert "Test Org" in response.text


async def test_org_detail_acronym_only_shows_acronym_in_heading(client, db):
    """Detail page for an org with only an acronym must show the acronym, not the raw ID."""
    oid = generate_id()
    await db.execute("INSERT INTO organizations (id) VALUES ($1)", oid)
    await db.execute(
        "INSERT INTO organization_acronyms (id, organization_id, acronym, is_canonical)"
        " VALUES ($1, $2, 'ACRO', TRUE)",
        generate_id(),
        oid,
    )

    response = await client.get(f"/admin/orgs/{oid}/", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert 'id="page-heading"' in response.text, "h1 must have id=page-heading"
    assert "ACRO" in response.text, "h1 must show acronym"


async def test_org_detail_404_for_unknown_id(client):
    response = await client.get(f"/admin/orgs/{generate_id()}/", headers=AUTH_HEADERS)
    assert response.status_code == 404


async def test_org_detail_has_email_and_phone_tables(client, org_id):
    r = await client.get(f"/admin/orgs/{org_id}/", headers=AUTH_HEADERS)
    assert r.status_code == 200
    assert 'id="emails-table"' in r.text
    assert 'id="phones-table"' in r.text


async def test_create_org_form_returns_200(client):
    response = await client.get("/admin/orgs/new/", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert "form" in response.text.lower()


async def test_create_org_post_redirects_on_success(client):
    response = await client.post(
        "/admin/orgs/new/",
        headers=AUTH_HEADERS,
        data={"name": "Test Create Org", "active": "true"},
        follow_redirects=False,
    )
    assert response.status_code in (302, 303)
    assert "/admin/orgs/" in response.headers["location"]


async def test_edit_route_removed(client):
    """GET /edit/ must return 404 — route has been deleted."""
    r = await client.get(f"/admin/orgs/{generate_id()}/edit/", headers=AUTH_HEADERS)
    assert r.status_code == 404


async def test_org_create_rejects_empty_name(client):
    """POST /admin/orgs/new/ with empty name must be rejected (422)."""
    r = await client.post(
        "/admin/orgs/new/",
        headers=AUTH_HEADERS,
        data={"name": "", "active": "true"},
        follow_redirects=False,
    )
    assert r.status_code == 422


async def test_org_create_rejects_whitespace_only_name(client):
    """POST /admin/orgs/new/ with whitespace-only name must be rejected (422)."""
    r = await client.post(
        "/admin/orgs/new/",
        headers=AUTH_HEADERS,
        data={"name": "   ", "active": "true"},
        follow_redirects=False,
    )
    assert r.status_code == 422


async def test_archive_org(client, org_id):
    response = await client.post(
        f"/admin/orgs/{org_id}/archive/",
        headers=AUTH_HEADERS,
        follow_redirects=False,
    )
    assert response.status_code in (302, 303)


async def test_archive_already_archived_org_returns_409(client, org_id, db):
    """Re-archiving an already-archived org is rejected with 409."""
    await db.execute("UPDATE organizations SET archived_at = NOW() WHERE id = $1", org_id)

    response = await client.post(
        f"/admin/orgs/{org_id}/archive/",
        headers=AUTH_HEADERS,
        follow_redirects=False,
    )
    assert response.status_code == 409
    assert response.json()["detail"] == "Organization is already archived"


async def test_archive_org_redirects_with_flash_query(client, org_id):
    """Archive redirects to detail with ?flash=archived."""
    response = await client.post(
        f"/admin/orgs/{org_id}/archive/",
        headers=AUTH_HEADERS,
        follow_redirects=False,
    )
    assert response.status_code in (302, 303)
    assert response.headers["location"] == f"/admin/orgs/{org_id}/?flash=archived"


async def test_archive_org_htmx_returns_hx_location(client, org_id):
    """HTMX archive returns 204 + HX-Location pointing at detail with flash."""
    response = await client.post(
        f"/admin/orgs/{org_id}/archive/",
        headers={**AUTH_HEADERS, "HX-Request": "true"},
    )
    assert response.status_code == 204
    assert response.headers["HX-Location"] == f"/admin/orgs/{org_id}/?flash=archived"


async def test_archive_already_archived_org_htmx_returns_409(client, org_id, db):
    """HTMX re-archive still guarded with 409."""
    await db.execute("UPDATE organizations SET archived_at = NOW() WHERE id = $1", org_id)
    response = await client.post(
        f"/admin/orgs/{org_id}/archive/",
        headers={**AUTH_HEADERS, "HX-Request": "true"},
    )
    assert response.status_code == 409


async def test_unarchive_org_htmx_returns_hx_location(client, org_id, db):
    """HTMX unarchive returns 204 + HX-Location pointing at detail with flash."""
    await db.execute("UPDATE organizations SET archived_at = NOW() WHERE id = $1", org_id)
    response = await client.post(
        f"/admin/orgs/{org_id}/unarchive/",
        headers={**AUTH_HEADERS, "HX-Request": "true"},
    )
    assert response.status_code == 204
    assert response.headers["HX-Location"] == f"/admin/orgs/{org_id}/?flash=unarchived"


async def test_unarchive_org_htmx_rejects_non_archived(client, org_id):
    """HTMX unarchive of an active org still guarded with 409."""
    response = await client.post(
        f"/admin/orgs/{org_id}/unarchive/",
        headers={**AUTH_HEADERS, "HX-Request": "true"},
    )
    assert response.status_code == 409


async def test_archived_flash_renders_on_org_detail(client, org_id):
    """Org detail with ?flash=archived renders the success flash."""
    response = await client.get(f"/admin/orgs/{org_id}/?flash=archived", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert "Organization archived." in response.text
    assert "flash--success" in response.text
    assert "HX-Replace-Url" in response.headers
    assert "flash" not in response.headers["HX-Replace-Url"]


async def test_unarchive_org_clears_archived_at(client, org_id, db):
    await db.execute(
        "UPDATE organizations SET archived_at = NOW(), active = FALSE WHERE id = $1",
        org_id,
    )

    response = await client.post(
        f"/admin/orgs/{org_id}/unarchive/",
        headers=AUTH_HEADERS,
        follow_redirects=False,
    )
    assert response.status_code in (302, 303)

    row = await db.fetchrow("SELECT archived_at, active FROM organizations WHERE id = $1", org_id)
    assert row["archived_at"] is None
    assert row["active"] is False  # prior active state preserved


async def test_unarchive_org_redirects_with_flash_query(client, org_id, db):
    """Unarchive redirects to detail with ?flash=unarchived."""
    await db.execute("UPDATE organizations SET archived_at = NOW() WHERE id = $1", org_id)

    response = await client.post(
        f"/admin/orgs/{org_id}/unarchive/",
        headers=AUTH_HEADERS,
        follow_redirects=False,
    )
    assert response.status_code in (302, 303)
    assert response.headers["location"] == f"/admin/orgs/{org_id}/?flash=unarchived"


async def test_unarchived_flash_renders_on_org_detail(client, org_id):
    """Org detail with ?flash=unarchived renders the success flash."""
    response = await client.get(f"/admin/orgs/{org_id}/?flash=unarchived", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert "Organization unarchived." in response.text
    assert "flash--success" in response.text
    assert "HX-Replace-Url" in response.headers
    assert "flash" not in response.headers["HX-Replace-Url"]


async def test_org_detail_unknown_flash_key_ignored(client, org_id):
    """GET org detail with ?flash=bogus returns 200 with no flash and no HX-Replace-Url."""
    response = await client.get(f"/admin/orgs/{org_id}/?flash=bogus", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert "flash--success" not in response.text
    assert "HX-Replace-Url" not in response.headers


async def test_unarchive_org_rejects_non_archived(client, org_id):
    response = await client.post(
        f"/admin/orgs/{org_id}/unarchive/",
        headers=AUTH_HEADERS,
        follow_redirects=False,
    )
    assert response.status_code == 409


async def test_unarchive_org_redirects_unauthenticated(client, org_id):
    response = await client.post(
        f"/admin/orgs/{org_id}/unarchive/",
        follow_redirects=False,
    )
    assert response.status_code in (302, 307)
    assert "/__exe.dev/login" in response.headers["location"]


async def test_hard_delete_requires_archive_first(client, org_id):
    response = await client.delete(f"/admin/orgs/{org_id}/", headers=AUTH_HEADERS)
    assert response.status_code == 409


async def test_hard_delete_archived_org(client, org_id, db):
    await db.execute("UPDATE organizations SET archived_at = NOW() WHERE id = $1", org_id)

    response = await client.delete(
        f"/admin/orgs/{org_id}/",
        headers={**AUTH_HEADERS, "HX-Request": "true"},
    )
    assert response.status_code == 204
    assert "HX-Location" in response.headers
    assert "flash=deleted" in response.headers["HX-Location"]


async def test_hard_delete_archived_org_non_htmx_redirects(client, org_id, db):
    """Non-HTMX delete must redirect to org list."""
    await db.execute("UPDATE organizations SET archived_at = NOW() WHERE id = $1", org_id)

    response = await client.delete(
        f"/admin/orgs/{org_id}/",
        headers=AUTH_HEADERS,
        follow_redirects=False,
    )
    assert response.status_code in (302, 303)
    assert "/admin/orgs/" in response.headers["location"]
    assert "flash=deleted" in response.headers["location"]


async def test_orgs_list_flash_deleted_renders_message(client):
    """GET /admin/orgs/?flash=deleted must render a flash notification."""
    response = await client.get("/admin/orgs/?flash=deleted", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert "Organization deleted" in response.text
    assert "flash" in response.text.lower()


async def test_orgs_list_flash_deleted_strips_param_via_hx_replace_url(client):
    """Full-page response with ?flash=deleted must include HX-Replace-Url without flash param."""
    response = await client.get("/admin/orgs/?flash=deleted", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert "HX-Replace-Url" in response.headers
    assert "flash" not in response.headers["HX-Replace-Url"]


async def test_orgs_list_unknown_flash_key_ignored(client):
    """GET /admin/orgs/?flash=bogus must return 200 with no flash rendered."""
    response = await client.get("/admin/orgs/?flash=bogus", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert "Organization deleted" not in response.text
    assert "HX-Replace-Url" not in response.headers


async def test_org_with_acronym_appears_once_in_list_with_formatted_name(client, db):
    oid = generate_id()
    await db.execute("INSERT INTO organizations (id) VALUES ($1)", oid)
    await db.execute(
        "INSERT INTO organization_names"
        " (id, organization_id, name, name_type, is_canonical)"
        " VALUES ($1, $2, 'Cannabis Alliance', 'legal', TRUE)",
        generate_id(),
        oid,
    )
    await db.execute(
        "INSERT INTO organization_acronyms"
        " (id, organization_id, acronym, is_canonical)"
        " VALUES ($1, $2, 'CA', TRUE)",
        generate_id(),
        oid,
    )

    response = await client.get("/admin/orgs/?q=Cannabis+Alliance", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert "Cannabis Alliance (CA)" in response.text
    # Each row in _rows.html renders exactly one Edit-button aria-label
    # (`aria-label="Edit <display name>"`) — a stable per-row marker. We
    # count that rather than counting raw href occurrences (which depend
    # on how many links per row the template happens to render today).
    edit_aria_marker = 'aria-label="Edit Cannabis Alliance (CA)"'
    assert response.text.count(edit_aria_marker) == 1, "org must appear in exactly one row"


async def test_create_org_with_acronym_stores_acronym(client, db):
    """Creating an org with acronym=NEWCO must insert a canonical acronym row."""
    response = await client.post(
        "/admin/orgs/new/",
        headers=AUTH_HEADERS,
        data={"name": "New Company", "acronym": "NEWCO", "active": "true"},
        follow_redirects=False,
    )
    assert response.status_code in (302, 303)
    location = response.headers["location"]
    created_id = location.rstrip("/").split("/")[-1]

    row = await db.fetchrow(
        "SELECT acronym FROM organization_acronyms"
        " WHERE organization_id = $1 AND is_canonical = TRUE",
        created_id,
    )
    assert row is not None and row["acronym"] == "NEWCO"


async def test_create_org_without_acronym_succeeds(client, db):
    """Creating an org with no acronym field must succeed and insert no acronym row."""
    response = await client.post(
        "/admin/orgs/new/",
        headers=AUTH_HEADERS,
        data={"name": "No Acronym Org", "active": "true"},
        follow_redirects=False,
    )
    assert response.status_code in (302, 303)
    created_id = response.headers["location"].rstrip("/").split("/")[-1]

    row = await db.fetchrow(
        "SELECT id FROM organization_acronyms WHERE organization_id = $1", created_id
    )
    assert row is None, "no acronym row should be created"


async def test_org_create_blank_name_returns_html_not_json(client):
    """POST /admin/orgs/new/ with blank name must return HTML form, not JSON."""
    r = await client.post(
        "/admin/orgs/new/",
        headers=AUTH_HEADERS,
        data={"name": "", "active": "true"},
        follow_redirects=False,
    )
    assert r.status_code == 422
    assert r.headers["content-type"].startswith("text/html")
    assert "Name is required" in r.text


async def test_org_create_blank_name_preserves_submitted_values(client):
    """POST /admin/orgs/new/ with blank name must re-render form with submitted acronym."""
    r = await client.post(
        "/admin/orgs/new/",
        headers=AUTH_HEADERS,
        data={"name": "   ", "acronym": "ACME", "active": "true"},
        follow_redirects=False,
    )
    assert r.status_code == 422
    assert "ACME" in r.text


async def test_org_create_blank_name_preserves_notes(client):
    """POST /admin/orgs/new/ with blank name must re-render form with submitted notes."""
    r = await client.post(
        "/admin/orgs/new/",
        headers=AUTH_HEADERS,
        data={"name": "", "notes": "Some notes here", "active": "true"},
        follow_redirects=False,
    )
    assert r.status_code == 422
    assert "Some notes here" in r.text


async def test_orgs_list_htmx_boost_returns_full_page(client):
    """Boosted navigation must return the full page layout, not a bare rows partial."""
    response = await client.get(
        "/admin/orgs/",
        headers={**AUTH_HEADERS, "HX-Request": "true", "HX-Boosted": "true"},
    )
    assert response.status_code == 200
    assert "admin-layout" in response.text


async def test_orgs_list_htmx_request_returns_rows_partial(client):
    """Non-boosted HTMX request (filter/pagination) must return the rows partial only."""
    response = await client.get(
        "/admin/orgs/",
        headers={**AUTH_HEADERS, "HX-Request": "true"},
    )
    assert response.status_code == 200
    assert "admin-layout" not in response.text


async def test_org_detail_hierarchy_has_entity_card(client, org_id):
    r = await client.get(f"/admin/orgs/{org_id}/", headers=AUTH_HEADERS)
    assert r.status_code == 200
    assert "Parent Organization" in r.text
    assert "Child Organizations" in r.text
    assert "field-group-label" in r.text


async def test_org_detail_contact_information_section(client, org_id):
    r = await client.get(f"/admin/orgs/{org_id}/", headers=AUTH_HEADERS)
    assert r.status_code == 200
    assert "Contact Information" in r.text
    assert 'id="emails-table"' in r.text
    assert 'id="phones-table"' in r.text
    assert 'id="addresses-table"' in r.text
    # Old standalone sections should be gone
    assert "<h2>Addresses</h2>" not in r.text
    assert "<h2>Contact Methods</h2>" not in r.text


async def test_org_detail_links_add_button_in_header(client, org_id):
    r = await client.get(f"/admin/orgs/{org_id}/", headers=AUTH_HEADERS)
    assert r.status_code == 200
    links_idx = r.text.find('id="links-table"')
    add_link_idx = r.text.find("+ Add link")
    assert add_link_idx < links_idx  # button comes before table


async def test_org_detail_identifiers_add_button_in_header(client, org_id):
    r = await client.get(f"/admin/orgs/{org_id}/", headers=AUTH_HEADERS)
    idents_idx = r.text.find('id="identifiers-table"')
    add_idents_idx = r.text.find("+ Add identifier")
    assert add_idents_idx < idents_idx
