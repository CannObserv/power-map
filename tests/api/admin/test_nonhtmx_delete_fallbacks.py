"""Integration tests: delete handlers 303-redirect non-HTMX clients (#349).

Each of the 12 delete-family handlers flagged in #349 returned an HTMX-shaped
body (empty string, OOB fragment) to any caller. These tests drive each route
without the HX-Request header and assert the §32 fallback: the mutation still
happens, then a 303 redirect to the owning detail/list page. Factory-made
handlers (contacts/links/identifiers/citations) are covered through one
representative mount each; citations twice to pin both ``_dest`` paths
(plain ``detail_url`` and the ``redirect_resolver`` indirection).
"""

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from src.api.admin.deps import get_db
from src.api.main import app
from src.core.db import generate_id

pytestmark = [pytest.mark.integration]

AUTH_HEADERS = {"X-ExeDev-UserID": "usr_test", "X-ExeDev-Email": "admin@test.com"}


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
    """Non-following AsyncClient so 303 responses are observable."""

    async def _get_db_override():
        yield db

    app.dependency_overrides[get_db] = _get_db_override
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test", follow_redirects=False
    ) as c:
        yield c
    app.dependency_overrides.pop(get_db, None)


async def _seed_org(db) -> str:
    oid = generate_id()
    await db.execute("INSERT INTO organizations (id) VALUES ($1)", oid)
    return oid


async def _seed_person(db) -> str:
    pid = generate_id()
    await db.execute("INSERT INTO people (id) VALUES ($1)", pid)
    return pid


async def _seed_jurisdiction(db, slug_marker: str) -> str:
    jid = generate_id()
    type_id = await db.fetchval("SELECT id FROM jurisdiction_types WHERE slug='county'")
    await db.execute(
        "INSERT INTO jurisdictions (id, slug, name, type_id) VALUES ($1,$2,$3,$4)",
        jid,
        f"test-349-{slug_marker}-{jid[-6:].lower()}",
        f"Test 349 {slug_marker}",
        type_id,
    )
    return jid


async def _seed_entity_address(db, entity_type: str, entity_id: str) -> str:
    address_id, ea_id = generate_id(), generate_id()
    await db.execute(
        "INSERT INTO addresses (id, standardized, country) VALUES ($1,'1 Test St','US')",
        address_id,
    )
    await db.execute(
        "INSERT INTO entity_addresses (id, entity_type, entity_id, address_id, address_type)"
        " VALUES ($1,$2,$3,$4,'physical')",
        ea_id,
        entity_type,
        entity_id,
        address_id,
    )
    return ea_id


def _assert_303(r, expected_location: str):
    assert r.status_code == 303, f"expected 303, got {r.status_code}: {r.text[:200]}"
    assert r.headers["location"] == expected_location


# --- shared factories (one representative mount each) ---


async def test_contact_delete_nonhtmx_redirects(client, db):
    oid, cid = await _seed_org(db), generate_id()
    await db.execute(
        "INSERT INTO contact_methods (id, entity_type, entity_id, contact_type, value)"
        " VALUES ($1,'organization',$2,'phone','+13605551234')",
        cid,
        oid,
    )
    r = await client.delete(f"/admin/orgs/{oid}/contacts/{cid}/", headers=AUTH_HEADERS)
    _assert_303(r, f"/admin/orgs/{oid}/?flash=removed")
    assert await db.fetchval("SELECT count(*) FROM contact_methods WHERE id=$1", cid) == 0


async def test_link_delete_nonhtmx_redirects(client, db):
    oid, lid = await _seed_org(db), generate_id()
    lt_id = await db.fetchval("SELECT id FROM link_types WHERE slug='website'")
    await db.execute(
        "INSERT INTO links (id, entity_type, entity_id, url, link_type_id)"
        " VALUES ($1,'organization',$2,'https://example.com/',$3)",
        lid,
        oid,
        lt_id,
    )
    r = await client.delete(f"/admin/orgs/{oid}/links/{lid}/", headers=AUTH_HEADERS)
    _assert_303(r, f"/admin/orgs/{oid}/?flash=removed")
    assert await db.fetchval("SELECT count(*) FROM links WHERE id=$1", lid) == 0


async def test_identifier_delete_nonhtmx_redirects(client, db):
    oid, iid = await _seed_org(db), generate_id()
    type_id = await db.fetchval(
        "SELECT id FROM entity_identifier_types WHERE entity_type='organization' LIMIT 1"
    )
    if not type_id:
        pytest.skip("No organization identifier types seeded")
    await db.execute(
        "INSERT INTO identifiers (id, entity_id, entity_identifier_type_id, value)"
        " VALUES ($1,$2,$3,'TEST-349')",
        iid,
        oid,
        type_id,
    )
    r = await client.delete(f"/admin/orgs/{oid}/identifiers/{iid}/", headers=AUTH_HEADERS)
    _assert_303(r, f"/admin/orgs/{oid}/?flash=removed")
    assert await db.fetchval("SELECT count(*) FROM identifiers WHERE id=$1", iid) == 0


async def test_citation_delete_nonhtmx_redirects_detail_url(client, db):
    oid, cid = await _seed_org(db), generate_id()
    await db.execute(
        "INSERT INTO citations (id, entity_type, entity_id, url)"
        " VALUES ($1,'organization',$2,'https://example.com/src')",
        cid,
        oid,
    )
    r = await client.delete(f"/admin/orgs/{oid}/citations/{cid}/", headers=AUTH_HEADERS)
    _assert_303(r, f"/admin/orgs/{oid}/?flash=removed")
    assert await db.fetchval("SELECT count(*) FROM citations WHERE id=$1", cid) == 0


async def test_citation_delete_nonhtmx_redirects_via_resolver(client, db):
    """person_name citations resolve the redirect to the owning person (#319)."""
    pid, nid, cid = await _seed_person(db), generate_id(), generate_id()
    await db.execute(
        "INSERT INTO person_names (id, person_id, name, is_canonical)"
        " VALUES ($1,$2,'Test ThreeFourNine',TRUE)",
        nid,
        pid,
    )
    await db.execute(
        "INSERT INTO citations (id, entity_type, entity_id, url)"
        " VALUES ($1,'person_name',$2,'https://example.com/src')",
        cid,
        nid,
    )
    r = await client.delete(f"/admin/person-names/{nid}/citations/{cid}/", headers=AUTH_HEADERS)
    _assert_303(r, f"/admin/people/{pid}/?flash=removed")
    assert await db.fetchval("SELECT count(*) FROM citations WHERE id=$1", cid) == 0


# --- standalone handlers ---


async def test_org_address_delete_nonhtmx_redirects(client, db):
    oid = await _seed_org(db)
    ea_id = await _seed_entity_address(db, "organization", oid)
    r = await client.delete(f"/admin/orgs/{oid}/addresses/{ea_id}/", headers=AUTH_HEADERS)
    _assert_303(r, f"/admin/orgs/{oid}/?flash=removed")
    assert await db.fetchval("SELECT count(*) FROM entity_addresses WHERE id=$1", ea_id) == 0


async def test_person_address_delete_nonhtmx_redirects(client, db):
    pid = await _seed_person(db)
    ea_id = await _seed_entity_address(db, "person", pid)
    r = await client.delete(f"/admin/people/{pid}/addresses/{ea_id}/", headers=AUTH_HEADERS)
    _assert_303(r, f"/admin/people/{pid}/?flash=removed")
    assert await db.fetchval("SELECT count(*) FROM entity_addresses WHERE id=$1", ea_id) == 0


async def test_jurisdiction_address_delete_nonhtmx_redirects(client, db):
    jid = await _seed_jurisdiction(db, "addr")
    ea_id = await _seed_entity_address(db, "jurisdiction", jid)
    r = await client.delete(f"/admin/jurisdictions/{jid}/addresses/{ea_id}/", headers=AUTH_HEADERS)
    _assert_303(r, f"/admin/jurisdictions/{jid}/?flash=removed")
    assert await db.fetchval("SELECT count(*) FROM entity_addresses WHERE id=$1", ea_id) == 0


async def test_relationship_delete_nonhtmx_redirects(client, db):
    jid_a = await _seed_jurisdiction(db, "rel-a")
    jid_b = await _seed_jurisdiction(db, "rel-b")
    rel_id = generate_id()
    rel_type = await db.fetchval(
        "SELECT id FROM jurisdiction_relationship_types WHERE slug='is_fully_contained_by'"
    )
    await db.execute(
        "INSERT INTO jurisdiction_relationships (id, from_id, to_id, rel_type_id)"
        " VALUES ($1,$2,$3,$4)",
        rel_id,
        jid_a,
        jid_b,
        rel_type,
    )
    r = await client.delete(
        f"/admin/jurisdictions/{jid_a}/relationships/{rel_id}/", headers=AUTH_HEADERS
    )
    _assert_303(r, f"/admin/jurisdictions/{jid_a}/?flash=removed")
    assert (
        await db.fetchval("SELECT count(*) FROM jurisdiction_relationships WHERE id=$1", rel_id)
        == 0
    )


async def _seed_affiliation(db, oid: str, jid: str) -> str:
    aff_id = generate_id()
    aff_type = await db.fetchval(
        "SELECT id FROM organization_jurisdiction_affiliation_types WHERE slug='governing'"
    )
    await db.execute(
        "INSERT INTO organization_jurisdiction_affiliations"
        " (id, organization_id, jurisdiction_id, affiliation_type_id) VALUES ($1,$2,$3,$4)",
        aff_id,
        oid,
        jid,
        aff_type,
    )
    return aff_id


async def test_jur_affiliation_delete_nonhtmx_redirects(client, db):
    oid, jid = await _seed_org(db), await _seed_jurisdiction(db, "jaff")
    aff_id = await _seed_affiliation(db, oid, jid)
    r = await client.delete(
        f"/admin/jurisdictions/{jid}/affiliations/{aff_id}/", headers=AUTH_HEADERS
    )
    _assert_303(r, f"/admin/jurisdictions/{jid}/?flash=removed")
    assert (
        await db.fetchval(
            "SELECT count(*) FROM organization_jurisdiction_affiliations WHERE id=$1", aff_id
        )
        == 0
    )


async def test_org_affiliation_delete_nonhtmx_redirects(client, db):
    oid, jid = await _seed_org(db), await _seed_jurisdiction(db, "oaff")
    aff_id = await _seed_affiliation(db, oid, jid)
    r = await client.delete(
        f"/admin/orgs/{oid}/jurisdiction-affiliations/{aff_id}/", headers=AUTH_HEADERS
    )
    _assert_303(r, f"/admin/orgs/{oid}/?flash=removed")
    assert (
        await db.fetchval(
            "SELECT count(*) FROM organization_jurisdiction_affiliations WHERE id=$1", aff_id
        )
        == 0
    )


async def test_children_remove_nonhtmx_redirects(client, db):
    parent = await _seed_org(db)
    child = generate_id()
    await db.execute("INSERT INTO organizations (id, parent_id) VALUES ($1,$2)", child, parent)
    r = await client.delete(f"/admin/orgs/{parent}/children/{child}/", headers=AUTH_HEADERS)
    _assert_303(r, f"/admin/orgs/{parent}/?flash=removed")
    assert await db.fetchval("SELECT parent_id FROM organizations WHERE id=$1", child) is None


async def test_api_key_delete_nonhtmx_redirects(client, db):
    kid = generate_id()
    await db.execute(
        "INSERT INTO app_users (id, email) VALUES ('usr_test','admin@test.com')"
        " ON CONFLICT (id) DO NOTHING"
    )
    await db.execute(
        "INSERT INTO api_keys (id, user_id, label, key_prefix, key_hash)"
        " VALUES ($1,'usr_test','t349','pm_t349xx',$2)",
        kid,
        "349afeed" * 8,
    )
    r = await client.delete(f"/admin/settings/api-keys/{kid}/", headers=AUTH_HEADERS)
    _assert_303(r, "/admin/settings/api-keys/?flash=removed")
    assert await db.fetchval("SELECT count(*) FROM api_keys WHERE id=$1", kid) == 0


# --- HTMX behavior unchanged ---


async def test_contact_delete_htmx_still_returns_empty_body(client, db):
    oid, cid = await _seed_org(db), generate_id()
    await db.execute(
        "INSERT INTO contact_methods (id, entity_type, entity_id, contact_type, value)"
        " VALUES ($1,'organization',$2,'phone','+13605551234')",
        cid,
        oid,
    )
    r = await client.delete(
        f"/admin/orgs/{oid}/contacts/{cid}/", headers={**AUTH_HEADERS, "HX-Request": "true"}
    )
    assert r.status_code == 200
    assert r.text == ""
