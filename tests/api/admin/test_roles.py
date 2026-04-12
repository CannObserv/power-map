"""Integration tests for admin roles views.

Requires DATABASE_URL. Run with:
    DATABASE_URL=<dsn> uv run pytest tests/api/admin/test_roles.py -m integration -v
"""

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


async def test_role_detail_shows_person_name(client, db, role_id, person_id):
    """Role detail assignment list must show canonical name via v_person_display_names."""
    ra_id = generate_id()
    await db.execute(
        "INSERT INTO role_assignments (id, person_id, role_id, is_current)"
        " VALUES ($1, $2, $3, TRUE)",
        ra_id, person_id, role_id,
    )
    try:
        response = client.get(f"/admin/roles/{role_id}/", headers=AUTH_HEADERS)
        assert response.status_code == 200
        assert "Test Person" in response.text
    finally:
        await db.execute("DELETE FROM role_assignments WHERE id = $1", ra_id)


def test_roles_list_returns_200(client):
    response = client.get("/admin/roles/", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert "roles" in response.text.lower()


def test_roles_list_redirects_unauthenticated(client):
    response = client.get("/admin/roles/", follow_redirects=False)
    assert response.status_code in (302, 307)
    assert "/__exe.dev/login" in response.headers["location"]


def test_role_detail_returns_200(client, role_id):
    response = client.get(f"/admin/roles/{role_id}/", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert "Test Role" in response.text


def test_role_detail_404_for_unknown(client):
    response = client.get(f"/admin/roles/{generate_id()}/", headers=AUTH_HEADERS)
    assert response.status_code == 404


def test_create_role_post_redirects(client, org_id):
    response = client.post(
        "/admin/roles/new/",
        headers=AUTH_HEADERS,
        data={"organization_id": org_id, "title": "New Role", "notes": ""},
        follow_redirects=False,
    )
    assert response.status_code in (302, 303)


def test_archive_role(client, role_id):
    response = client.post(
        f"/admin/roles/{role_id}/archive/",
        headers=AUTH_HEADERS,
        follow_redirects=False,
    )
    assert response.status_code in (302, 303)


def test_hard_delete_requires_archive(client, role_id):
    response = client.delete(f"/admin/roles/{role_id}/", headers=AUTH_HEADERS)
    assert response.status_code == 409


async def test_hard_delete_archived_role(client, db, role_id):
    await db.execute("UPDATE roles SET archived_at = NOW() WHERE id = $1", role_id)
    response = client.delete(
        f"/admin/roles/{role_id}/",
        headers={**AUTH_HEADERS, "HX-Request": "true"},
        follow_redirects=False,
    )
    assert response.status_code == 200


async def test_hard_delete_archived_role_htmx_redirects(client, db, role_id):
    """HTMX delete of archived role must return HX-Redirect to /admin/roles/."""
    await db.execute("UPDATE roles SET archived_at = NOW() WHERE id = $1", role_id)
    response = client.delete(
        f"/admin/roles/{role_id}/",
        headers={**AUTH_HEADERS, "HX-Request": "true"},
    )
    assert response.status_code == 200
    assert response.headers.get("HX-Redirect") == "/admin/roles/"


async def test_hard_delete_archived_role_non_htmx_redirects(client, db, role_id):
    """Non-HTMX delete of archived role must 303-redirect to /admin/roles/."""
    await db.execute("UPDATE roles SET archived_at = NOW() WHERE id = $1", role_id)
    response = client.delete(
        f"/admin/roles/{role_id}/",
        headers=AUTH_HEADERS,
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers.get("location") == "/admin/roles/"


def test_roles_list_filters_by_org_name(client, role_id):
    response = client.get("/admin/roles/?org_q=Test", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert "Test Role" in response.text


def test_roles_list_org_filter_excludes_nonmatching(client, role_id):
    response = client.get("/admin/roles/?org_q=NoSuchOrg", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert "Test Role" not in response.text


def test_roles_list_org_filter_literal_percent(client, role_id):
    # A literal '%' in org_q must not act as a SQL wildcard
    response = client.get("/admin/roles/?org_q=%25", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert "Test Role" not in response.text


def test_roles_list_title_and_org_combined(client, role_id):
    response = client.get("/admin/roles/?q=Test&org_q=Test", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert "Test Role" in response.text


def test_roles_list_title_and_org_nonmatching_combo(client, role_id):
    response = client.get("/admin/roles/?q=Test&org_q=NoSuchOrg", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert "Test Role" not in response.text


def test_roles_list_htmx_boost_returns_full_page(client):
    """Boosted navigation must return the full page layout, not a bare rows partial."""
    response = client.get(
        "/admin/roles/",
        headers={**AUTH_HEADERS, "HX-Request": "true", "HX-Boosted": "true"},
    )
    assert response.status_code == 200
    assert "admin-layout" in response.text


def test_roles_list_htmx_request_returns_rows_partial(client):
    """Non-boosted HTMX request (filter/pagination) must return the rows partial only."""
    response = client.get(
        "/admin/roles/",
        headers={**AUTH_HEADERS, "HX-Request": "true"},
    )
    assert response.status_code == 200
    assert "admin-layout" not in response.text
