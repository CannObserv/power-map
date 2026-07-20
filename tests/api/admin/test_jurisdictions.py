"""Integration tests for the admin jurisdictions list + detail views (#275)."""

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from src.api.admin.deps import get_db
from src.api.main import app
from src.core.db import generate_id

pytestmark = [pytest.mark.integration]

AUTH_HEADERS = {
    "X-ExeDev-UserID": "usr_test",
    "X-ExeDev-Email": "admin@test.com",
}


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
async def jur_id(db):
    """Insert a county jurisdiction under a unique marker, yield it."""
    jid = generate_id()
    marker = jid[-10:].lower()
    name = f"Testburg {marker} County"
    slug = f"test-{marker}"
    type_id = await db.fetchval("SELECT id FROM jurisdiction_types WHERE slug='county'")
    await db.execute(
        "INSERT INTO jurisdictions (id, slug, name, type_id) VALUES ($1,$2,$3,$4)",
        jid,
        slug,
        name,
        type_id,
    )
    return {"id": jid, "marker": marker, "name": name, "slug": slug}


# ---------------------------------------------------------------------------
# List
# ---------------------------------------------------------------------------


async def test_list_returns_200(client, jur_id):
    r = await client.get(
        "/admin/jurisdictions/", headers=AUTH_HEADERS, params={"q": jur_id["marker"]}
    )
    assert r.status_code == 200
    assert jur_id["name"] in r.text


async def test_list_redirects_unauthenticated(client):
    r = await client.get("/admin/jurisdictions/", follow_redirects=False)
    assert r.status_code in (302, 307)
    assert "/__exe.dev/login" in r.headers["location"]


async def test_list_has_type_filter_options(client):
    r = await client.get("/admin/jurisdictions/", headers=AUTH_HEADERS)
    assert r.status_code == 200
    assert "Legislative District" in r.text
    assert "County" in r.text


async def test_list_type_filter(client, jur_id):
    # county jurisdiction absent when filtered to city, present when filtered to county
    r_city = await client.get(
        "/admin/jurisdictions/",
        headers=AUTH_HEADERS,
        params={"q": jur_id["marker"], "type": "city"},
    )
    assert jur_id["name"] not in r_city.text
    r_county = await client.get(
        "/admin/jurisdictions/",
        headers=AUTH_HEADERS,
        params={"q": jur_id["marker"], "type": "county"},
    )
    assert jur_id["name"] in r_county.text


async def test_list_htmx_returns_region_only(client, jur_id):
    r = await client.get(
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
    r = await client.get("/admin/jurisdictions/", headers=AUTH_HEADERS)
    assert r.status_code == 200
    # sidebar link renders and is marked current on the jurisdictions page
    assert 'href="/admin/jurisdictions/" aria-current="page"' in r.text
    assert ">Jurisdictions<" in r.text


# ---------------------------------------------------------------------------
# Detail — header
# ---------------------------------------------------------------------------


async def test_detail_returns_200_with_core_fields(client, jur_id):
    r = await client.get(f"/admin/jurisdictions/{jur_id['id']}/", headers=AUTH_HEADERS)
    assert r.status_code == 200
    assert jur_id["name"] in r.text
    assert jur_id["slug"] in r.text
    assert "County" in r.text  # type display name
    assert "Valid from" in r.text  # validity row present


async def test_detail_unknown_id_404(client):
    r = await client.get("/admin/jurisdictions/01JUNKNOWNJUNKNOWNJUNKNOW0/", headers=AUTH_HEADERS)
    assert r.status_code == 404


async def test_detail_redirects_unauthenticated(client, jur_id):
    r = await client.get(f"/admin/jurisdictions/{jur_id['id']}/", follow_redirects=False)
    assert r.status_code in (302, 307)
    assert "/__exe.dev/login" in r.headers["location"]


# ---------------------------------------------------------------------------
# Detail — attachment panels (identifiers / links / addresses / contacts)
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(loop_scope="session")
async def jur_with_attachments(db):
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
    type_id = await db.fetchval("SELECT id FROM jurisdiction_types WHERE slug='county'")
    await db.execute(
        "INSERT INTO jurisdictions (id, slug, name, type_id) VALUES ($1,$2,$3,$4)",
        jid,
        vals["slug"],
        vals["name"],
        type_id,
    )
    ocd_type = await db.fetchval("SELECT id FROM entity_identifier_types WHERE slug='jur_ocd'")
    await db.execute(
        "INSERT INTO identifiers (id, entity_id, entity_identifier_type_id, value)"
        " VALUES ($1,$2,$3,$4)",
        generate_id(),
        jid,
        ocd_type,
        vals["identifier"],
    )
    link_type = await db.fetchval("SELECT id FROM link_types ORDER BY id LIMIT 1")
    await db.execute(
        "INSERT INTO links (id, entity_type, entity_id, url, link_type_id)"
        " VALUES ($1,'jurisdiction',$2,$3,$4)",
        generate_id(),
        jid,
        vals["link"],
        link_type,
    )
    await db.execute(
        "INSERT INTO addresses (id, standardized, country) VALUES ($1,$2,'US')",
        addr_id,
        vals["address"],
    )
    await db.execute(
        "INSERT INTO entity_addresses (id, entity_type, entity_id, address_id, address_type)"
        " VALUES ($1,'jurisdiction',$2,$3,'mailing')",
        generate_id(),
        jid,
        addr_id,
    )
    await db.execute(
        "INSERT INTO contact_methods (id, entity_type, entity_id, contact_type, value)"
        " VALUES ($1,'jurisdiction',$2,'email',$3)",
        generate_id(),
        jid,
        vals["email"],
    )
    return vals


async def test_detail_shows_identifier(client, jur_with_attachments):
    r = await client.get(
        f"/admin/jurisdictions/{jur_with_attachments['id']}/", headers=AUTH_HEADERS
    )
    assert jur_with_attachments["identifier"] in r.text


async def test_detail_shows_link(client, jur_with_attachments):
    r = await client.get(
        f"/admin/jurisdictions/{jur_with_attachments['id']}/", headers=AUTH_HEADERS
    )
    assert jur_with_attachments["link"] in r.text


async def test_detail_shows_address(client, jur_with_attachments):
    r = await client.get(
        f"/admin/jurisdictions/{jur_with_attachments['id']}/", headers=AUTH_HEADERS
    )
    assert jur_with_attachments["address"] in r.text


async def test_detail_shows_contact(client, jur_with_attachments):
    r = await client.get(
        f"/admin/jurisdictions/{jur_with_attachments['id']}/", headers=AUTH_HEADERS
    )
    assert jur_with_attachments["email"] in r.text


# ---------------------------------------------------------------------------
# Detail — graph panels (relationships / lineage / affiliations / roles)
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(loop_scope="session")
async def jur_with_graph(db):
    """Jurisdiction J wired to: a spatial edge (→ R), a lineage edge (→ P),
    an affiliated org, and a role referencing J."""
    marker = generate_id()[-10:].lower()
    ids = {
        k: generate_id()
        for k in ("j", "r", "p", "org", "role", "rel_spatial", "rel_lineage", "affil")
    }
    names = {
        "j": f"Graphville {marker}",
        "r": f"Parentstate {marker}",
        "p": f"Oldcounty {marker}",
        "org": f"Governing Body {marker}",
        "role_title": f"Delegate {marker}",
    }
    county = await db.fetchval("SELECT id FROM jurisdiction_types WHERE slug='county'")
    state = await db.fetchval("SELECT id FROM jurisdiction_types WHERE slug='state'")
    for key, tid, nm in (
        ("j", county, names["j"]),
        ("r", state, names["r"]),
        ("p", county, names["p"]),
    ):
        await db.execute(
            "INSERT INTO jurisdictions (id, slug, name, type_id) VALUES ($1,$2,$3,$4)",
            ids[key],
            f"{key}-{marker}",
            nm,
            tid,
        )
    contained = await db.fetchval(
        "SELECT id FROM jurisdiction_relationship_types WHERE slug='is_fully_contained_by'"
    )
    supersedes = await db.fetchval(
        "SELECT id FROM jurisdiction_relationship_types WHERE slug='supersedes'"
    )
    await db.execute(
        "INSERT INTO jurisdiction_relationships (id, from_id, to_id, rel_type_id)"
        " VALUES ($1,$2,$3,$4)",
        ids["rel_spatial"],
        ids["j"],
        ids["r"],
        contained,
    )
    await db.execute(
        "INSERT INTO jurisdiction_relationships (id, from_id, to_id, rel_type_id)"
        " VALUES ($1,$2,$3,$4)",
        ids["rel_lineage"],
        ids["j"],
        ids["p"],
        supersedes,
    )
    await db.execute("INSERT INTO organizations (id) VALUES ($1)", ids["org"])
    await db.execute(
        "INSERT INTO organization_names (id, organization_id, name, is_canonical)"
        " VALUES ($1,$2,$3,TRUE)",
        generate_id(),
        ids["org"],
        names["org"],
    )
    governing = await db.fetchval(
        "SELECT id FROM organization_jurisdiction_affiliation_types WHERE slug='governing'"
    )
    await db.execute(
        "INSERT INTO organization_jurisdiction_affiliations"
        " (id, organization_id, jurisdiction_id, affiliation_type_id) VALUES ($1,$2,$3,$4)",
        ids["affil"],
        ids["org"],
        ids["j"],
        governing,
    )
    member = await db.fetchval("SELECT id FROM role_types WHERE slug='committee_member'")
    await db.execute(
        "INSERT INTO roles (id, organization_id, title, role_type_id, jurisdiction_id)"
        " VALUES ($1,$2,$3,$4,$5)",
        ids["role"],
        ids["org"],
        names["role_title"],
        member,
        ids["j"],
    )
    return {"ids": ids, "names": names, "marker": marker}


async def test_detail_shows_relationships(client, jur_with_graph):
    r = await client.get(
        f"/admin/jurisdictions/{jur_with_graph['ids']['j']}/", headers=AUTH_HEADERS
    )
    assert jur_with_graph["names"]["r"] in r.text
    # asymmetric edge renders as a full phrase (rel type shown lowercased)
    assert "is fully contained by" in r.text.lower()
    # lineage-category edges belong to the Lineage panel only, not Relationships
    assert "supersedes" not in r.text.lower()


async def test_detail_shows_lineage(client, jur_with_graph):
    r = await client.get(
        f"/admin/jurisdictions/{jur_with_graph['ids']['j']}/", headers=AUTH_HEADERS
    )
    assert jur_with_graph["names"]["p"] in r.text


async def test_detail_shows_affiliated_org(client, jur_with_graph):
    r = await client.get(
        f"/admin/jurisdictions/{jur_with_graph['ids']['j']}/", headers=AUTH_HEADERS
    )
    assert jur_with_graph["names"]["org"] in r.text


async def test_detail_shows_referencing_role(client, jur_with_graph):
    r = await client.get(
        f"/admin/jurisdictions/{jur_with_graph['ids']['j']}/", headers=AUTH_HEADERS
    )
    assert jur_with_graph["names"]["role_title"] in r.text


# ---------------------------------------------------------------------------
# List — route-level status handling
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(loop_scope="session")
async def jur_status_variants(db):
    """Active, archived, and superseded jurisdictions under one search marker."""
    marker = generate_id()[-10:].lower()
    ids = {"active": generate_id(), "archived": generate_id(), "superseded": generate_id()}
    names = {
        "active": f"Statusville {marker} Active",
        "archived": f"Statusville {marker} Archived",
        "superseded": f"Statusville {marker} Superseded",
    }
    tid = await db.fetchval("SELECT id FROM jurisdiction_types WHERE slug='county'")
    await db.execute(
        "INSERT INTO jurisdictions (id, slug, name, type_id) VALUES ($1,$2,$3,$4)",
        ids["active"],
        f"stv-{marker}-a",
        names["active"],
        tid,
    )
    await db.execute(
        "INSERT INTO jurisdictions (id, slug, name, type_id, archived_at)"
        " VALUES ($1,$2,$3,$4,NOW())",
        ids["archived"],
        f"stv-{marker}-x",
        names["archived"],
        tid,
    )
    await db.execute(
        "INSERT INTO jurisdictions (id, slug, name, type_id, superseded_at)"
        " VALUES ($1,$2,$3,$4,NOW())",
        ids["superseded"],
        f"stv-{marker}-s",
        names["superseded"],
        tid,
    )
    return {"marker": marker, "names": names}


async def test_list_invalid_status_falls_back_to_active(client, jur_status_variants):
    """An unknown ?status= normalizes to 'active' (not a filter pass-through)."""
    v = jur_status_variants
    r = await client.get(
        "/admin/jurisdictions/",
        headers=AUTH_HEADERS,
        params={"q": v["marker"], "status": "bogus"},
    )
    assert r.status_code == 200
    assert v["names"]["active"] in r.text
    assert v["names"]["archived"] not in r.text
    assert v["names"]["superseded"] not in r.text


async def test_list_superseded_status_filter(client, jur_status_variants):
    """status=superseded surfaces superseded rows and excludes active/archived."""
    v = jur_status_variants
    r = await client.get(
        "/admin/jurisdictions/",
        headers=AUTH_HEADERS,
        params={"q": v["marker"], "status": "superseded"},
    )
    assert r.status_code == 200
    assert v["names"]["superseded"] in r.text
    assert v["names"]["active"] not in r.text
    assert v["names"]["archived"] not in r.text
