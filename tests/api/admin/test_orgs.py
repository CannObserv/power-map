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
        h1_text = response.text.split("<h1>")[1].split("</h1>")[0]
        assert "ACRO" in h1_text, "h1 must show acronym"
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


def test_archive_org(client, org_id):
    response = client.post(
        f"/admin/orgs/{org_id}/archive/",
        headers=AUTH_HEADERS,
        follow_redirects=False,
    )
    assert response.status_code in (302, 303)


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
    response = client.delete(f"/admin/orgs/{org_id}/", headers=AUTH_HEADERS)
    assert response.status_code == 200


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
