"""Integration tests for admin people views."""

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient

from src.api.main import app
from src.core.db import generate_id

pytestmark = [
    pytest.mark.integration,
]

AUTH_HEADERS = {"X-ExeDev-UserID": "usr_test", "X-ExeDev-Email": "admin@test.com"}


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


@pytest_asyncio.fixture(loop_scope="session")
async def person_id(db_pool):
    """Insert a person, yield its ID, then delete it."""
    pid = generate_id()

    async with db_pool.acquire() as conn:
        await conn.execute("INSERT INTO people (id) VALUES ($1)", pid)
        await conn.execute(
            "INSERT INTO person_names (id, person_id, name, is_canonical)"
            " VALUES ($1, $2, 'Jane Doe', TRUE)",
            generate_id(),
            pid,
        )

    yield pid

    async with db_pool.acquire() as conn:
        await conn.execute("DELETE FROM person_names WHERE person_id = $1", pid)
        await conn.execute("DELETE FROM people WHERE id = $1", pid)


async def test_people_list_returns_200(client):
    response = client.get("/admin/people/", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert "people" in response.text.lower()


async def test_people_list_redirects_unauthenticated(client):
    response = client.get("/admin/people/", follow_redirects=False)
    assert response.status_code in (302, 307)
    assert "/__exe.dev/login" in response.headers["location"]


async def test_person_detail_returns_200(client, person_id):
    response = client.get(f"/admin/people/{person_id}/", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert "Jane Doe" in response.text


async def test_person_detail_404_for_unknown(client):
    response = client.get(f"/admin/people/{generate_id()}/", headers=AUTH_HEADERS)
    assert response.status_code == 404


async def test_create_person_form_returns_200(client):
    response = client.get("/admin/people/new/", headers=AUTH_HEADERS)
    assert response.status_code == 200


async def test_create_person_post_redirects(client, db_pool):
    response = client.post(
        "/admin/people/new/",
        headers=AUTH_HEADERS,
        data={"name": "Test Person"},
        follow_redirects=False,
    )
    assert response.status_code in (302, 303)
    assert "/admin/people/" in response.headers["location"]

    async with db_pool.acquire() as conn:
        rows = await conn.fetch("SELECT person_id FROM person_names WHERE name = 'Test Person'")
        pids = [r["person_id"] for r in rows]
        if pids:
            await conn.execute("DELETE FROM role_assignments WHERE person_id = ANY($1)", pids)
            await conn.execute("DELETE FROM person_names WHERE person_id = ANY($1)", pids)
            await conn.execute("DELETE FROM people WHERE id = ANY($1)", pids)


async def test_archive_person(client, person_id):
    response = client.post(
        f"/admin/people/{person_id}/archive/",
        headers=AUTH_HEADERS,
        follow_redirects=False,
    )
    assert response.status_code in (302, 303)


async def test_archive_already_archived_person_returns_409(client, person_id, db_pool):
    """Re-archiving an already-archived person is rejected with 409."""
    async with db_pool.acquire() as conn:
        await conn.execute("UPDATE people SET archived_at = NOW() WHERE id = $1", person_id)

    response = client.post(
        f"/admin/people/{person_id}/archive/",
        headers=AUTH_HEADERS,
        follow_redirects=False,
    )
    assert response.status_code == 409
    assert response.json()["detail"] == "Person is already archived"


async def test_archive_person_redirects_with_flash_query(client, person_id):
    """Archive redirects to detail with ?flash=archived."""
    response = client.post(
        f"/admin/people/{person_id}/archive/",
        headers=AUTH_HEADERS,
        follow_redirects=False,
    )
    assert response.status_code in (302, 303)
    assert response.headers["location"] == f"/admin/people/{person_id}/?flash=archived"


async def test_archived_flash_renders_on_person_detail(client, person_id):
    """Person detail with ?flash=archived renders the success flash."""
    response = client.get(f"/admin/people/{person_id}/?flash=archived", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert "Person archived." in response.text
    assert "flash--success" in response.text
    assert "HX-Replace-Url" in response.headers
    assert "flash" not in response.headers["HX-Replace-Url"]


async def test_hard_delete_requires_archive(client, person_id):
    response = client.delete(f"/admin/people/{person_id}/", headers=AUTH_HEADERS)
    assert response.status_code == 409


async def test_hard_delete_archived_person(client, person_id):
    """HTMX delete returns 204 + HX-Location pointing at the people list with flash."""
    client.post(f"/admin/people/{person_id}/archive/", headers=AUTH_HEADERS, follow_redirects=False)
    response = client.delete(
        f"/admin/people/{person_id}/",
        headers={**AUTH_HEADERS, "HX-Request": "true"},
    )
    assert response.status_code == 204
    assert "HX-Location" in response.headers
    assert "/admin/people/" in response.headers["HX-Location"]
    assert "flash=deleted" in response.headers["HX-Location"]


async def test_hard_delete_archived_person_non_htmx_redirects(client, person_id):
    """Non-HTMX delete must redirect to people list with flash."""
    client.post(f"/admin/people/{person_id}/archive/", headers=AUTH_HEADERS, follow_redirects=False)
    response = client.delete(
        f"/admin/people/{person_id}/",
        headers=AUTH_HEADERS,
        follow_redirects=False,
    )
    assert response.status_code in (302, 303)
    assert "/admin/people/" in response.headers["location"]
    assert "flash=deleted" in response.headers["location"]


async def test_people_list_flash_deleted_renders_message(client):
    """GET /admin/people/?flash=deleted must render a flash notification."""
    response = client.get("/admin/people/?flash=deleted", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert "Person deleted" in response.text
    assert "flash" in response.text.lower()


async def test_people_list_flash_deleted_strips_param_via_hx_replace_url(client):
    """Full-page response with ?flash=deleted must include HX-Replace-Url without flash param."""
    response = client.get("/admin/people/?flash=deleted", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert "HX-Replace-Url" in response.headers
    assert "flash" not in response.headers["HX-Replace-Url"]


async def test_people_list_unknown_flash_key_ignored(client):
    """GET /admin/people/?flash=bogus must return 200 with no flash rendered."""
    response = client.get("/admin/people/?flash=bogus", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert "Person deleted" not in response.text
    assert "HX-Replace-Url" not in response.headers


async def test_unarchive_person_redirects_with_flash_query(client, person_id):
    """Unarchive redirects to detail with ?flash=unarchived."""
    client.post(f"/admin/people/{person_id}/archive/", headers=AUTH_HEADERS, follow_redirects=False)
    response = client.post(
        f"/admin/people/{person_id}/unarchive/",
        headers=AUTH_HEADERS,
        follow_redirects=False,
    )
    assert response.status_code in (302, 303)
    assert response.headers["location"] == f"/admin/people/{person_id}/?flash=unarchived"


async def test_unarchived_flash_renders_on_person_detail(client, person_id):
    """Person detail with ?flash=unarchived renders the success flash."""
    response = client.get(f"/admin/people/{person_id}/?flash=unarchived", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert "Person unarchived." in response.text
    assert "flash--success" in response.text
    assert "HX-Replace-Url" in response.headers
    assert "flash" not in response.headers["HX-Replace-Url"]


async def test_person_detail_unknown_flash_key_ignored(client, person_id):
    """GET person detail with ?flash=bogus returns 200 with no flash and no HX-Replace-Url."""
    response = client.get(f"/admin/people/{person_id}/?flash=bogus", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert "flash--success" not in response.text
    assert "HX-Replace-Url" not in response.headers


async def test_people_list_htmx_boost_returns_full_page(client):
    """Boosted navigation must return the full page layout, not a bare rows partial."""
    response = client.get(
        "/admin/people/",
        headers={**AUTH_HEADERS, "HX-Request": "true", "HX-Boosted": "true"},
    )
    assert response.status_code == 200
    assert "admin-layout" in response.text


async def test_people_list_htmx_request_returns_rows_partial(client):
    """Non-boosted HTMX request (filter/pagination) must return the rows partial only."""
    response = client.get(
        "/admin/people/",
        headers={**AUTH_HEADERS, "HX-Request": "true"},
    )
    assert response.status_code == 200
    assert "admin-layout" not in response.text
