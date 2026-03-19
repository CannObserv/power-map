"""Integration tests for admin people views."""

import asyncio
import os

import asyncpg
import pytest
from fastapi.testclient import TestClient

from src.api.main import app
from src.core.db import apply_schema, generate_id

pytestmark = pytest.mark.integration

AUTH_HEADERS = {"X-ExeDev-UserID": "usr_test", "X-ExeDev-Email": "admin@test.com"}


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
def person_id():
    """Insert a person, yield its ID, then delete it."""
    dsn = _get_dsn()
    pid = generate_id()

    async def setup():
        conn = await _aconnect(dsn)
        try:
            await conn.execute("INSERT INTO people (id) VALUES ($1)", pid)
            await conn.execute(
                "INSERT INTO person_names (id, person_id, name, is_canonical)"
                " VALUES ($1, $2, 'Jane Doe', TRUE)",
                generate_id(), pid,
            )
        finally:
            await conn.close()

    async def teardown():
        conn = await _aconnect(dsn)
        try:
            await conn.execute("DELETE FROM person_names WHERE person_id = $1", pid)
            await conn.execute("DELETE FROM people WHERE id = $1", pid)
        finally:
            await conn.close()

    asyncio.run(setup())
    yield pid
    asyncio.run(teardown())


def test_people_list_returns_200(client):
    response = client.get("/admin/people/", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert "people" in response.text.lower()


def test_people_list_redirects_unauthenticated(client):
    response = client.get("/admin/people/", follow_redirects=False)
    assert response.status_code in (302, 307)
    assert "/__exe.dev/login" in response.headers["location"]


def test_person_detail_returns_200(client, person_id):
    response = client.get(f"/admin/people/{person_id}/", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert "Jane Doe" in response.text


def test_person_detail_404_for_unknown(client):
    response = client.get(f"/admin/people/{generate_id()}/", headers=AUTH_HEADERS)
    assert response.status_code == 404


def test_create_person_form_returns_200(client):
    response = client.get("/admin/people/new/", headers=AUTH_HEADERS)
    assert response.status_code == 200


def test_create_person_post_redirects(client):
    response = client.post(
        "/admin/people/new/",
        headers=AUTH_HEADERS,
        data={"name": "Test Person"},
        follow_redirects=False,
    )
    assert response.status_code in (302, 303)
    assert "/admin/people/" in response.headers["location"]


def test_edit_person_form_returns_200(client, person_id):
    response = client.get(f"/admin/people/{person_id}/edit/", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert "Jane Doe" in response.text


def test_archive_person(client, person_id):
    response = client.post(
        f"/admin/people/{person_id}/archive/",
        headers=AUTH_HEADERS,
        follow_redirects=False,
    )
    assert response.status_code in (302, 303)


def test_hard_delete_requires_archive(client, person_id):
    response = client.delete(f"/admin/people/{person_id}/", headers=AUTH_HEADERS)
    assert response.status_code == 409


def test_hard_delete_archived_person(client, person_id):
    # Archive first via the route
    client.post(f"/admin/people/{person_id}/archive/", headers=AUTH_HEADERS, follow_redirects=False)
    response = client.delete(f"/admin/people/{person_id}/", headers=AUTH_HEADERS)
    assert response.status_code == 200
