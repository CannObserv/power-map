"""Integration tests for org-jurisdiction affiliations, both sides (#275 Phase 3)."""

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from src.api.admin.deps import get_db
from src.api.main import app
from src.core.db import generate_id
from tests.db_utils import row_version

pytestmark = [pytest.mark.integration]

AUTH_HEADERS = {"X-ExeDev-UserID": "usr_test", "X-ExeDev-Email": "admin@test.com"}
HX = {**AUTH_HEADERS, "HX-Request": "true"}

_INSERT_AFF = (
    "INSERT INTO organization_jurisdiction_affiliations"
    " (id, organization_id, jurisdiction_id, affiliation_type_id) VALUES ($1,$2,$3,$4)"
)


@pytest_asyncio.fixture(loop_scope="session")
async def db(db_pool):
    """Pool-acquired connection wrapped in a rolled-back transaction."""
    async with db_pool.acquire() as conn:
        tr = conn.transaction()
        await tr.start()
        try:
            yield conn
        finally:
            await tr.rollback()


@pytest_asyncio.fixture(loop_scope="session")
async def client(db):
    """AsyncClient with app, overriding get_db to use the test connection."""

    async def _get_db_override():
        yield db

    app.dependency_overrides[get_db] = _get_db_override
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test", follow_redirects=True
    ) as c:
        yield c
    app.dependency_overrides.pop(get_db, None)


@pytest_asyncio.fixture(loop_scope="session")
async def county_type_id(db):
    return await db.fetchval("SELECT id FROM jurisdiction_types WHERE slug='county'")


@pytest_asyncio.fixture(loop_scope="session")
async def aff_type_id(db):
    return await db.fetchval(
        "SELECT id FROM organization_jurisdiction_affiliation_types WHERE slug='governing'"
    )


@pytest_asyncio.fixture(loop_scope="session")
async def jur_and_org(db, county_type_id):
    """A jurisdiction + an org."""
    jid, oid = generate_id(), generate_id()
    await db.execute(
        "INSERT INTO jurisdictions (id, slug, name, type_id) VALUES ($1,$2,$3,$4)",
        jid,
        f"aff-{jid[-8:].lower()}",
        "Affil County",
        county_type_id,
    )
    await db.execute("INSERT INTO organizations (id) VALUES ($1)", oid)
    return {"jur": jid, "org": oid}


# ---------------------------------------------------------------------------
# Jurisdiction side — "Affiliated organizations"
# ---------------------------------------------------------------------------


async def test_jur_affiliation_new_row_form(client, jur_and_org):
    r = await client.get(
        f"/admin/jurisdictions/{jur_and_org['jur']}/affiliations/new-row/", headers=HX
    )
    assert r.status_code == 200
    assert "/admin/orgs/search/" in r.text  # org typeahead
    assert 'name="affiliation_type_id"' in r.text


async def test_jur_affiliation_add(client, jur_and_org, aff_type_id, db):
    r = await client.post(
        f"/admin/jurisdictions/{jur_and_org['jur']}/affiliations/",
        headers=HX,
        data={"organization_id": jur_and_org["org"], "affiliation_type_id": aff_type_id},
    )
    assert r.status_code == 200
    aid = await db.fetchval(
        "SELECT id FROM organization_jurisdiction_affiliations"
        " WHERE jurisdiction_id=$1 AND organization_id=$2",
        jur_and_org["jur"],
        jur_and_org["org"],
    )
    assert aid is not None


async def test_jur_affiliation_duplicate_lands_on_the_form_row(
    client, jur_and_org, aff_type_id, db
):
    """htmx swaps no 4xx, so an HTMX error is a 200 retargeted onto the form row (#683)."""
    await db.execute(
        _INSERT_AFF, generate_id(), jur_and_org["org"], jur_and_org["jur"], aff_type_id
    )
    r = await client.post(
        f"/admin/jurisdictions/{jur_and_org['jur']}/affiliations/",
        headers=HX,
        data={"organization_id": jur_and_org["org"], "affiliation_type_id": aff_type_id},
    )
    assert r.status_code == 200
    assert r.headers["HX-Retarget"] == "#affiliation-row-new"
    assert "already has that affiliation" in r.text


async def test_jur_affiliation_duplicate_non_htmx_keeps_409(client, jur_and_org, aff_type_id, db):
    await db.execute(
        _INSERT_AFF, generate_id(), jur_and_org["org"], jur_and_org["jur"], aff_type_id
    )
    r = await client.post(
        f"/admin/jurisdictions/{jur_and_org['jur']}/affiliations/",
        headers=AUTH_HEADERS,
        data={"organization_id": jur_and_org["org"], "affiliation_type_id": aff_type_id},
    )
    assert r.status_code == 409


async def test_jur_affiliation_missing_org_lands_on_the_form_row(client, jur_and_org, aff_type_id):
    r = await client.post(
        f"/admin/jurisdictions/{jur_and_org['jur']}/affiliations/",
        headers=HX,
        data={"organization_id": "", "affiliation_type_id": aff_type_id},
    )
    assert r.status_code == 200
    assert r.headers["HX-Retarget"] == "#affiliation-row-new"
    assert "Select an organization" in r.text


async def test_jur_affiliation_delete(client, jur_and_org, aff_type_id, db):
    aid = generate_id()
    await db.execute(_INSERT_AFF, aid, jur_and_org["org"], jur_and_org["jur"], aff_type_id)
    r = await client.request(
        "DELETE", f"/admin/jurisdictions/{jur_and_org['jur']}/affiliations/{aid}/", headers=HX
    )
    assert r.status_code == 200
    assert (
        await db.fetchval("SELECT id FROM organization_jurisdiction_affiliations WHERE id=$1", aid)
        is None
    )


# ---------------------------------------------------------------------------
# Org side — reciprocal "Affiliated jurisdictions"
# ---------------------------------------------------------------------------


async def test_org_affiliation_new_row_form(client, jur_and_org):
    r = await client.get(
        f"/admin/orgs/{jur_and_org['org']}/jurisdiction-affiliations/new-row/", headers=HX
    )
    assert r.status_code == 200
    assert "/admin/jurisdictions/search/" in r.text  # jurisdiction typeahead
    assert 'name="affiliation_type_id"' in r.text


async def test_org_affiliation_add(client, jur_and_org, aff_type_id, db):
    r = await client.post(
        f"/admin/orgs/{jur_and_org['org']}/jurisdiction-affiliations/",
        headers=HX,
        data={"jurisdiction_id": jur_and_org["jur"], "affiliation_type_id": aff_type_id},
    )
    assert r.status_code == 200
    aid = await db.fetchval(
        "SELECT id FROM organization_jurisdiction_affiliations"
        " WHERE jurisdiction_id=$1 AND organization_id=$2",
        jur_and_org["jur"],
        jur_and_org["org"],
    )
    assert aid is not None


async def test_org_affiliation_duplicate_lands_on_the_form_row(
    client, jur_and_org, aff_type_id, db
):
    await db.execute(
        _INSERT_AFF, generate_id(), jur_and_org["org"], jur_and_org["jur"], aff_type_id
    )
    r = await client.post(
        f"/admin/orgs/{jur_and_org['org']}/jurisdiction-affiliations/",
        headers=HX,
        data={"jurisdiction_id": jur_and_org["jur"], "affiliation_type_id": aff_type_id},
    )
    assert r.status_code == 200
    assert r.headers["HX-Retarget"] == "#jur-affiliation-row-new"
    assert "already has that affiliation" in r.text


async def test_org_detail_shows_affiliations_panel(client, jur_and_org, aff_type_id, db):
    await db.execute(
        _INSERT_AFF, generate_id(), jur_and_org["org"], jur_and_org["jur"], aff_type_id
    )
    r = await client.get(f"/admin/orgs/{jur_and_org['org']}/", headers=AUTH_HEADERS)
    assert r.status_code == 200
    assert "Affiliated jurisdictions" in r.text
    assert "Affil County" in r.text  # the affiliated jurisdiction renders in the panel


async def test_org_affiliation_delete(client, jur_and_org, aff_type_id, db):
    aid = generate_id()
    await db.execute(_INSERT_AFF, aid, jur_and_org["org"], jur_and_org["jur"], aff_type_id)
    r = await client.request(
        "DELETE", f"/admin/orgs/{jur_and_org['org']}/jurisdiction-affiliations/{aid}/", headers=HX
    )
    assert r.status_code == 200
    assert (
        await db.fetchval("SELECT id FROM organization_jurisdiction_affiliations WHERE id=$1", aid)
        is None
    )


# ---------------------------------------------------------------------------
# Touch propagation to the jurisdiction side (touch trigger)
# ---------------------------------------------------------------------------


async def test_affiliation_add_touches_jurisdiction(client, jur_and_org, aff_type_id, db):
    before = await row_version(db, "jurisdictions", jur_and_org["jur"])
    r = await client.post(
        f"/admin/jurisdictions/{jur_and_org['jur']}/affiliations/",
        headers=HX,
        data={"organization_id": jur_and_org["org"], "affiliation_type_id": aff_type_id},
    )
    assert r.status_code == 200
    # the affiliated jurisdiction (not just the org) is touched
    assert await row_version(db, "jurisdictions", jur_and_org["jur"]) != before


async def test_affiliation_delete_touches_jurisdiction(client, jur_and_org, aff_type_id, db):
    aid = generate_id()
    await db.execute(_INSERT_AFF, aid, jur_and_org["org"], jur_and_org["jur"], aff_type_id)
    before = await row_version(db, "jurisdictions", jur_and_org["jur"])
    r = await client.request(
        "DELETE", f"/admin/orgs/{jur_and_org['org']}/jurisdiction-affiliations/{aid}/", headers=HX
    )
    assert r.status_code == 200
    assert await row_version(db, "jurisdictions", jur_and_org["jur"]) != before
