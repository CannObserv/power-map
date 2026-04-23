"""Integration tests for admin organizations views."""

import asyncio
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


def _get_dsn() -> str:
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        pytest.skip("DATABASE_URL not set")
    return dsn


async def _aconnect(dsn: str) -> asyncpg.Connection:
    conn = await asyncpg.connect(dsn)
    await apply_schema(conn)
    return conn


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture
def org_id():
    """Insert an org, yield its ID, then delete it."""
    dsn = _get_dsn()
    oid = generate_id()

    async def setup():
        conn = await _aconnect(dsn)
        try:
            await conn.execute("INSERT INTO organizations (id) VALUES ($1)", oid)
            await conn.execute(
                "INSERT INTO organization_names (id, organization_id, name, is_canonical)"
                " VALUES ($1, $2, 'Test Org', TRUE)",
                generate_id(), oid,
            )
        finally:
            await conn.close()

    async def teardown():
        conn = await _aconnect(dsn)
        try:
            await conn.execute("DELETE FROM organization_acronyms WHERE organization_id = $1", oid)
            await conn.execute("DELETE FROM organization_names WHERE organization_id = $1", oid)
            await conn.execute("DELETE FROM organizations WHERE id = $1", oid)
        finally:
            await conn.close()

    asyncio.run(setup())
    yield oid
    asyncio.run(teardown())


def test_orgs_list_returns_200(client):
    response = client.get("/admin/orgs/", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert "organizations" in response.text.lower()


def test_orgs_list_redirects_unauthenticated(client):
    response = client.get("/admin/orgs/", follow_redirects=False)
    assert response.status_code in (302, 307)
    assert "/__exe.dev/login" in response.headers["location"]


def test_org_detail_returns_200(client, org_id):
    response = client.get(f"/admin/orgs/{org_id}/", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert "Test Org" in response.text


def test_org_detail_acronym_only_shows_acronym_in_heading(client):
    """Detail page for an org with only an acronym must show the acronym, not the raw ID."""
    dsn = _get_dsn()
    oid = generate_id()

    async def setup():
        conn = await _aconnect(dsn)
        try:
            await conn.execute("INSERT INTO organizations (id) VALUES ($1)", oid)
            await conn.execute(
                "INSERT INTO organization_acronyms (id, organization_id, acronym, is_canonical)"
                " VALUES ($1, $2, 'ACRO', TRUE)",
                generate_id(), oid,
            )
        finally:
            await conn.close()

    async def teardown():
        conn = await _aconnect(dsn)
        try:
            await conn.execute("DELETE FROM organization_acronyms WHERE organization_id = $1", oid)
            await conn.execute("DELETE FROM organizations WHERE id = $1", oid)
        finally:
            await conn.close()

    asyncio.run(setup())
    try:
        response = client.get(f"/admin/orgs/{oid}/", headers=AUTH_HEADERS)
        assert response.status_code == 200
        assert 'id="page-heading"' in response.text, "h1 must have id=page-heading"
        assert "ACRO" in response.text, "h1 must show acronym"
    finally:
        asyncio.run(teardown())


def test_org_detail_404_for_unknown_id(client):
    response = client.get(f"/admin/orgs/{generate_id()}/", headers=AUTH_HEADERS)
    assert response.status_code == 404


def test_org_detail_has_email_and_phone_tables(client, org_id):
    r = client.get(f"/admin/orgs/{org_id}/", headers=AUTH_HEADERS)
    assert r.status_code == 200
    assert 'id="emails-table"' in r.text
    assert 'id="phones-table"' in r.text


def test_create_org_form_returns_200(client):
    response = client.get("/admin/orgs/new/", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert "form" in response.text.lower()


def test_create_org_post_redirects_on_success(client):
    response = client.post(
        "/admin/orgs/new/",
        headers=AUTH_HEADERS,
        data={"name": "Test Create Org", "active": "true"},
        follow_redirects=False,
    )
    assert response.status_code in (302, 303)
    assert "/admin/orgs/" in response.headers["location"]


def test_edit_route_removed(client):
    """GET /edit/ must return 404 — route has been deleted."""
    r = client.get(f"/admin/orgs/{generate_id()}/edit/", headers=AUTH_HEADERS)
    assert r.status_code == 404


def test_org_create_rejects_empty_name(client):
    """POST /admin/orgs/new/ with empty name must be rejected (422)."""
    r = client.post(
        "/admin/orgs/new/",
        headers=AUTH_HEADERS,
        data={"name": "", "active": "true"},
        follow_redirects=False,
    )
    assert r.status_code == 422


def test_org_create_rejects_whitespace_only_name(client):
    """POST /admin/orgs/new/ with whitespace-only name must be rejected (422)."""
    r = client.post(
        "/admin/orgs/new/",
        headers=AUTH_HEADERS,
        data={"name": "   ", "active": "true"},
        follow_redirects=False,
    )
    assert r.status_code == 422


def test_archive_org(client, org_id):
    response = client.post(
        f"/admin/orgs/{org_id}/archive/",
        headers=AUTH_HEADERS,
        follow_redirects=False,
    )
    assert response.status_code in (302, 303)


def test_unarchive_org_clears_archived_at(client, org_id):
    dsn = _get_dsn()

    async def archive():
        conn = await _aconnect(dsn)
        try:
            await conn.execute(
                "UPDATE organizations SET archived_at = NOW(), active = FALSE WHERE id = $1",
                org_id,
            )
        finally:
            await conn.close()

    asyncio.run(archive())
    response = client.post(
        f"/admin/orgs/{org_id}/unarchive/",
        headers=AUTH_HEADERS,
        follow_redirects=False,
    )
    assert response.status_code in (302, 303)

    async def fetch():
        conn = await _aconnect(dsn)
        try:
            return await conn.fetchrow(
                "SELECT archived_at, active FROM organizations WHERE id = $1", org_id
            )
        finally:
            await conn.close()

    row = asyncio.run(fetch())
    assert row["archived_at"] is None
    assert row["active"] is False  # prior active state preserved


def test_unarchive_org_redirects_with_flash_query(client, org_id):
    """Unarchive redirects to detail with ?flash=unarchived."""
    dsn = _get_dsn()

    async def archive():
        conn = await _aconnect(dsn)
        try:
            await conn.execute(
                "UPDATE organizations SET archived_at = NOW() WHERE id = $1", org_id
            )
        finally:
            await conn.close()

    asyncio.run(archive())
    response = client.post(
        f"/admin/orgs/{org_id}/unarchive/",
        headers=AUTH_HEADERS,
        follow_redirects=False,
    )
    assert response.status_code in (302, 303)
    assert response.headers["location"] == f"/admin/orgs/{org_id}/?flash=unarchived"


def test_unarchived_flash_renders_on_org_detail(client, org_id):
    """Org detail with ?flash=unarchived renders the success flash."""
    response = client.get(f"/admin/orgs/{org_id}/?flash=unarchived", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert "Organization unarchived." in response.text
    assert "flash--success" in response.text


def test_unarchive_org_rejects_non_archived(client, org_id):
    response = client.post(
        f"/admin/orgs/{org_id}/unarchive/",
        headers=AUTH_HEADERS,
        follow_redirects=False,
    )
    assert response.status_code == 409


def test_unarchive_org_redirects_unauthenticated(client, org_id):
    response = client.post(
        f"/admin/orgs/{org_id}/unarchive/",
        follow_redirects=False,
    )
    assert response.status_code in (302, 307)
    assert "/__exe.dev/login" in response.headers["location"]


def test_hard_delete_requires_archive_first(client, org_id):
    response = client.delete(f"/admin/orgs/{org_id}/", headers=AUTH_HEADERS)
    assert response.status_code == 409


def test_hard_delete_archived_org(client, org_id):
    dsn = _get_dsn()

    async def archive():
        conn = await _aconnect(dsn)
        try:
            await conn.execute(
                "UPDATE organizations SET archived_at = NOW() WHERE id = $1", org_id
            )
        finally:
            await conn.close()

    asyncio.run(archive())
    response = client.delete(
        f"/admin/orgs/{org_id}/",
        headers={**AUTH_HEADERS, "HX-Request": "true"},
    )
    assert response.status_code == 204
    assert "HX-Location" in response.headers
    assert "flash=deleted" in response.headers["HX-Location"]


def test_hard_delete_archived_org_non_htmx_redirects(client, org_id):
    """Non-HTMX delete must redirect to org list."""
    dsn = _get_dsn()

    async def archive():
        conn = await _aconnect(dsn)
        try:
            await conn.execute(
                "UPDATE organizations SET archived_at = NOW() WHERE id = $1", org_id
            )
        finally:
            await conn.close()

    asyncio.run(archive())
    response = client.delete(
        f"/admin/orgs/{org_id}/",
        headers=AUTH_HEADERS,
        follow_redirects=False,
    )
    assert response.status_code in (302, 303)
    assert "/admin/orgs/" in response.headers["location"]
    assert "flash=deleted" in response.headers["location"]


def test_orgs_list_flash_deleted_renders_message(client):
    """GET /admin/orgs/?flash=deleted must render a flash notification."""
    response = client.get("/admin/orgs/?flash=deleted", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert "Organization deleted" in response.text
    assert "flash" in response.text.lower()


def test_orgs_list_flash_deleted_strips_param_via_hx_replace_url(client):
    """Full-page response with ?flash=deleted must include HX-Replace-Url without flash param."""
    response = client.get("/admin/orgs/?flash=deleted", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert "HX-Replace-Url" in response.headers
    assert "flash" not in response.headers["HX-Replace-Url"]


def test_orgs_list_unknown_flash_key_ignored(client):
    """GET /admin/orgs/?flash=bogus must return 200 with no flash rendered."""
    response = client.get("/admin/orgs/?flash=bogus", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert "Organization deleted" not in response.text
    assert "HX-Replace-Url" not in response.headers


def test_org_with_acronym_appears_once_in_list_with_formatted_name(client):
    dsn = _get_dsn()
    oid = generate_id()

    async def setup():
        conn = await _aconnect(dsn)
        try:
            await conn.execute("INSERT INTO organizations (id) VALUES ($1)", oid)
            await conn.execute(
                "INSERT INTO organization_names"
                " (id, organization_id, name, name_type, is_canonical)"
                " VALUES ($1, $2, 'Cannabis Alliance', 'legal', TRUE)",
                generate_id(), oid,
            )
            await conn.execute(
                "INSERT INTO organization_acronyms"
                " (id, organization_id, acronym, is_canonical)"
                " VALUES ($1, $2, 'CA', TRUE)",
                generate_id(), oid,
            )
        finally:
            await conn.close()

    async def teardown():
        conn = await _aconnect(dsn)
        try:
            await conn.execute("DELETE FROM organization_acronyms WHERE organization_id = $1", oid)
            await conn.execute("DELETE FROM organization_names WHERE organization_id = $1", oid)
            await conn.execute("DELETE FROM organizations WHERE id = $1", oid)
        finally:
            await conn.close()

    asyncio.run(setup())
    try:
        response = client.get("/admin/orgs/?q=Cannabis+Alliance", headers=AUTH_HEADERS)
        assert response.status_code == 200
        assert "Cannabis Alliance (CA)" in response.text
        # Each row has a detail link + edit link; count detail link only to detect duplicate rows.
        detail_link_count = response.text.count(f'href="/admin/orgs/{oid}/"')
        assert detail_link_count == 1, "org must appear exactly once"
    finally:
        asyncio.run(teardown())


def test_create_org_with_acronym_stores_acronym(client):
    """Creating an org with acronym=NEWCO must insert a canonical acronym row."""
    dsn = _get_dsn()

    response = client.post(
        "/admin/orgs/new/",
        headers=AUTH_HEADERS,
        data={"name": "New Company", "acronym": "NEWCO", "active": "true"},
        follow_redirects=False,
    )
    assert response.status_code in (302, 303)
    location = response.headers["location"]
    created_id = location.rstrip("/").split("/")[-1]

    async def get_acronym():
        conn = await _aconnect(dsn)
        try:
            return await conn.fetchrow(
                "SELECT acronym FROM organization_acronyms"
                " WHERE organization_id = $1 AND is_canonical = TRUE",
                created_id,
            )
        finally:
            await conn.close()

    async def teardown():
        conn = await _aconnect(dsn)
        try:
            await conn.execute(
                "DELETE FROM organization_acronyms WHERE organization_id = $1", created_id
            )
            await conn.execute(
                "DELETE FROM organization_names WHERE organization_id = $1", created_id
            )
            await conn.execute("DELETE FROM organizations WHERE id = $1", created_id)
        finally:
            await conn.close()

    try:
        row = asyncio.run(get_acronym())
        assert row is not None and row["acronym"] == "NEWCO"
    finally:
        asyncio.run(teardown())


def test_create_org_without_acronym_succeeds(client):
    """Creating an org with no acronym field must succeed and insert no acronym row."""
    dsn = _get_dsn()

    response = client.post(
        "/admin/orgs/new/",
        headers=AUTH_HEADERS,
        data={"name": "No Acronym Org", "active": "true"},
        follow_redirects=False,
    )
    assert response.status_code in (302, 303)
    created_id = response.headers["location"].rstrip("/").split("/")[-1]

    async def get_acronym_row():
        conn = await _aconnect(dsn)
        try:
            return await conn.fetchrow(
                "SELECT id FROM organization_acronyms WHERE organization_id = $1", created_id
            )
        finally:
            await conn.close()

    async def teardown():
        conn = await _aconnect(dsn)
        try:
            await conn.execute(
                "DELETE FROM organization_names WHERE organization_id = $1", created_id
            )
            await conn.execute("DELETE FROM organizations WHERE id = $1", created_id)
        finally:
            await conn.close()

    try:
        row = asyncio.run(get_acronym_row())
        assert row is None, "no acronym row should be created"
    finally:
        asyncio.run(teardown())


def test_org_create_blank_name_returns_html_not_json(client):
    """POST /admin/orgs/new/ with blank name must return HTML form, not JSON."""
    r = client.post(
        "/admin/orgs/new/",
        headers=AUTH_HEADERS,
        data={"name": "", "active": "true"},
        follow_redirects=False,
    )
    assert r.status_code == 422
    assert r.headers["content-type"].startswith("text/html")
    assert "Name is required" in r.text


def test_org_create_blank_name_preserves_submitted_values(client):
    """POST /admin/orgs/new/ with blank name must re-render form with submitted acronym."""
    r = client.post(
        "/admin/orgs/new/",
        headers=AUTH_HEADERS,
        data={"name": "   ", "acronym": "ACME", "active": "true"},
        follow_redirects=False,
    )
    assert r.status_code == 422
    assert "ACME" in r.text


def test_org_create_blank_name_preserves_notes(client):
    """POST /admin/orgs/new/ with blank name must re-render form with submitted notes."""
    r = client.post(
        "/admin/orgs/new/",
        headers=AUTH_HEADERS,
        data={"name": "", "notes": "Some notes here", "active": "true"},
        follow_redirects=False,
    )
    assert r.status_code == 422
    assert "Some notes here" in r.text


def test_orgs_list_htmx_boost_returns_full_page(client):
    """Boosted navigation must return the full page layout, not a bare rows partial."""
    response = client.get(
        "/admin/orgs/",
        headers={**AUTH_HEADERS, "HX-Request": "true", "HX-Boosted": "true"},
    )
    assert response.status_code == 200
    assert "admin-layout" in response.text


def test_orgs_list_htmx_request_returns_rows_partial(client):
    """Non-boosted HTMX request (filter/pagination) must return the rows partial only."""
    response = client.get(
        "/admin/orgs/",
        headers={**AUTH_HEADERS, "HX-Request": "true"},
    )
    assert response.status_code == 200
    assert "admin-layout" not in response.text


def test_org_detail_hierarchy_has_entity_card(client, org_id):
    r = client.get(f"/admin/orgs/{org_id}/", headers=AUTH_HEADERS)
    assert r.status_code == 200
    assert "Parent Organization" in r.text
    assert "Child Organizations" in r.text
    assert "field-group-label" in r.text


def test_org_detail_contact_information_section(client, org_id):
    r = client.get(f"/admin/orgs/{org_id}/", headers=AUTH_HEADERS)
    assert r.status_code == 200
    assert "Contact Information" in r.text
    assert 'id="emails-table"' in r.text
    assert 'id="phones-table"' in r.text
    assert 'id="addresses-table"' in r.text
    # Old standalone sections should be gone
    assert "<h2>Addresses</h2>" not in r.text
    assert "<h2>Contact Methods</h2>" not in r.text


def test_org_detail_links_add_button_in_header(client, org_id):
    r = client.get(f"/admin/orgs/{org_id}/", headers=AUTH_HEADERS)
    assert r.status_code == 200
    links_idx = r.text.find('id="links-table"')
    add_link_idx = r.text.find("+ Add link")
    assert add_link_idx < links_idx  # button comes before table


def test_org_detail_identifiers_add_button_in_header(client, org_id):
    r = client.get(f"/admin/orgs/{org_id}/", headers=AUTH_HEADERS)
    idents_idx = r.text.find('id="identifiers-table"')
    add_idents_idx = r.text.find("+ Add identifier")
    assert add_idents_idx < idents_idx
