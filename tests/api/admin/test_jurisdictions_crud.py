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
