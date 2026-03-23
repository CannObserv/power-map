"""Integration tests for org links CRUD."""

import asyncio
import os

import asyncpg
import pytest
from fastapi.testclient import TestClient

from src.api.main import app
from src.core.db import apply_schema, generate_id

pytestmark = pytest.mark.integration
AUTH_HEADERS = {"X-ExeDev-UserID": "usr_test", "X-ExeDev-Email": "admin@test.com"}
HTMX_HEADERS = {**AUTH_HEADERS, "HX-Request": "true"}


def _dsn():
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        pytest.skip("DATABASE_URL not set")
    return dsn


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture
def org_and_link():
    dsn = _dsn()
    oid, lid = generate_id(), generate_id()

    async def setup():
        conn = await asyncpg.connect(dsn)
        await apply_schema(conn)
        try:
            await conn.execute("INSERT INTO organizations (id) VALUES ($1)", oid)
            lt_id = await conn.fetchval("SELECT id FROM link_types WHERE slug='website'")
            await conn.execute(
                "INSERT INTO links (id, entity_type, entity_id, url, link_type_id)"
                " VALUES ($1, 'organization', $2, 'https://example.com', $3)",
                lid,
                oid,
                lt_id,
            )
        finally:
            await conn.close()

    async def teardown():
        conn = await asyncpg.connect(dsn)
        try:
            await conn.execute("DELETE FROM links WHERE entity_id=$1", oid)
            await conn.execute("DELETE FROM organizations WHERE id=$1", oid)
        finally:
            await conn.close()

    asyncio.run(setup())
    yield oid, lid
    asyncio.run(teardown())


def test_links_new_row_returns_form(client, org_and_link):
    oid, _ = org_and_link
    r = client.get(f"/admin/orgs/{oid}/links/new-row/", headers=HTMX_HEADERS)
    assert r.status_code == 200
    assert "<form" in r.text


def test_links_create(client, org_and_link):
    oid, _ = org_and_link
    dsn = _dsn()

    async def get_lt():
        conn = await asyncpg.connect(dsn)
        try:
            return await conn.fetchval("SELECT id FROM link_types WHERE slug='website'")
        finally:
            await conn.close()

    lt_id = asyncio.run(get_lt())
    r = client.post(
        f"/admin/orgs/{oid}/links/",
        headers=HTMX_HEADERS,
        data={"url": "https://new.example.com", "link_type_id": lt_id, "is_active": "true"},
    )
    assert r.status_code == 200
    assert "https://new.example.com" in r.text


def test_links_read_row_returns_row(client, org_and_link):
    oid, lid = org_and_link
    r = client.get(f"/admin/orgs/{oid}/links/{lid}/read-row/", headers=HTMX_HEADERS)
    assert r.status_code == 200
    assert "https://example.com" in r.text
    assert "<form" not in r.text


def test_links_create_canonical_demotes_existing(client, org_and_link):
    """Creating a second canonical link must demote the first."""
    dsn = _dsn()
    oid, _ = org_and_link

    async def make_first_canonical():
        conn = await asyncpg.connect(dsn)
        try:
            lt_id = await conn.fetchval("SELECT id FROM link_types WHERE slug='website'")
            first_id = generate_id()
            await conn.execute(
                "INSERT INTO links (id, entity_type, entity_id, url, link_type_id, is_canonical)"
                " VALUES ($1, 'organization', $2, 'https://first.example.com', $3, TRUE)",
                first_id, oid, lt_id,
            )
            return first_id, lt_id
        finally:
            await conn.close()

    async def is_canonical(link_id):
        conn = await asyncpg.connect(dsn)
        try:
            return await conn.fetchval("SELECT is_canonical FROM links WHERE id=$1", link_id)
        finally:
            await conn.close()

    first_id, lt_id = asyncio.run(make_first_canonical())
    assert asyncio.run(is_canonical(first_id)) is True

    r = client.post(
        f"/admin/orgs/{oid}/links/",
        headers=HTMX_HEADERS,
        data={"url": "https://second.example.com", "link_type_id": lt_id,
              "is_active": "true", "is_canonical": "true"},
    )
    assert r.status_code == 200
    assert asyncio.run(is_canonical(first_id)) is False


def test_links_edit_row_returns_form(client, org_and_link):
    oid, lid = org_and_link
    r = client.get(f"/admin/orgs/{oid}/links/{lid}/edit-row/", headers=HTMX_HEADERS)
    assert r.status_code == 200
    assert "<form" in r.text


def test_links_update(client, org_and_link):
    oid, lid = org_and_link
    dsn = _dsn()

    async def get_lt():
        conn = await asyncpg.connect(dsn)
        try:
            return await conn.fetchval("SELECT id FROM link_types WHERE slug='website'")
        finally:
            await conn.close()

    lt_id = asyncio.run(get_lt())
    r = client.post(
        f"/admin/orgs/{oid}/links/{lid}/edit-row/",
        headers=HTMX_HEADERS,
        data={"url": "https://updated.example.com", "link_type_id": lt_id, "is_active": "true"},
    )
    assert r.status_code == 200
    assert "https://updated.example.com" in r.text


def test_links_delete(client, org_and_link):
    oid, lid = org_and_link
    r = client.delete(f"/admin/orgs/{oid}/links/{lid}/", headers=HTMX_HEADERS)
    assert r.status_code == 200


def test_links_delete_unknown_returns_404(client, org_and_link):
    oid, _ = org_and_link
    r = client.delete(f"/admin/orgs/{oid}/links/{generate_id()}/", headers=HTMX_HEADERS)
    assert r.status_code == 404
