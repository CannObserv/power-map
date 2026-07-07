"""Integration tests for jurisdictions admin CRUD (#275 Phase 2)."""

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient

from src.api.main import app
from src.core.db import generate_id

pytestmark = [pytest.mark.integration]

AUTH_HEADERS = {"X-ExeDev-UserID": "usr_test", "X-ExeDev-Email": "admin@test.com"}


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


@pytest_asyncio.fixture(loop_scope="session")
async def county_type_id(db_pool):
    async with db_pool.acquire() as conn:
        return await conn.fetchval("SELECT id FROM jurisdiction_types WHERE slug='county'")


@pytest_asyncio.fixture(loop_scope="session")
async def cleanup_slugs(db_pool):
    """Collect slugs created by a test; delete them at teardown."""
    slugs: list[str] = []
    yield slugs
    if slugs:
        async with db_pool.acquire() as conn:
            await conn.execute("DELETE FROM jurisdictions WHERE slug = ANY($1::text[])", slugs)


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------


async def test_new_form_returns_200(client):
    r = client.get("/admin/jurisdictions/new/", headers=AUTH_HEADERS)
    assert r.status_code == 200
    assert 'name="slug"' in r.text
    assert 'name="name"' in r.text
    assert 'name="type_id"' in r.text
    assert "County" in r.text  # type option


async def test_new_form_redirects_unauthenticated(client):
    r = client.get("/admin/jurisdictions/new/", follow_redirects=False)
    assert r.status_code in (302, 307)
    assert "/__exe.dev/login" in r.headers["location"]


async def test_create_valid(client, county_type_id, cleanup_slugs, db_pool):
    marker = generate_id()[-10:].lower()
    slug = f"crt-{marker}"
    cleanup_slugs.append(slug)
    r = client.post(
        "/admin/jurisdictions/new/",
        headers=AUTH_HEADERS,
        data={
            "slug": slug,
            "name": f"Createburg {marker}",
            "type_id": county_type_id,
            "valid_from": "2001-01-01",
            "valid_until": "",
            "notes": "seeded by test",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"].startswith("/admin/jurisdictions/")
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT name, notes, valid_from FROM jurisdictions WHERE slug=$1", slug
        )
    assert row is not None
    assert row["name"] == f"Createburg {marker}"
    assert row["notes"] == "seeded by test"
    assert str(row["valid_from"]) == "2001-01-01"


async def test_create_missing_name(client, county_type_id):
    r = client.post(
        "/admin/jurisdictions/new/",
        headers=AUTH_HEADERS,
        data={"slug": "x-missing-name", "name": "", "type_id": county_type_id},
    )
    assert r.status_code == 422
    assert "required" in r.text.lower()


async def test_create_missing_slug(client, county_type_id):
    r = client.post(
        "/admin/jurisdictions/new/",
        headers=AUTH_HEADERS,
        data={"slug": "", "name": "No Slug", "type_id": county_type_id},
    )
    assert r.status_code == 422


async def test_create_missing_type(client):
    r = client.post(
        "/admin/jurisdictions/new/",
        headers=AUTH_HEADERS,
        data={"slug": "x-missing-type", "name": "No Type", "type_id": ""},
    )
    assert r.status_code == 422


async def test_create_duplicate_slug(client, county_type_id, cleanup_slugs, db_pool):
    marker = generate_id()[-10:].lower()
    slug = f"dup-{marker}"
    cleanup_slugs.append(slug)
    async with db_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO jurisdictions (id, slug, name, type_id) VALUES ($1,$2,$3,$4)",
            generate_id(),
            slug,
            "Existing",
            county_type_id,
        )
    r = client.post(
        "/admin/jurisdictions/new/",
        headers=AUTH_HEADERS,
        data={"slug": slug, "name": "Duplicate", "type_id": county_type_id},
    )
    assert r.status_code == 422
    assert "slug" in r.text.lower()


async def test_create_invalid_validity_range(client, county_type_id):
    r = client.post(
        "/admin/jurisdictions/new/",
        headers=AUTH_HEADERS,
        data={
            "slug": "x-bad-range",
            "name": "Bad Range",
            "type_id": county_type_id,
            "valid_from": "2020-01-01",
            "valid_until": "2010-01-01",
        },
    )
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# Inline details edit (name / slug / type / validity / notes)
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(loop_scope="session")
async def jur(db_pool, county_type_id):
    """A county jurisdiction to edit; deleted at teardown."""
    jid = generate_id()
    marker = jid[-10:].lower()
    async with db_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO jurisdictions (id, slug, name, type_id) VALUES ($1,$2,$3,$4)",
            jid,
            f"edit-{marker}",
            f"Editburg {marker}",
            county_type_id,
        )
    yield {"id": jid, "marker": marker, "slug": f"edit-{marker}"}
    async with db_pool.acquire() as conn:
        # polymorphic attachments have no FK to jurisdictions — clean by entity_id
        await conn.execute(
            "DELETE FROM contact_methods WHERE entity_type='jurisdiction' AND entity_id=$1", jid
        )
        await conn.execute(
            "DELETE FROM links WHERE entity_type='jurisdiction' AND entity_id=$1", jid
        )
        await conn.execute("DELETE FROM identifiers WHERE entity_id=$1", jid)
        addr_rows = await conn.fetch(
            "SELECT address_id FROM entity_addresses"
            " WHERE entity_type='jurisdiction' AND entity_id=$1",
            jid,
        )
        await conn.execute(
            "DELETE FROM entity_addresses WHERE entity_type='jurisdiction' AND entity_id=$1", jid
        )
        if addr_rows:
            await conn.execute(
                "DELETE FROM addresses WHERE id = ANY($1::text[])",
                [r["address_id"] for r in addr_rows],
            )
        await conn.execute("DELETE FROM jurisdictions WHERE id=$1", jid)


async def test_details_edit_form_prefilled(client, jur):
    r = client.get(f"/admin/jurisdictions/{jur['id']}/details/edit/", headers=AUTH_HEADERS)
    assert r.status_code == 200
    assert jur["slug"] in r.text
    assert 'name="name"' in r.text
    assert 'name="type_id"' in r.text
    # slug caveat about the public /resolve key
    assert "/resolve" in r.text


async def test_details_save_updates_db_and_header(client, jur, db_pool):
    new_name = f"Renamedville {jur['marker']}"
    r = client.post(
        f"/admin/jurisdictions/{jur['id']}/details/",
        headers={**AUTH_HEADERS, "HX-Request": "true"},
        data={
            "name": new_name,
            "slug": jur["slug"],
            "type_id": "",  # keep current type (unchanged) — resolved server-side
            "valid_from": "1990-05-01",
            "valid_until": "",
            "notes": "edited",
        },
    )
    assert r.status_code == 200
    assert "edited" in r.text  # updated notes render in the details card
    # the name lives in the page heading (not the card); it rides the header-sync
    # trigger, which the detail JS applies to #page-heading in place
    trigger = r.headers.get("HX-Trigger", "")
    assert "updateJurisdictionHeader" in trigger
    assert new_name in trigger
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT name, notes, valid_from FROM jurisdictions WHERE id=$1", jur["id"]
        )
    assert row["name"] == new_name
    assert row["notes"] == "edited"
    assert str(row["valid_from"]) == "1990-05-01"


async def test_details_save_duplicate_slug_rejected(
    client, jur, county_type_id, cleanup_slugs, db_pool
):
    other = f"other-{generate_id()[-8:].lower()}"
    cleanup_slugs.append(other)
    async with db_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO jurisdictions (id, slug, name, type_id) VALUES ($1,$2,$3,$4)",
            generate_id(),
            other,
            "Other",
            county_type_id,
        )
    r = client.post(
        f"/admin/jurisdictions/{jur['id']}/details/",
        headers={**AUTH_HEADERS, "HX-Request": "true"},
        data={
            "name": "X",
            "slug": other,
            "type_id": "",
            "valid_from": "",
            "valid_until": "",
            "notes": "",
        },
    )
    assert r.status_code == 422
    assert "slug" in r.text.lower()


async def test_details_save_invalid_range_rejected(client, jur):
    r = client.post(
        f"/admin/jurisdictions/{jur['id']}/details/",
        headers={**AUTH_HEADERS, "HX-Request": "true"},
        data={
            "name": "X",
            "slug": jur["slug"],
            "type_id": "",
            "valid_from": "2020-01-01",
            "valid_until": "2000-01-01",
            "notes": "",
        },
    )
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# Archive / unarchive / delete
# ---------------------------------------------------------------------------


async def test_archive_and_unarchive(client, jur, db_pool):
    r = client.post(
        f"/admin/jurisdictions/{jur['id']}/archive/", headers=AUTH_HEADERS, follow_redirects=False
    )
    assert r.status_code == 303
    assert "flash=archived" in r.headers["location"]
    async with db_pool.acquire() as conn:
        assert (
            await conn.fetchval("SELECT archived_at FROM jurisdictions WHERE id=$1", jur["id"])
            is not None
        )
    # archiving an archived jurisdiction → 409
    assert (
        client.post(f"/admin/jurisdictions/{jur['id']}/archive/", headers=AUTH_HEADERS).status_code
        == 409
    )
    # unarchive
    r3 = client.post(
        f"/admin/jurisdictions/{jur['id']}/unarchive/", headers=AUTH_HEADERS, follow_redirects=False
    )
    assert r3.status_code == 303
    assert "flash=unarchived" in r3.headers["location"]
    async with db_pool.acquire() as conn:
        assert (
            await conn.fetchval("SELECT archived_at FROM jurisdictions WHERE id=$1", jur["id"])
            is None
        )
    # unarchiving an active jurisdiction → 409
    assert (
        client.post(
            f"/admin/jurisdictions/{jur['id']}/unarchive/", headers=AUTH_HEADERS
        ).status_code
        == 409
    )


async def test_delete_requires_archived(client, jur):
    r = client.request("DELETE", f"/admin/jurisdictions/{jur['id']}/", headers=AUTH_HEADERS)
    assert r.status_code == 409


async def test_delete_archived_ok(client, county_type_id, db_pool):
    jid = generate_id()
    async with db_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO jurisdictions (id, slug, name, type_id, archived_at)"
            " VALUES ($1,$2,$3,$4,NOW())",
            jid,
            f"del-{jid[-8:].lower()}",
            "Deletable",
            county_type_id,
        )
    r = client.request(
        "DELETE",
        f"/admin/jurisdictions/{jid}/",
        headers=AUTH_HEADERS,
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert "flash=deleted" in r.headers["location"]
    async with db_pool.acquire() as conn:
        assert await conn.fetchval("SELECT id FROM jurisdictions WHERE id=$1", jid) is None
        assert (
            await conn.fetchval(
                "SELECT 1 FROM deleted_entities WHERE entity_type='jurisdiction' AND entity_id=$1",
                jid,
            )
            == 1
        )


async def test_delete_referenced_returns_409(client, county_type_id, db_pool):
    jid, oid, rid = generate_id(), generate_id(), generate_id()
    async with db_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO jurisdictions (id, slug, name, type_id, archived_at)"
            " VALUES ($1,$2,$3,$4,NOW())",
            jid,
            f"ref-{jid[-8:].lower()}",
            "Referenced",
            county_type_id,
        )
        await conn.execute("INSERT INTO organizations (id) VALUES ($1)", oid)
        member = await conn.fetchval("SELECT id FROM role_types WHERE slug='member'")
        await conn.execute(
            "INSERT INTO roles (id, organization_id, title, role_type_id, jurisdiction_id)"
            " VALUES ($1,$2,$3,$4,$5)",
            rid,
            oid,
            "Ref Role",
            member,
            jid,
        )
    try:
        r = client.request("DELETE", f"/admin/jurisdictions/{jid}/", headers=AUTH_HEADERS)
        assert r.status_code == 409
    finally:
        async with db_pool.acquire() as conn:
            await conn.execute("DELETE FROM roles WHERE id=$1", rid)
            await conn.execute("DELETE FROM organizations WHERE id=$1", oid)
            await conn.execute("DELETE FROM jurisdictions WHERE id=$1", jid)


# ---------------------------------------------------------------------------
# Attachment CRUD (factory-wired: contacts / links / identifiers)
# ---------------------------------------------------------------------------

HX = {**AUTH_HEADERS, "HX-Request": "true"}


async def test_contact_crud(client, jur, db_pool):
    # new-row form partial
    r0 = client.get(
        f"/admin/jurisdictions/{jur['id']}/contacts/new-row/?contact_type=email", headers=HX
    )
    assert r0.status_code == 200
    assert 'name="value"' in r0.text
    # create
    r = client.post(
        f"/admin/jurisdictions/{jur['id']}/contacts/",
        headers=HX,
        data={"contact_type": "email", "value": "hall@example.gov", "display_label": "Clerk"},
    )
    assert r.status_code == 200
    assert "hall@example.gov" in r.text
    async with db_pool.acquire() as conn:
        cid = await conn.fetchval(
            "SELECT id FROM contact_methods"
            " WHERE entity_type='jurisdiction' AND entity_id=$1 AND value=$2",
            jur["id"],
            "hall@example.gov",
        )
    assert cid is not None
    # delete
    rd = client.request("DELETE", f"/admin/jurisdictions/{jur['id']}/contacts/{cid}/", headers=HX)
    assert rd.status_code == 200
    async with db_pool.acquire() as conn:
        assert await conn.fetchval("SELECT id FROM contact_methods WHERE id=$1", cid) is None


async def test_link_create(client, jur, db_pool):
    async with db_pool.acquire() as conn:
        lt = await conn.fetchval("SELECT id FROM link_types ORDER BY id LIMIT 1")
    r = client.post(
        f"/admin/jurisdictions/{jur['id']}/links/",
        headers=HX,
        data={"url": "https://jur.example.gov", "link_type_id": lt, "is_active": "true"},
    )
    assert r.status_code == 200
    assert "jur.example.gov" in r.text
    async with db_pool.acquire() as conn:
        assert (
            await conn.fetchval(
                "SELECT id FROM links WHERE entity_type='jurisdiction' AND entity_id=$1 AND url=$2",
                jur["id"],
                "https://jur.example.gov",
            )
            is not None
        )


async def test_identifier_create(client, jur, db_pool):
    async with db_pool.acquire() as conn:
        it = await conn.fetchval("SELECT id FROM entity_identifier_types WHERE slug='jur_ocd'")
    r = client.post(
        f"/admin/jurisdictions/{jur['id']}/identifiers/",
        headers=HX,
        data={"entity_identifier_type_id": it, "value": "ocd-division/country:us/x"},
    )
    assert r.status_code == 200
    async with db_pool.acquire() as conn:
        assert (
            await conn.fetchval(
                "SELECT id FROM identifiers WHERE entity_id=$1 AND value=$2",
                jur["id"],
                "ocd-division/country:us/x",
            )
            is not None
        )


# ---------------------------------------------------------------------------
# Addresses CRUD (hand-built, normalizer-pinned)
# ---------------------------------------------------------------------------


async def test_address_new_row_form(client, jur, local_address_normalizer):
    r = client.get(f"/admin/jurisdictions/{jur['id']}/addresses/new-row/", headers=HX)
    assert r.status_code == 200
    assert 'name="address_line_1"' in r.text


async def test_address_create_and_delete(client, jur, db_pool, local_address_normalizer):
    # mode=save skips the confirm modal and inserts directly
    r = client.post(
        f"/admin/jurisdictions/{jur['id']}/addresses/",
        headers=HX,
        data={
            "address_line_1": "600 Fourth Ave",
            "city": "Seattle",
            "region": "WA",
            "postal_code": "98104",
            "address_type": "mailing",
            "country": "US",
            "mode": "save",
            "standardized": "600 Fourth Ave, Seattle, WA 98104",
        },
    )
    assert r.status_code == 200
    assert "Seattle" in r.text
    async with db_pool.acquire() as conn:
        eaid = await conn.fetchval(
            "SELECT id FROM entity_addresses WHERE entity_type='jurisdiction' AND entity_id=$1",
            jur["id"],
        )
    assert eaid is not None
    rd = client.request("DELETE", f"/admin/jurisdictions/{jur['id']}/addresses/{eaid}/", headers=HX)
    assert rd.status_code == 200
    async with db_pool.acquire() as conn:
        assert await conn.fetchval("SELECT id FROM entity_addresses WHERE id=$1", eaid) is None


async def test_address_edit(client, jur, db_pool, local_address_normalizer):
    # create first (mode=save skips the confirm modal and inserts directly)
    client.post(
        f"/admin/jurisdictions/{jur['id']}/addresses/",
        headers=HX,
        data={
            "address_line_1": "600 Fourth Ave",
            "city": "Seattle",
            "region": "WA",
            "postal_code": "98104",
            "address_type": "mailing",
            "country": "US",
            "mode": "save",
            "standardized": "600 Fourth Ave, Seattle, WA 98104",
        },
    )
    async with db_pool.acquire() as conn:
        eaid = await conn.fetchval(
            "SELECT id FROM entity_addresses WHERE entity_type='jurisdiction' AND entity_id=$1",
            jur["id"],
        )
    assert eaid is not None
    # edit: change line/city/type/label — both addresses + entity_addresses update
    r = client.post(
        f"/admin/jurisdictions/{jur['id']}/addresses/{eaid}/edit-row/",
        headers=HX,
        data={
            "address_line_1": "1200 Fifth Ave",
            "city": "Tacoma",
            "region": "WA",
            "postal_code": "98402",
            "address_type": "physical",
            "display_name": "Annex",
            "country": "US",
            "mode": "save",
            "standardized": "1200 Fifth Ave, Tacoma, WA 98402",
        },
    )
    assert r.status_code == 200
    assert "Tacoma" in r.text
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT a.address_line_1, a.city, ea.address_type, ea.display_name"
            " FROM entity_addresses ea JOIN addresses a ON a.id = ea.address_id"
            " WHERE ea.id=$1",
            eaid,
        )
    assert row["address_line_1"] == "1200 Fifth Ave"
    assert row["city"] == "Tacoma"
    assert row["address_type"] == "physical"
    assert row["display_name"] == "Annex"
