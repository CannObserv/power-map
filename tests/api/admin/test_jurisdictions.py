"""Integration tests for the admin jurisdictions list + detail views (#275)."""

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient

from src.api.main import app
from src.core.db import generate_id

pytestmark = [pytest.mark.integration]

AUTH_HEADERS = {
    "X-ExeDev-UserID": "usr_test",
    "X-ExeDev-Email": "admin@test.com",
}


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


@pytest_asyncio.fixture(loop_scope="session")
async def jur_id(db_pool):
    """Insert a county jurisdiction under a unique marker, yield it, then delete."""
    jid = generate_id()
    marker = jid[-10:].lower()
    name = f"Testburg {marker} County"
    slug = f"test-{marker}"
    async with db_pool.acquire() as conn:
        type_id = await conn.fetchval("SELECT id FROM jurisdiction_types WHERE slug='county'")
        await conn.execute(
            "INSERT INTO jurisdictions (id, slug, name, type_id) VALUES ($1,$2,$3,$4)",
            jid,
            slug,
            name,
            type_id,
        )
    yield {"id": jid, "marker": marker, "name": name, "slug": slug}
    async with db_pool.acquire() as conn:
        await conn.execute("DELETE FROM jurisdictions WHERE id=$1", jid)


# ---------------------------------------------------------------------------
# List
# ---------------------------------------------------------------------------


async def test_list_returns_200(client, jur_id):
    r = client.get("/admin/jurisdictions/", headers=AUTH_HEADERS, params={"q": jur_id["marker"]})
    assert r.status_code == 200
    assert jur_id["name"] in r.text


async def test_list_redirects_unauthenticated(client):
    r = client.get("/admin/jurisdictions/", follow_redirects=False)
    assert r.status_code in (302, 307)
    assert "/__exe.dev/login" in r.headers["location"]


async def test_list_has_type_filter_options(client):
    r = client.get("/admin/jurisdictions/", headers=AUTH_HEADERS)
    assert r.status_code == 200
    assert "Legislative District" in r.text
    assert "County" in r.text


async def test_list_type_filter(client, jur_id):
    # county jurisdiction absent when filtered to city, present when filtered to county
    r_city = client.get(
        "/admin/jurisdictions/",
        headers=AUTH_HEADERS,
        params={"q": jur_id["marker"], "type": "city"},
    )
    assert jur_id["name"] not in r_city.text
    r_county = client.get(
        "/admin/jurisdictions/",
        headers=AUTH_HEADERS,
        params={"q": jur_id["marker"], "type": "county"},
    )
    assert jur_id["name"] in r_county.text


async def test_list_htmx_returns_region_only(client, jur_id):
    r = client.get(
        "/admin/jurisdictions/",
        headers={**AUTH_HEADERS, "HX-Request": "true"},
        params={"q": jur_id["marker"]},
    )
    assert r.status_code == 200
    assert jur_id["name"] in r.text
    # region partial carries no full-page chrome
    assert "<html" not in r.text.lower()
    assert "admin-sidebar" not in r.text


# ---------------------------------------------------------------------------
# Navigation
# ---------------------------------------------------------------------------


async def test_nav_link_present_and_current(client):
    r = client.get("/admin/jurisdictions/", headers=AUTH_HEADERS)
    assert r.status_code == 200
    # sidebar link renders and is marked current on the jurisdictions page
    assert 'href="/admin/jurisdictions/" aria-current="page"' in r.text
    assert ">Jurisdictions<" in r.text


# ---------------------------------------------------------------------------
# Detail — header
# ---------------------------------------------------------------------------


async def test_detail_returns_200_with_core_fields(client, jur_id):
    r = client.get(f"/admin/jurisdictions/{jur_id['id']}/", headers=AUTH_HEADERS)
    assert r.status_code == 200
    assert jur_id["name"] in r.text
    assert jur_id["slug"] in r.text
    assert "County" in r.text  # type display name
    assert "Valid from" in r.text  # validity row present


async def test_detail_unknown_id_404(client):
    r = client.get("/admin/jurisdictions/01JUNKNOWNJUNKNOWNJUNKNOW0/", headers=AUTH_HEADERS)
    assert r.status_code == 404


async def test_detail_redirects_unauthenticated(client, jur_id):
    r = client.get(f"/admin/jurisdictions/{jur_id['id']}/", follow_redirects=False)
    assert r.status_code in (302, 307)
    assert "/__exe.dev/login" in r.headers["location"]


# ---------------------------------------------------------------------------
# Detail — attachment panels (identifiers / links / addresses / contacts)
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(loop_scope="session")
async def jur_with_attachments(db_pool):
    """Jurisdiction seeded with one identifier, link, address, and contact."""
    jid = generate_id()
    marker = jid[-10:].lower()
    vals = {
        "id": jid,
        "name": f"Attachburg {marker}",
        "slug": f"attach-{marker}",
        "identifier": f"ocd-division/country:us/test:{marker}",
        "link": f"https://{marker}.example.gov",
        "address": f"{marker} Capitol Way, Olympia, WA 98501",
        "email": f"info-{marker}@example.gov",
    }
    addr_id = generate_id()
    async with db_pool.acquire() as conn:
        type_id = await conn.fetchval("SELECT id FROM jurisdiction_types WHERE slug='county'")
        await conn.execute(
            "INSERT INTO jurisdictions (id, slug, name, type_id) VALUES ($1,$2,$3,$4)",
            jid,
            vals["slug"],
            vals["name"],
            type_id,
        )
        ocd_type = await conn.fetchval(
            "SELECT id FROM entity_identifier_types WHERE slug='jur_ocd'"
        )
        await conn.execute(
            "INSERT INTO identifiers (id, entity_id, entity_identifier_type_id, value)"
            " VALUES ($1,$2,$3,$4)",
            generate_id(),
            jid,
            ocd_type,
            vals["identifier"],
        )
        link_type = await conn.fetchval("SELECT id FROM link_types ORDER BY id LIMIT 1")
        await conn.execute(
            "INSERT INTO links (id, entity_type, entity_id, url, link_type_id)"
            " VALUES ($1,'jurisdiction',$2,$3,$4)",
            generate_id(),
            jid,
            vals["link"],
            link_type,
        )
        await conn.execute(
            "INSERT INTO addresses (id, standardized, country) VALUES ($1,$2,'US')",
            addr_id,
            vals["address"],
        )
        await conn.execute(
            "INSERT INTO entity_addresses (id, entity_type, entity_id, address_id, address_type)"
            " VALUES ($1,'jurisdiction',$2,$3,'mailing')",
            generate_id(),
            jid,
            addr_id,
        )
        await conn.execute(
            "INSERT INTO contact_methods (id, entity_type, entity_id, contact_type, value)"
            " VALUES ($1,'jurisdiction',$2,'email',$3)",
            generate_id(),
            jid,
            vals["email"],
        )
    yield vals
    async with db_pool.acquire() as conn:
        await conn.execute("DELETE FROM identifiers WHERE entity_id=$1", jid)
        await conn.execute("DELETE FROM links WHERE entity_id=$1", jid)
        await conn.execute("DELETE FROM contact_methods WHERE entity_id=$1", jid)
        await conn.execute("DELETE FROM entity_addresses WHERE entity_id=$1", jid)
        await conn.execute("DELETE FROM addresses WHERE id=$1", addr_id)
        await conn.execute("DELETE FROM jurisdictions WHERE id=$1", jid)


async def test_detail_shows_identifier(client, jur_with_attachments):
    r = client.get(f"/admin/jurisdictions/{jur_with_attachments['id']}/", headers=AUTH_HEADERS)
    assert jur_with_attachments["identifier"] in r.text


async def test_detail_shows_link(client, jur_with_attachments):
    r = client.get(f"/admin/jurisdictions/{jur_with_attachments['id']}/", headers=AUTH_HEADERS)
    assert jur_with_attachments["link"] in r.text


async def test_detail_shows_address(client, jur_with_attachments):
    r = client.get(f"/admin/jurisdictions/{jur_with_attachments['id']}/", headers=AUTH_HEADERS)
    assert jur_with_attachments["address"] in r.text


async def test_detail_shows_contact(client, jur_with_attachments):
    r = client.get(f"/admin/jurisdictions/{jur_with_attachments['id']}/", headers=AUTH_HEADERS)
    assert jur_with_attachments["email"] in r.text
