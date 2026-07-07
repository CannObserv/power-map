"""Integration tests for org-jurisdiction affiliations, both sides (#275 Phase 3)."""

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient

from src.api.main import app
from src.core.db import generate_id
from tests.api.admin.conftest import jurisdiction_change_count

pytestmark = [pytest.mark.integration]

AUTH_HEADERS = {"X-ExeDev-UserID": "usr_test", "X-ExeDev-Email": "admin@test.com"}
HX = {**AUTH_HEADERS, "HX-Request": "true"}

_INSERT_AFF = (
    "INSERT INTO organization_jurisdiction_affiliations"
    " (id, organization_id, jurisdiction_id, affiliation_type_id) VALUES ($1,$2,$3,$4)"
)


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


@pytest_asyncio.fixture(loop_scope="session")
async def county_type_id(db_pool):
    async with db_pool.acquire() as conn:
        return await conn.fetchval("SELECT id FROM jurisdiction_types WHERE slug='county'")


@pytest_asyncio.fixture(loop_scope="session")
async def aff_type_id(db_pool):
    async with db_pool.acquire() as conn:
        return await conn.fetchval(
            "SELECT id FROM organization_jurisdiction_affiliation_types WHERE slug='governing'"
        )


@pytest_asyncio.fixture(loop_scope="session")
async def jur_and_org(db_pool, county_type_id):
    """A jurisdiction + an org; affiliations + rows cleaned at teardown."""
    jid, oid = generate_id(), generate_id()
    async with db_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO jurisdictions (id, slug, name, type_id) VALUES ($1,$2,$3,$4)",
            jid,
            f"aff-{jid[-8:].lower()}",
            "Affil County",
            county_type_id,
        )
        await conn.execute("INSERT INTO organizations (id) VALUES ($1)", oid)
    yield {"jur": jid, "org": oid}
    async with db_pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM organization_jurisdiction_affiliations"
            " WHERE jurisdiction_id=$1 OR organization_id=$2",
            jid,
            oid,
        )
        await conn.execute("DELETE FROM jurisdictions WHERE id=$1", jid)
        await conn.execute("DELETE FROM organizations WHERE id=$1", oid)


# ---------------------------------------------------------------------------
# Jurisdiction side — "Affiliated organizations"
# ---------------------------------------------------------------------------


async def test_jur_affiliation_new_row_form(client, jur_and_org):
    r = client.get(f"/admin/jurisdictions/{jur_and_org['jur']}/affiliations/new-row/", headers=HX)
    assert r.status_code == 200
    assert "/admin/orgs/search/" in r.text  # org typeahead
    assert 'name="affiliation_type_id"' in r.text


async def test_jur_affiliation_add(client, jur_and_org, aff_type_id, db_pool):
    r = client.post(
        f"/admin/jurisdictions/{jur_and_org['jur']}/affiliations/",
        headers=HX,
        data={"organization_id": jur_and_org["org"], "affiliation_type_id": aff_type_id},
    )
    assert r.status_code == 200
    async with db_pool.acquire() as conn:
        aid = await conn.fetchval(
            "SELECT id FROM organization_jurisdiction_affiliations"
            " WHERE jurisdiction_id=$1 AND organization_id=$2",
            jur_and_org["jur"],
            jur_and_org["org"],
        )
    assert aid is not None


async def test_jur_affiliation_duplicate_409(client, jur_and_org, aff_type_id, db_pool):
    async with db_pool.acquire() as conn:
        await conn.execute(
            _INSERT_AFF, generate_id(), jur_and_org["org"], jur_and_org["jur"], aff_type_id
        )
    r = client.post(
        f"/admin/jurisdictions/{jur_and_org['jur']}/affiliations/",
        headers=HX,
        data={"organization_id": jur_and_org["org"], "affiliation_type_id": aff_type_id},
    )
    assert r.status_code == 409


async def test_jur_affiliation_missing_org_422(client, jur_and_org, aff_type_id):
    r = client.post(
        f"/admin/jurisdictions/{jur_and_org['jur']}/affiliations/",
        headers=HX,
        data={"organization_id": "", "affiliation_type_id": aff_type_id},
    )
    assert r.status_code == 422


async def test_jur_affiliation_delete(client, jur_and_org, aff_type_id, db_pool):
    aid = generate_id()
    async with db_pool.acquire() as conn:
        await conn.execute(_INSERT_AFF, aid, jur_and_org["org"], jur_and_org["jur"], aff_type_id)
    r = client.request(
        "DELETE", f"/admin/jurisdictions/{jur_and_org['jur']}/affiliations/{aid}/", headers=HX
    )
    assert r.status_code == 200
    async with db_pool.acquire() as conn:
        assert (
            await conn.fetchval(
                "SELECT id FROM organization_jurisdiction_affiliations WHERE id=$1", aid
            )
            is None
        )


# ---------------------------------------------------------------------------
# Org side — reciprocal "Affiliated jurisdictions"
# ---------------------------------------------------------------------------


async def test_org_affiliation_new_row_form(client, jur_and_org):
    r = client.get(
        f"/admin/orgs/{jur_and_org['org']}/jurisdiction-affiliations/new-row/", headers=HX
    )
    assert r.status_code == 200
    assert "/admin/jurisdictions/search/" in r.text  # jurisdiction typeahead
    assert 'name="affiliation_type_id"' in r.text


async def test_org_affiliation_add(client, jur_and_org, aff_type_id, db_pool):
    r = client.post(
        f"/admin/orgs/{jur_and_org['org']}/jurisdiction-affiliations/",
        headers=HX,
        data={"jurisdiction_id": jur_and_org["jur"], "affiliation_type_id": aff_type_id},
    )
    assert r.status_code == 200
    async with db_pool.acquire() as conn:
        aid = await conn.fetchval(
            "SELECT id FROM organization_jurisdiction_affiliations"
            " WHERE jurisdiction_id=$1 AND organization_id=$2",
            jur_and_org["jur"],
            jur_and_org["org"],
        )
    assert aid is not None


async def test_org_affiliation_duplicate_409(client, jur_and_org, aff_type_id, db_pool):
    async with db_pool.acquire() as conn:
        await conn.execute(
            _INSERT_AFF, generate_id(), jur_and_org["org"], jur_and_org["jur"], aff_type_id
        )
    r = client.post(
        f"/admin/orgs/{jur_and_org['org']}/jurisdiction-affiliations/",
        headers=HX,
        data={"jurisdiction_id": jur_and_org["jur"], "affiliation_type_id": aff_type_id},
    )
    assert r.status_code == 409


async def test_org_detail_shows_affiliations_panel(client, jur_and_org, aff_type_id, db_pool):
    async with db_pool.acquire() as conn:
        await conn.execute(
            _INSERT_AFF, generate_id(), jur_and_org["org"], jur_and_org["jur"], aff_type_id
        )
    r = client.get(f"/admin/orgs/{jur_and_org['org']}/", headers=AUTH_HEADERS)
    assert r.status_code == 200
    assert "Affiliated jurisdictions" in r.text
    assert "Affil County" in r.text  # the affiliated jurisdiction renders in the panel


async def test_org_affiliation_delete(client, jur_and_org, aff_type_id, db_pool):
    aid = generate_id()
    async with db_pool.acquire() as conn:
        await conn.execute(_INSERT_AFF, aid, jur_and_org["org"], jur_and_org["jur"], aff_type_id)
    r = client.request(
        "DELETE", f"/admin/orgs/{jur_and_org['org']}/jurisdiction-affiliations/{aid}/", headers=HX
    )
    assert r.status_code == 200
    async with db_pool.acquire() as conn:
        assert (
            await conn.fetchval(
                "SELECT id FROM organization_jurisdiction_affiliations WHERE id=$1", aid
            )
            is None
        )


# ---------------------------------------------------------------------------
# Change-feed propagation to the jurisdiction side (touch trigger)
# ---------------------------------------------------------------------------


async def test_affiliation_add_emits_jurisdiction_change_feed(
    client, jur_and_org, aff_type_id, db_pool
):
    before = await jurisdiction_change_count(db_pool, jur_and_org["jur"])
    r = client.post(
        f"/admin/jurisdictions/{jur_and_org['jur']}/affiliations/",
        headers=HX,
        data={"organization_id": jur_and_org["org"], "affiliation_type_id": aff_type_id},
    )
    assert r.status_code == 200
    # the affiliated jurisdiction (not just the org) surfaces on the change feed
    assert await jurisdiction_change_count(db_pool, jur_and_org["jur"]) > before


async def test_affiliation_delete_emits_jurisdiction_change_feed(
    client, jur_and_org, aff_type_id, db_pool
):
    aid = generate_id()
    async with db_pool.acquire() as conn:
        await conn.execute(_INSERT_AFF, aid, jur_and_org["org"], jur_and_org["jur"], aff_type_id)
    before = await jurisdiction_change_count(db_pool, jur_and_org["jur"])
    r = client.request(
        "DELETE", f"/admin/orgs/{jur_and_org['org']}/jurisdiction-affiliations/{aid}/", headers=HX
    )
    assert r.status_code == 200
    assert await jurisdiction_change_count(db_pool, jur_and_org["jur"]) > before
