"""Integration tests for admin lookup table views."""

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


@pytest.fixture
async def db():
    """Async DB connection with schema applied."""
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
    """TestClient for the app."""
    with TestClient(app) as c:
        yield c


def test_platforms_list_returns_200(client):
    response = client.get("/admin/lookups/platforms/", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert "platform" in response.text.lower()


def test_platforms_list_redirects_unauthenticated(client):
    response = client.get("/admin/lookups/platforms/", follow_redirects=False)
    assert response.status_code in (302, 307)
    assert "/__exe.dev/login" in response.headers["location"]


def test_url_types_list_returns_200(client):
    response = client.get("/admin/lookups/url-types/", headers=AUTH_HEADERS)
    assert response.status_code == 200


def test_identifier_types_list_returns_200(client):
    response = client.get("/admin/lookups/identifier-types/", headers=AUTH_HEADERS)
    assert response.status_code == 200


def test_platforms_new_form_returns_200(client):
    response = client.get("/admin/lookups/platforms/new/", headers=AUTH_HEADERS)
    assert response.status_code == 200


def test_create_platform_redirects(client):
    response = client.post(
        "/admin/lookups/platforms/new/",
        headers=AUTH_HEADERS,
        data={"display_name": "Test Platform", "slug": f"test-platform-{generate_id()}"},
        follow_redirects=False,
    )
    assert response.status_code in (302, 303)


def test_edit_platform_form_returns_200(client, db):
    pid = generate_id()
    asyncio.get_event_loop().run_until_complete(
        db.execute(
            "INSERT INTO platforms (id, display_name, slug) VALUES ($1, $2, $3)",
            pid, "Edit Me", f"edit-me-{pid}",
        )
    )
    response = client.get(f"/admin/lookups/platforms/{pid}/edit/", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert "Edit Me" in response.text


def test_update_platform_redirects(client, db):
    pid = generate_id()
    asyncio.get_event_loop().run_until_complete(
        db.execute(
            "INSERT INTO platforms (id, display_name, slug) VALUES ($1, $2, $3)",
            pid, "Update Me", f"update-me-{pid}",
        )
    )
    response = client.post(
        f"/admin/lookups/platforms/{pid}/edit/",
        headers=AUTH_HEADERS,
        data={"display_name": "Updated Platform", "slug": f"updated-{pid}"},
        follow_redirects=False,
    )
    assert response.status_code in (302, 303)


def test_delete_platform(client, db):
    pid = generate_id()
    asyncio.get_event_loop().run_until_complete(
        db.execute(
            "INSERT INTO platforms (id, display_name, slug) VALUES ($1, $2, $3)",
            pid, "Delete Me", f"delete-me-{pid}",
        )
    )
    response = client.delete(f"/admin/lookups/platforms/{pid}/", headers=AUTH_HEADERS)
    assert response.status_code == 200


def test_create_url_type_redirects(client):
    response = client.post(
        "/admin/lookups/url-types/new/",
        headers=AUTH_HEADERS,
        data={"display_name": "Test URL Type", "slug": f"test-url-type-{generate_id()}"},
        follow_redirects=False,
    )
    assert response.status_code in (302, 303)


def test_delete_url_type(client, db):
    uid = generate_id()
    asyncio.get_event_loop().run_until_complete(
        db.execute(
            "INSERT INTO url_types (id, display_name, slug) VALUES ($1, $2, $3)",
            uid, "Delete URL Type", f"del-url-{uid}",
        )
    )
    response = client.delete(f"/admin/lookups/url-types/{uid}/", headers=AUTH_HEADERS)
    assert response.status_code == 200


def test_create_identifier_type_redirects(client):
    response = client.post(
        "/admin/lookups/identifier-types/new/",
        headers=AUTH_HEADERS,
        data={
            "display_name": "Test ID Type",
            "slug": f"test-id-type-{generate_id()}",
            "full_name": "Test Identifier Type Full Name",
            "entity_type": "organization",
        },
        follow_redirects=False,
    )
    assert response.status_code in (302, 303)


def test_delete_identifier_type(client, db):
    iid = generate_id()
    asyncio.get_event_loop().run_until_complete(
        db.execute(
            "INSERT INTO entity_identifier_types"
            " (id, display_name, slug, full_name, entity_type)"
            " VALUES ($1, $2, $3, $4, $5)",
            iid, "Delete ID Type", f"del-id-{iid}",
            "Delete Identifier Type Long Name", "organization",
        )
    )
    response = client.delete(
        f"/admin/lookups/identifier-types/{iid}/", headers=AUTH_HEADERS
    )
    assert response.status_code == 200
