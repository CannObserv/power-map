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
