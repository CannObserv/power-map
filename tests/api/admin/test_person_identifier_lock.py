"""WA Legislature keys on an anchored person are the dataset's (#710): admin refuses
edits and deletes.

usa-wa's person crosswalk asserts `person_wa_legislature_roster` and
`person_wa_legislature_member_id` on every person it anchors, presence only. A
deleted or re-valued key would be re-inserted by the next execute, so edit and
delete refuse one — as it is and as the edit would leave it — and the row
renders read-only. Adding one stays open: nothing reverts an extra row.
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
ROSTER = "person_wa_legislature_roster"
MEMBER = "person_wa_legislature_member_id"
LOCKED = "person_crosswalk"  # every refusal names the dataset to fix upstream


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


async def _person(db, *, resolution: str | None) -> str:
    """A person; ``resolution`` set gives it a usa-wa anchor of that resolution."""
    pid = generate_id()
    await db.execute("INSERT INTO people (id) VALUES ($1)", pid)
    if resolution:
        await db.execute(
            "INSERT INTO producer_crosswalk"
            " (id, source, kind, producer_id, exported_pm_id, pm_id, resolution)"
            " VALUES ($1, $2, 'person', $3, $4, $4, $5)",
            generate_id(),
            PRODUCER_SOURCE,
            f"P-{pid}",
            pid,
            resolution,
        )
    return pid


@pytest_asyncio.fixture(loop_scope="session")
async def people(db):
    return {
        "anchored": await _person(db, resolution="live"),
        "archived_anchor": await _person(db, resolution="archived"),
        "pm": await _person(db, resolution=None),
    }


async def _type_id(db, slug):
    return await db.fetchval("SELECT id FROM entity_identifier_types WHERE slug = $1", slug)


async def _other_type_id(db):
    """An attachable person type the lock does not cover."""
    return await db.fetchval(
        "SELECT id FROM entity_identifier_types WHERE entity_type = 'person'"
        " AND NOT is_internal AND slug <> ALL($1::text[]) ORDER BY slug LIMIT 1",
        [ROSTER, MEMBER],
    )


async def _ident(db, person, type_id, value="smith:1933") -> str:
    row = generate_id()
    await db.execute(
        "INSERT INTO identifiers (id, entity_id, entity_identifier_type_id, value)"
        " VALUES ($1, $2, $3, $4)",
        row,
        person,
        type_id,
        value,
    )
    return row


async def _value(db, row):
    return await db.fetchval("SELECT value FROM identifiers WHERE id = $1", row)


def _url(person, row=None, tail=""):
    base = f"/admin/people/{person}/identifiers/"
    return f"{base}{row}/{tail}" if row else base


def _assert_refused(r):
    assert r.status_code == 200
    assert "warning" in r.headers["HX-Trigger"] and LOCKED in r.headers["HX-Trigger"]


def _assert_refused_fallback(r):
    assert r.status_code == 303
    assert r.headers["location"].endswith("flash=identifier_locked")


# --- edit ---------------------------------------------------------------------------


@pytest.mark.parametrize("slug", [ROSTER, MEMBER])
async def test_editing_a_locked_key_is_refused(client, db, people, slug):
    type_id = await _type_id(db, slug)
    row = await _ident(db, people["anchored"], type_id)

    r = await client.post(
        _url(people["anchored"], row, "edit-row/"),
        headers=HTMX_HEADERS,
        data={"entity_identifier_type_id": type_id, "value": "smith:1999"},
    )

    _assert_refused(r)
    assert f'id="identifier-row-{row}"' in r.text  # the rows stay as they were
    assert await _value(db, row) == "smith:1933"


async def test_editing_a_locked_key_fallback_names_the_lock(client, db, people):
    type_id = await _type_id(db, ROSTER)
    row = await _ident(db, people["anchored"], type_id)

    r = await client.post(
        _url(people["anchored"], row, "edit-row/"),
        headers=AUTH_HEADERS,
        data={"entity_identifier_type_id": type_id, "value": "smith:1999"},
    )

    _assert_refused_fallback(r)


async def test_editing_a_locked_key_out_of_the_lock_is_refused(client, db, people):
    """Re-typing it would leave the producer's key missing; the next run re-inserts it."""
    row = await _ident(db, people["anchored"], await _type_id(db, ROSTER))

    r = await client.post(
        _url(people["anchored"], row, "edit-row/"),
        headers=HTMX_HEADERS,
        data={"entity_identifier_type_id": await _other_type_id(db), "value": "smith:1933"},
    )

    _assert_refused(r)
    assert await db.fetchval(
        "SELECT entity_identifier_type_id FROM identifiers WHERE id = $1", row
    ) == await _type_id(db, ROSTER)


async def test_editing_a_pm_identifier_into_the_lock_is_refused(client, db, people):
    row = await _ident(db, people["anchored"], await _other_type_id(db), "Q42")

    r = await client.post(
        _url(people["anchored"], row, "edit-row/"),
        headers=HTMX_HEADERS,
        data={"entity_identifier_type_id": await _type_id(db, ROSTER), "value": "smith:1933"},
    )

    _assert_refused(r)
    assert await _value(db, row) == "Q42"


async def test_editing_a_pm_identifier_on_an_anchored_person_still_works(client, db, people):
    type_id = await _other_type_id(db)
    row = await _ident(db, people["anchored"], type_id, "Q42")

    r = await client.post(
        _url(people["anchored"], row, "edit-row/"),
        headers=HTMX_HEADERS,
        data={"entity_identifier_type_id": type_id, "value": "Q43"},
    )

    assert "success" in r.headers["HX-Trigger"]
    assert await _value(db, row) == "Q43"


@pytest.mark.parametrize("person", ["pm", "archived_anchor"])
async def test_a_wa_key_on_a_person_out_of_scope_is_ordinary_curation(client, db, people, person):
    type_id = await _type_id(db, ROSTER)
    row = await _ident(db, people[person], type_id)

    r = await client.post(
        _url(people[person], row, "edit-row/"),
        headers=HTMX_HEADERS,
        data={"entity_identifier_type_id": type_id, "value": "smith:1999"},
    )

    assert "success" in r.headers["HX-Trigger"]
    assert await _value(db, row) == "smith:1999"


async def test_the_edit_form_of_a_locked_key_is_not_served(client, db, people):
    """A form that can never save is a dead end; hand back the row and why."""
    row = await _ident(db, people["anchored"], await _type_id(db, ROSTER))
    await _ident(db, people["anchored"], await _other_type_id(db), "Q42")

    r = await client.get(_url(people["anchored"], row, "edit-row/"), headers=HTMX_HEADERS)

    _assert_refused(r)
    assert "<form" not in r.text and f'id="identifier-row-{row}"' in r.text
    # CR 6: the caller swaps this into the one row (outerHTML), never the tbody.
    assert r.text.count("<tr") == 1


# --- delete -------------------------------------------------------------------------


async def test_deleting_a_locked_key_is_refused(client, db, people):
    row = await _ident(db, people["anchored"], await _type_id(db, ROSTER))

    r = await client.delete(_url(people["anchored"], row), headers=HTMX_HEADERS)

    _assert_refused(r)
    assert f'id="identifier-row-{row}"' in r.text
    assert await _value(db, row) == "smith:1933"


async def test_deleting_a_locked_key_fallback_names_the_lock(client, db, people):
    row = await _ident(db, people["anchored"], await _type_id(db, MEMBER), "31526")

    r = await client.delete(_url(people["anchored"], row), headers=AUTH_HEADERS)

    _assert_refused_fallback(r)


async def test_deleting_a_wa_key_on_an_unanchored_person_still_works(client, db, people):
    row = await _ident(db, people["pm"], await _type_id(db, ROSTER))

    r = await client.delete(_url(people["pm"], row), headers=HTMX_HEADERS)

    assert "success" in r.headers["HX-Trigger"]
    assert await _value(db, row) is None


# --- add stays open -----------------------------------------------------------------


async def test_adding_a_wa_key_to_an_anchored_person_is_allowed(client, db, people):
    """#710 decision 1: presence only, so an extra row is never reverted."""
    r = await client.post(
        _url(people["anchored"]),
        headers=HTMX_HEADERS,
        data={"entity_identifier_type_id": await _type_id(db, ROSTER), "value": "smith:1941"},
    )

    assert "success" in r.headers["HX-Trigger"]


# --- the read-only row ----------------------------------------------------------------


async def test_a_locked_key_renders_read_only_with_its_owner(client, db, people):
    locked = await _ident(db, people["anchored"], await _type_id(db, ROSTER))
    own = await _ident(db, people["anchored"], await _other_type_id(db), "Q42")

    r = await client.get(f"/admin/people/{people['anchored']}/", headers=AUTH_HEADERS)

    row_of = {
        rid: r.text.split(f'id="identifier-row-{rid}"', 1)[1].split("</tr>", 1)[0]
        for rid in (locked, own)
    }
    assert "usa-wa" in row_of[locked]
    assert "/edit-row/" not in row_of[locked] and "hx-delete" not in row_of[locked]
    assert "/edit-row/" in row_of[own] and "hx-delete" in row_of[own]
