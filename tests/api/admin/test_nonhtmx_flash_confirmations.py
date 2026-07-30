"""Integration tests: §32 non-HTMX fallbacks carry a ?flash= confirmation (#351).

#349 gave non-HTMX mutations a 303 fallback, but the redirect dropped the
confirmation the HTMX HX-Trigger flash provides. #351 appends a ?flash= key via
``with_flash`` so the target detail/list route surfaces it through
``resolve_query_flash``. These tests drive representative create/edit/delete/
conflict paths without HX-Request and assert (a) the redirect carries the right
key, and (b) following the redirect renders the flash message on the page.
"""

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from src.api.admin.deps import SHARED_FLASH_MESSAGES, get_db
from src.api.main import app
from src.core.db import generate_id

pytestmark = [pytest.mark.integration]

AUTH_HEADERS = {"X-ExeDev-UserID": "usr_test", "X-ExeDev-Email": "admin@test.com"}


@pytest_asyncio.fixture(loop_scope="session")
async def db(db_pool):
    async with db_pool.acquire() as conn:
        tr = conn.transaction()
        await tr.start()
        try:
            yield conn
        finally:
            await tr.rollback()


@pytest_asyncio.fixture(loop_scope="session")
async def client(db):
    async def _get_db_override():
        yield db

    app.dependency_overrides[get_db] = _get_db_override
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test", follow_redirects=False
    ) as c:
        yield c
    app.dependency_overrides.pop(get_db, None)


@pytest_asyncio.fixture(loop_scope="session")
async def following_client(db):
    async def _get_db_override():
        yield db

    app.dependency_overrides[get_db] = _get_db_override
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test", follow_redirects=True
    ) as c:
        yield c
    app.dependency_overrides.pop(get_db, None)


async def _seed_org(db) -> str:
    oid = generate_id()
    await db.execute("INSERT INTO organizations (id) VALUES ($1)", oid)
    return oid


def _flash_body(key: str) -> str:
    return SHARED_FLASH_MESSAGES[key][1]


# --- factory create/edit success carries ?flash=saved -------------------------


async def test_link_create_nonhtmx_flashes_saved(client, db):
    oid = await _seed_org(db)
    lt_id = await db.fetchval("SELECT id FROM link_types WHERE slug='website'")
    r = await client.post(
        f"/admin/orgs/{oid}/links/",
        data={"url": "https://example.com/", "link_type_id": lt_id, "is_active": "true"},
        headers=AUTH_HEADERS,
    )
    assert r.status_code == 303
    assert r.headers["location"] == f"/admin/orgs/{oid}/?flash=saved"


async def test_link_create_conflict_nonhtmx_flashes_exists(client, db):
    oid = await _seed_org(db)
    lt_id = await db.fetchval("SELECT id FROM link_types WHERE slug='website'")
    payload = {"url": "https://dup.example/", "link_type_id": lt_id, "is_active": "true"}
    await client.post(f"/admin/orgs/{oid}/links/", data=payload, headers=AUTH_HEADERS)
    r = await client.post(f"/admin/orgs/{oid}/links/", data=payload, headers=AUTH_HEADERS)
    assert r.status_code == 303
    assert r.headers["location"] == f"/admin/orgs/{oid}/?flash=exists"


async def test_contact_invalid_nonhtmx_flashes_invalid(client, db):
    oid = await _seed_org(db)
    r = await client.post(
        f"/admin/orgs/{oid}/contacts/",
        data={"contact_type": "email", "value": "not-an-email", "display_label": ""},
        headers=AUTH_HEADERS,
    )
    assert r.status_code == 303
    assert r.headers["location"] == f"/admin/orgs/{oid}/?flash=invalid"


# --- the flash actually renders on the followed page --------------------------


async def test_followed_redirect_renders_flash_message(following_client, db):
    oid = await _seed_org(db)
    lt_id = await db.fetchval("SELECT id FROM link_types WHERE slug='website'")
    r = await following_client.post(
        f"/admin/orgs/{oid}/links/",
        data={"url": "https://render.example/", "link_type_id": lt_id, "is_active": "true"},
        headers=AUTH_HEADERS,
    )
    assert r.status_code == 200
    assert _flash_body("saved") in r.text


# --- a settings-catalog target (list route) renders the shared flash ----------


async def test_settings_link_types_list_renders_flash(following_client, db):
    r = await following_client.get("/admin/settings/link-types/?flash=saved", headers=AUTH_HEADERS)
    assert r.status_code == 200
    assert _flash_body("saved") in r.text
