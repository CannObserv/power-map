"""Links between two anchored orgs are the dataset's (#592): admin refuses them.

usa-wa's `org_lineage` owns `succeeded_by` / `merged_with` / `split_from` wherever
both orgs carry a producer anchor; the applier archives what it drops and dates
what it publishes. A curator who disagrees fixes it upstream — so every admin
door that writes an event refuses such a link, as it is and as the write would
leave it, and the row renders read-only.
"""

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from src.api.admin.deps import get_db
from src.api.main import app
from src.core.db import generate_id
from src.core.ingestion.crosswalk import PRODUCER_SOURCE

pytestmark = [pytest.mark.integration]

AUTH_HEADERS = {"X-ExeDev-UserID": "usr_test", "X-ExeDev-Email": "admin@test.com"}
HTMX_HEADERS = {**AUTH_HEADERS, "HX-Request": "true"}
SOURCE = PRODUCER_SOURCE  # the lock reads the applier's own source (CR 2)
LOCKED = "org_lineage"  # every refusal names the dataset to fix upstream


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


async def _org(db, *, anchored: bool) -> str:
    org = generate_id()
    await db.execute("INSERT INTO organizations (id) VALUES ($1)", org)
    await db.execute(
        "INSERT INTO organization_names (id, organization_id, name, is_canonical)"
        " VALUES ($1, $2, $3, TRUE)",
        generate_id(),
        org,
        f"Lock Test {org[-6:]}",
    )
    if anchored:
        await db.execute(
            "INSERT INTO producer_crosswalk"
            " (id, source, kind, producer_id, exported_pm_id, pm_id, resolution)"
            " VALUES ($1, $2, 'organization', $3, $4, $4, 'live')",
            generate_id(),
            SOURCE,
            f"P-{org}",
            org,
        )
    return org


@pytest_asyncio.fixture(loop_scope="session")
async def orgs(db):
    return {
        "a": await _org(db, anchored=True),
        "b": await _org(db, anchored=True),
        "pm": await _org(db, anchored=False),
    }


async def _type_id(db, slug):
    return await db.fetchval("SELECT id FROM entity_event_types WHERE slug = $1", slug)


async def _link(db, subject, slug, linked, *, archived=False) -> str:
    row = generate_id()
    await db.execute(
        "INSERT INTO entity_events (id, entity_type, entity_id, event_type_id, event_year,"
        " linked_entity_type, linked_entity_id, archived_at)"
        f" VALUES ($1, 'organization', $2, $3, 2021, 'organization', $4,"
        f" {'NOW()' if archived else 'NULL'})",
        row,
        subject,
        await _type_id(db, slug),
        linked,
    )
    return row


async def _event(db, row):
    return await db.fetchrow("SELECT * FROM entity_events WHERE id = $1", row)


def _form(type_id, linked, year="2021"):
    return {
        "event_type_id": type_id,
        "event_year": year,
        "linked_entity_type": "organization",
        "linked_entity_id": linked,
        "visibility": "public",
    }


def _assert_refused_fallback(r):
    assert r.status_code == 303
    assert r.headers["location"].endswith("flash=locked")


# --- the succession route ---------------------------------------------------------


async def test_link_successor_between_anchored_orgs_is_refused(client, db, orgs):
    r = await client.post(
        f"/admin/orgs/{orgs['a']}/link-successor/{orgs['b']}/", headers=HTMX_HEADERS, data={}
    )

    assert r.status_code == 200
    assert "warning" in r.headers["HX-Trigger"] and LOCKED in r.headers["HX-Trigger"]
    assert not await db.fetchval(
        "SELECT count(*) FROM entity_events WHERE entity_id = $1 AND linked_entity_id = $2",
        orgs["a"],
        orgs["b"],
    )


async def test_link_successor_fallback_names_the_lock(client, orgs):
    r = await client.post(
        f"/admin/orgs/{orgs['a']}/link-successor/{orgs['b']}/", headers=AUTH_HEADERS, data={}
    )

    _assert_refused_fallback(r)


async def test_link_successor_to_an_unanchored_org_still_works(client, db, orgs):
    r = await client.post(
        f"/admin/orgs/{orgs['a']}/link-successor/{orgs['pm']}/", headers=HTMX_HEADERS, data={}
    )

    assert "success" in r.headers["HX-Trigger"]


# --- the events panel ---------------------------------------------------------------


async def test_adding_a_locked_link_is_refused(client, db, orgs):
    form = _form(await _type_id(db, "merged_with"), orgs["b"])

    r = await client.post(f"/admin/orgs/{orgs['a']}/events/", headers=HTMX_HEADERS, data=form)

    assert r.status_code == 200 and LOCKED in r.text
    assert not await db.fetchval(
        "SELECT count(*) FROM entity_events WHERE entity_id = $1", orgs["a"]
    )


async def test_adding_a_locked_link_fallback_names_the_lock(client, db, orgs):
    form = _form(await _type_id(db, "merged_with"), orgs["b"])

    r = await client.post(f"/admin/orgs/{orgs['a']}/events/", headers=AUTH_HEADERS, data=form)

    _assert_refused_fallback(r)


async def test_adding_a_link_to_an_unanchored_org_is_allowed(client, db, orgs):
    form = _form(await _type_id(db, "merged_with"), orgs["pm"])

    r = await client.post(f"/admin/orgs/{orgs['a']}/events/", headers=HTMX_HEADERS, data=form)

    assert "success" in r.headers["HX-Trigger"]


async def test_editing_a_locked_link_is_refused(client, db, orgs):
    row = await _link(db, orgs["a"], "succeeded_by", orgs["b"])
    form = _form(await _type_id(db, "succeeded_by"), orgs["b"], year="1999")

    r = await client.post(
        f"/admin/orgs/{orgs['a']}/events/{row}/edit-row/", headers=HTMX_HEADERS, data=form
    )

    assert LOCKED in r.text
    assert (await _event(db, row))["event_year"] == 2021


async def test_editing_a_locked_link_out_of_the_lock_is_refused(client, db, orgs):
    """Re-pointing it at an unanchored org would free a link the dataset owns."""
    row = await _link(db, orgs["a"], "succeeded_by", orgs["b"])
    form = _form(await _type_id(db, "succeeded_by"), orgs["pm"])

    r = await client.post(
        f"/admin/orgs/{orgs['a']}/events/{row}/edit-row/", headers=HTMX_HEADERS, data=form
    )

    assert LOCKED in r.text
    assert (await _event(db, row))["linked_entity_id"] == orgs["b"]


async def test_editing_a_pm_link_into_the_lock_is_refused(client, db, orgs):
    """The curator started from an unlocked row; the refusal still says why."""
    row = await _link(db, orgs["a"], "succeeded_by", orgs["pm"])
    form = _form(await _type_id(db, "succeeded_by"), orgs["b"])

    r = await client.post(
        f"/admin/orgs/{orgs['a']}/events/{row}/edit-row/", headers=HTMX_HEADERS, data=form
    )

    assert LOCKED in r.text
    assert (await _event(db, row))["linked_entity_id"] == orgs["pm"]


async def test_editing_a_locked_link_fallback_names_the_lock(client, db, orgs):
    row = await _link(db, orgs["a"], "succeeded_by", orgs["b"])
    form = _form(await _type_id(db, "succeeded_by"), orgs["b"], year="1999")

    r = await client.post(
        f"/admin/orgs/{orgs['a']}/events/{row}/edit-row/", headers=AUTH_HEADERS, data=form
    )

    _assert_refused_fallback(r)


@pytest.mark.parametrize(
    ("action", "archived"), [("archive", False), ("unarchive", True)], ids=["archive", "unarchive"]
)
async def test_archive_and_unarchive_of_a_locked_link_are_refused(
    client, db, orgs, action, archived
):
    row = await _link(db, orgs["a"], "split_from", orgs["b"], archived=archived)

    r = await client.post(f"/admin/orgs/{orgs['a']}/events/{row}/{action}/", headers=HTMX_HEADERS)

    assert r.status_code == 200
    assert "warning" in r.headers["HX-Trigger"] and LOCKED in r.headers["HX-Trigger"]
    assert ((await _event(db, row))["archived_at"] is not None) is archived


@pytest.mark.parametrize(
    ("action", "archived"), [("archive", False), ("unarchive", True)], ids=["archive", "unarchive"]
)
async def test_archive_and_unarchive_fallbacks_name_the_lock(client, db, orgs, action, archived):
    row = await _link(db, orgs["a"], "split_from", orgs["b"], archived=archived)

    r = await client.post(f"/admin/orgs/{orgs['a']}/events/{row}/{action}/", headers=AUTH_HEADERS)

    _assert_refused_fallback(r)


async def test_deleting_a_locked_link_is_refused(client, db, orgs):
    row = await _link(db, orgs["a"], "split_from", orgs["b"], archived=True)

    r = await client.delete(f"/admin/orgs/{orgs['a']}/events/{row}/", headers=HTMX_HEADERS)

    assert r.status_code == 200
    assert "warning" in r.headers["HX-Trigger"] and LOCKED in r.headers["HX-Trigger"]
    assert f'id="org-event-row-{row}"' in r.text  # the row stays in place
    assert await _event(db, row) is not None


async def test_deleting_a_locked_link_fallback_names_the_lock(client, db, orgs):
    row = await _link(db, orgs["a"], "split_from", orgs["b"], archived=True)

    r = await client.delete(f"/admin/orgs/{orgs['a']}/events/{row}/", headers=AUTH_HEADERS)

    _assert_refused_fallback(r)


async def test_archiving_a_pm_link_still_works(client, db, orgs):
    row = await _link(db, orgs["a"], "split_from", orgs["pm"])

    r = await client.post(f"/admin/orgs/{orgs['a']}/events/{row}/archive/", headers=HTMX_HEADERS)

    assert "success" in r.headers["HX-Trigger"]
    assert (await _event(db, row))["archived_at"] is not None


# --- the read-only row ----------------------------------------------------------------


async def test_a_locked_link_renders_read_only_with_its_owner(client, db, orgs):
    locked = await _link(db, orgs["a"], "succeeded_by", orgs["b"])
    own = await _link(db, orgs["a"], "split_from", orgs["pm"])

    r = await client.get(f"/admin/orgs/{orgs['a']}/", headers=AUTH_HEADERS)

    row_of = {
        rid: r.text.split(f'id="org-event-row-{rid}"', 1)[1].split("</tr>", 1)[0]
        for rid in (locked, own)
    }
    assert "usa-wa" in row_of[locked]
    assert "/edit-row/" not in row_of[locked] and "/archive/" not in row_of[locked]
    assert "/edit-row/" in row_of[own] and "/archive/" in row_of[own]


async def test_the_edit_form_of_a_locked_link_is_not_served(client, db, orgs):
    """CR 5: a form that can never save is a dead end; hand back the row and why."""
    row = await _link(db, orgs["a"], "succeeded_by", orgs["b"])

    r = await client.get(f"/admin/orgs/{orgs['a']}/events/{row}/edit-row/", headers=HTMX_HEADERS)

    assert r.status_code == 200
    assert "warning" in r.headers["HX-Trigger"] and LOCKED in r.headers["HX-Trigger"]
    assert "<form" not in r.text and f'id="org-event-row-{row}"' in r.text
