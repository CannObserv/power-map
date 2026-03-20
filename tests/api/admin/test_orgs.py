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
        assert "ACRO" in response.text
        assert oid not in response.text.split("<h1>")[1].split("</h1>")[0], \
            "h1 must show acronym, not raw org ID"
    finally:
        asyncio.run(teardown())


def test_org_detail_404_for_unknown_id(client):
    response = client.get(f"/admin/orgs/{generate_id()}/", headers=AUTH_HEADERS)
    assert response.status_code == 404


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


def test_edit_org_form_returns_200(client, org_id):
    response = client.get(f"/admin/orgs/{org_id}/edit/", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert "Test Org" in response.text


def test_edit_org_does_not_overwrite_acronym(client):
    """Saving the edit form must update the canonical name; acronym must be preserved."""
    dsn = _get_dsn()
    oid = generate_id()

    async def setup():
        conn = await _aconnect(dsn)
        try:
            await conn.execute("INSERT INTO organizations (id) VALUES ($1)", oid)
            await conn.execute(
                "INSERT INTO organization_names"
                " (id, organization_id, name, name_type, is_canonical)"
                " VALUES ($1, $2, 'Old Name', 'legal', TRUE)",
                generate_id(), oid,
            )
            await conn.execute(
                "INSERT INTO organization_acronyms"
                " (id, organization_id, acronym, is_canonical)"
                " VALUES ($1, $2, 'ON', TRUE)",
                generate_id(), oid,
            )
        finally:
            await conn.close()

    async def get_state():
        conn = await _aconnect(dsn)
        try:
            name_row = await conn.fetchrow(
                "SELECT name FROM organization_names"
                " WHERE organization_id = $1 AND is_canonical = TRUE",
                oid,
            )
            acronym_row = await conn.fetchrow(
                "SELECT acronym FROM organization_acronyms"
                " WHERE organization_id = $1 AND is_canonical = TRUE",
                oid,
            )
            return name_row, acronym_row
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
        response = client.post(
            f"/admin/orgs/{oid}/edit/",
            headers=AUTH_HEADERS,
            data={"name": "New Name", "active": "true", "parent_id": "", "notes": ""},
            follow_redirects=False,
        )
        assert response.status_code in (302, 303)
        name_row, acronym_row = asyncio.run(get_state())
        assert name_row is not None and name_row["name"] == "New Name", \
            "canonical name must be updated"
        assert acronym_row is not None and acronym_row["acronym"] == "ON", \
            "acronym must not be overwritten"
    finally:
        asyncio.run(teardown())


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
