"""Integration tests for person links CRUD (parity with test_orgs_links.py)."""

import json
import re

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient

from src.api.main import app
from src.core.db import generate_id

pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
]
AUTH_HEADERS = {"X-ExeDev-UserID": "usr_test", "X-ExeDev-Email": "admin@test.com"}
HTMX_HEADERS = {**AUTH_HEADERS, "HX-Request": "true"}


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


@pytest_asyncio.fixture(loop_scope="session")
async def person_and_link(db_pool):
    pid, lid = generate_id(), generate_id()

    async with db_pool.acquire() as conn:
        await conn.execute("INSERT INTO people (id) VALUES ($1)", pid)
        lt_id = await conn.fetchval("SELECT id FROM link_types WHERE slug='website'")
        await conn.execute(
            "INSERT INTO links (id, entity_type, entity_id, url, link_type_id)"
            " VALUES ($1, 'person', $2, 'https://example.com', $3)",
            lid,
            pid,
            lt_id,
        )

    yield pid, lid

    async with db_pool.acquire() as conn:
        await conn.execute("DELETE FROM links WHERE entity_id=$1", pid)
        await conn.execute("DELETE FROM people WHERE id=$1", pid)


async def _get_link_type_id(pool):
    async with pool.acquire() as conn:
        return await conn.fetchval("SELECT id FROM link_types WHERE slug='website'")


async def test_links_new_row_returns_form(client, person_and_link):
    pid, _ = person_and_link
    r = client.get(f"/admin/people/{pid}/links/new-row/", headers=HTMX_HEADERS)
    assert r.status_code == 200
    assert "<form" in r.text


async def test_links_create(client, person_and_link, db_pool):
    pid, _ = person_and_link
    lt_id = await _get_link_type_id(db_pool)
    r = client.post(
        f"/admin/people/{pid}/links/",
        headers=HTMX_HEADERS,
        data={"url": "https://new.example.com", "link_type_id": lt_id, "is_active": "true"},
    )
    assert r.status_code == 200
    assert "https://new.example.com" in r.text


async def test_links_read_row_returns_row(client, person_and_link):
    pid, lid = person_and_link
    r = client.get(f"/admin/people/{pid}/links/{lid}/read-row/", headers=HTMX_HEADERS)
    assert r.status_code == 200
    assert "https://example.com" in r.text
    assert "<form" not in r.text


async def test_links_edit_row_returns_form(client, person_and_link):
    pid, lid = person_and_link
    r = client.get(f"/admin/people/{pid}/links/{lid}/edit-row/", headers=HTMX_HEADERS)
    assert r.status_code == 200
    assert "<form" in r.text


async def test_links_update(client, person_and_link, db_pool):
    pid, lid = person_and_link
    lt_id = await _get_link_type_id(db_pool)
    r = client.post(
        f"/admin/people/{pid}/links/{lid}/edit-row/",
        headers=HTMX_HEADERS,
        data={
            "url": "https://updated.example.com",
            "link_type_id": lt_id,
            "is_active": "true",
        },
    )
    assert r.status_code == 200
    assert "https://updated.example.com" in r.text


async def test_links_delete(client, person_and_link):
    pid, lid = person_and_link
    r = client.delete(f"/admin/people/{pid}/links/{lid}/", headers=HTMX_HEADERS)
    assert r.status_code == 200


async def test_links_delete_unknown_returns_404(client, person_and_link):
    pid, _ = person_and_link
    r = client.delete(f"/admin/people/{pid}/links/{generate_id()}/", headers=HTMX_HEADERS)
    assert r.status_code == 404


async def test_link_form_row_has_form_group(client, person_and_link):
    pid, _ = person_and_link
    r = client.get(f"/admin/people/{pid}/links/new-row/", headers=HTMX_HEADERS)
    assert r.status_code == 200
    assert "form-group" in r.text


async def test_link_form_row_has_toggle(client, person_and_link):
    pid, _ = person_and_link
    r = client.get(f"/admin/people/{pid}/links/new-row/", headers=HTMX_HEADERS)
    assert "toggle__track" in r.text


async def test_links_create_returns_success_flash(client, person_and_link, db_pool):
    pid, _ = person_and_link
    lt_id = await _get_link_type_id(db_pool)
    r = client.post(
        f"/admin/people/{pid}/links/",
        headers=HTMX_HEADERS,
        data={
            "url": "https://flash.example.com",
            "link_type_id": lt_id,
            "is_active": "true",
        },
    )
    assert r.status_code == 200
    trigger = json.loads(r.headers["hx-trigger"])
    assert trigger["showFlash"]["level"] == "success"
    assert "flash.example.com" in trigger["showFlash"]["body"]


async def test_links_update_returns_success_flash(client, person_and_link, db_pool):
    pid, lid = person_and_link
    lt_id = await _get_link_type_id(db_pool)
    r = client.post(
        f"/admin/people/{pid}/links/{lid}/edit-row/",
        headers=HTMX_HEADERS,
        data={
            "url": "https://updated.example.com",
            "link_type_id": lt_id,
            "is_active": "true",
        },
    )
    assert r.status_code == 200
    trigger = json.loads(r.headers["hx-trigger"])
    assert trigger["showFlash"]["level"] == "success"
    assert "updated.example.com" in trigger["showFlash"]["body"]


async def test_links_delete_returns_info_flash(client, person_and_link):
    pid, lid = person_and_link
    r = client.delete(f"/admin/people/{pid}/links/{lid}/", headers=HTMX_HEADERS)
    assert r.status_code == 200
    trigger = json.loads(r.headers["hx-trigger"])
    assert trigger["showFlash"]["level"] == "info"


async def test_links_create_duplicate_returns_409_not_500(client, person_and_link, db_pool):
    """Issue #142: posting an existing (entity_type, entity_id, url, link_type_id)
    must return 409 with a flash, not 500. Parity with the orgs router test.
    """
    pid, _ = person_and_link  # fixture already inserted https://example.com (website)
    lt_id = await _get_link_type_id(db_pool)
    r = client.post(
        f"/admin/people/{pid}/links/",
        headers=HTMX_HEADERS,
        data={"url": "https://example.com", "link_type_id": lt_id, "is_active": "true"},
    )
    assert r.status_code == 409, (
        f"expected 409 on duplicate URL, got {r.status_code}: {r.text[:200]}"
    )
    trigger = json.loads(r.headers["hx-trigger"])
    assert trigger["showFlash"]["level"] == "warning"
    assert "already" in trigger["showFlash"]["body"].lower()


async def test_links_update_to_existing_returns_409_not_500(client, person_and_link, db_pool):
    """Editing a link to a URL/type pair that already exists for the entity
    must return 409 with a flash, not 500. Parity with the orgs router test.
    """
    pid, _ = person_and_link  # fixture has https://example.com (website)
    lt_id = await _get_link_type_id(db_pool)
    # Create a second distinct link; parse its id from the response row.
    r = client.post(
        f"/admin/people/{pid}/links/",
        headers=HTMX_HEADERS,
        data={
            "url": "https://other.example.com",
            "link_type_id": lt_id,
            "is_active": "true",
        },
    )
    assert r.status_code == 200
    m = re.search(r'id="link-row-([0-9A-Z]+)"', r.text)
    assert m, f"could not parse link id from response: {r.text[:200]}"
    other_lid = m.group(1)
    r = client.post(
        f"/admin/people/{pid}/links/{other_lid}/edit-row/",
        headers=HTMX_HEADERS,
        data={"url": "https://example.com", "link_type_id": lt_id, "is_active": "true"},
    )
    assert r.status_code == 409, (
        f"expected 409 on duplicate UPDATE, got {r.status_code}: {r.text[:200]}"
    )
    trigger = json.loads(r.headers["hx-trigger"])
    assert trigger["showFlash"]["level"] == "warning"
