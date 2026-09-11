"""The pins page (#498): every override in one place, so none is a future mystery.

`/admin/pins/` lists the curation overlay's pins — the entity (by display name,
linked), the slot, the value as a curator reads it, the note, who pinned it and
when — filtered by the #306 status axis (`active` by default, `archived`, `all`;
an unknown status falls back to `active`, never to no filter) and by entity
type, with an Unpin on each live row that archives exactly that row.
"""

import json

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from src.api.admin.deps import get_db
from src.api.admin.pins_queries import STATUS_PREDICATES, VALID_STATUSES
from src.api.main import app
from src.core.curation_overlay import active_pin, pin, unpin
from src.core.db import generate_id
from src.core.ingestion.crosswalk import PRODUCER_SOURCE

pytestmark = pytest.mark.integration

AUTH = {"X-ExeDev-UserID": "pins-page-curator", "X-ExeDev-Email": "pinner@example.org"}
HX = {**AUTH, "HX-Request": "true"}


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
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    app.dependency_overrides.pop(get_db, None)


@pytest_asyncio.fixture(loop_scope="session")
async def curator(db) -> str:
    await db.execute(
        "INSERT INTO app_users (id, email) VALUES ($1, $2) ON CONFLICT (id) DO NOTHING",
        AUTH["X-ExeDev-UserID"],
        AUTH["X-ExeDev-Email"],
    )
    return AUTH["X-ExeDev-UserID"]


async def _anchor(db, kind, pm_id):
    await db.execute(
        "INSERT INTO producer_crosswalk"
        " (id, source, kind, producer_id, exported_pm_id, pm_id, resolution)"
        " VALUES ($1, $2, $3, $4, $5, $5, 'live')",
        generate_id(),
        PRODUCER_SOURCE,
        kind,
        f"p-{generate_id()}",
        pm_id,
    )


async def _org(db, name: str) -> str:
    oid = generate_id()
    await db.execute("INSERT INTO organizations (id) VALUES ($1)", oid)
    await db.execute(
        "INSERT INTO organization_names (id, organization_id, name, name_type, is_canonical)"
        " VALUES ($1, $2, $3, 'legal', TRUE)",
        generate_id(),
        oid,
        name,
    )
    await _anchor(db, "organization", oid)
    return oid


async def _person(db, name: str) -> str:
    pid = generate_id()
    await db.execute("INSERT INTO people (id) VALUES ($1)", pid)
    await db.execute(
        "INSERT INTO person_names (id, person_id, name, name_type, is_canonical)"
        " VALUES ($1, $2, $3, 'legal', TRUE)",
        generate_id(),
        pid,
        name,
    )
    await _anchor(db, "person", pid)
    return pid


def test_the_status_axis_follows_the_list_convention():
    """#306: `active` first and the default, `all` a first-class status."""
    assert list(STATUS_PREDICATES) == ["active", "archived"]
    assert VALID_STATUSES == {"active", "archived", "all"}


async def test_the_page_lists_live_pins_as_a_curator_reads_them(client, db, curator):
    parent, org = await _org(db, "House Appropriations"), await _org(db, "Capital Subcommittee")
    person = await _person(db, "Denny Heck")
    await pin(db, "organization", org, "parent_id", parent, user_id=curator, note="PM's call")
    await pin(db, "person", person, "name", "Dennis Heck", user_id=curator)

    r = await client.get("/admin/pins/", headers=AUTH)

    assert r.status_code == 200
    page = r.text
    assert f'href="/admin/orgs/{org}/"' in page and "Capital Subcommittee" in page
    assert f'href="/admin/people/{person}/"' in page and "Dennis Heck" in page
    assert "Parent organization" in page and "House Appropriations" in page
    assert "PM&#39;s call" in page and "pinner@example.org" in page
    assert 'aria-current="page"' in page and 'href="/admin/pins/"' in page


async def test_the_default_status_hides_unpinned_rows_and_all_shows_them(client, db, curator):
    org = await _org(db, "Energy Committee")
    await pin(db, "organization", org, "acronym", "OLD", user_id=curator)
    await unpin(db, "organization", org, "acronym", user_id=curator)

    active = (await client.get("/admin/pins/", headers=AUTH)).text
    archived = (await client.get("/admin/pins/?status=archived", headers=AUTH)).text
    everything = (await client.get("/admin/pins/?status=all", headers=AUTH)).text
    fallback = (await client.get("/admin/pins/?status=banana", headers=AUTH)).text

    assert f"/admin/orgs/{org}/" not in active
    assert f"/admin/orgs/{org}/" in archived and "Unpinned" in archived
    assert f"/admin/orgs/{org}/" in everything
    assert f"/admin/orgs/{org}/" not in fallback  # unknown → active, never no filter


async def test_the_type_filter(client, db, curator):
    org, person = await _org(db, "Rules Committee"), await _person(db, "Pat Doe")
    await pin(db, "organization", org, "acronym", "RULES", user_id=curator)
    await pin(db, "person", person, "name", "Patricia Doe", user_id=curator)

    page = (await client.get("/admin/pins/?type=person", headers=AUTH)).text

    assert f"/admin/people/{person}/" in page and f"/admin/orgs/{org}/" not in page


async def test_an_htmx_filter_returns_the_region_only(client, db):
    r = await client.get("/admin/pins/?status=all", headers=HX)

    assert r.status_code == 200 and "<html" not in r.text and 'id="pins-table"' in r.text


async def test_unpin_from_the_list_archives_that_row(client, db, curator):
    org = await _org(db, "Transportation")
    held = await pin(db, "organization", org, "acronym", "TRAN", user_id=curator)

    r = await client.post(f"/admin/pins/{held.id}/unpin/", headers=HX)

    assert r.status_code == 200
    assert await active_pin(db, "organization", org, "acronym") is None
    assert "Unpinned" in r.text
    assert json.loads(r.headers["HX-Trigger"])["showFlash"]["level"] == "success"


async def test_unpin_from_a_stale_row_is_a_warning_and_leaves_the_live_pin(client, db, curator):
    org = await _org(db, "Ways and Means")
    old = await pin(db, "organization", org, "acronym", "WM", user_id=curator)
    await pin(db, "organization", org, "acronym", "W&M", user_id=curator)

    r = await client.post(f"/admin/pins/{old.id}/unpin/", headers=HX)

    assert json.loads(r.headers["HX-Trigger"])["showFlash"]["level"] == "warning"
    assert (await active_pin(db, "organization", org, "acronym")).value == "W&M"


async def test_unpin_from_the_list_falls_back_to_the_page(client, db, curator):
    org = await _org(db, "Finance")
    held = await pin(db, "organization", org, "acronym", "FIN", user_id=curator)

    r = await client.post(f"/admin/pins/{held.id}/unpin/", headers=AUTH)

    assert r.status_code == 303 and r.headers["location"] == "/admin/pins/?flash=unpinned"


async def test_an_empty_list_says_so(client, db):
    page = (await client.get("/admin/pins/?type=organization&status=archived", headers=AUTH)).text

    assert "No pins" in page
