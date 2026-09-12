"""Every admin write that can move a producer-owned slot pins it (#498).

The acceptance the design names: a curator's correction to a producer-owned
field survives every later snapshot. That holds only if *each* edit site pins —
names (people and orgs), acronyms, the three parent writes, and the dissolved
event — so each one is exercised here through its real route and form. A write
that leaves the slot's value unchanged pins nothing; an entity outside the
producer's row scope is direct curation and pins nothing.
"""

import json

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from src.api.admin.deps import get_db
from src.api.main import app
from src.core.curation_overlay import active_pin, active_pins
from src.core.db import generate_id
from src.core.ingestion.crosswalk import PRODUCER_SOURCE

pytestmark = pytest.mark.integration

AUTH = {"X-ExeDev-UserID": "overlay-curator", "X-ExeDev-Email": "curator@example.org"}
HX = {**AUTH, "HX-Request": "true"}
DISSOLVED = "01KV0000000000000000000007"  # seeded entity_event_types.dissolved


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


async def _anchor(db, kind: str, pm_id: str) -> None:
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


async def _person(db, *names: tuple[str, str, bool], anchored: bool = True) -> tuple[str, list]:
    pid = generate_id()
    await db.execute("INSERT INTO people (id) VALUES ($1)", pid)
    ids = []
    for text, name_type, canonical in names:
        nid = generate_id()
        await db.execute(
            "INSERT INTO person_names (id, person_id, name, name_type, is_canonical)"
            " VALUES ($1, $2, $3, $4, $5)",
            nid,
            pid,
            text,
            name_type,
            canonical,
        )
        ids.append(nid)
    if anchored:
        await _anchor(db, "person", pid)
    return pid, ids


async def _org(db, name: str = "Capital Budget", *, anchored: bool = True, parent=None) -> str:
    oid = generate_id()
    await db.execute("INSERT INTO organizations (id, parent_id) VALUES ($1, $2)", oid, parent)
    await db.execute(
        "INSERT INTO organization_names (id, organization_id, name, name_type, is_canonical)"
        " VALUES ($1, $2, $3, 'legal', TRUE)",
        generate_id(),
        oid,
        name,
    )
    if anchored:
        await _anchor(db, "organization", oid)
    return oid


def _flash(r) -> str:
    return json.loads(r.headers["HX-Trigger"])["showFlash"]["body"]


async def _pin(db, entity_type, entity_id, field):
    held = await active_pin(db, entity_type, entity_id, field)
    return None if held is None else ("pinned", held.value)


# --- names: people ------------------------------------------------------------------


async def test_editing_a_persons_legal_name_pins_it(client, db):
    pid, (nid,) = await _person(db, ("Denny Heck", "legal", True))

    r = await client.post(
        f"/admin/people/{pid}/names/{nid}/edit-row/",
        data={"name": "Dennis Heck", "name_type": "legal", "is_canonical": "true"},
        headers=HX,
    )

    assert r.status_code == 200
    assert await _pin(db, "person", pid, "name") == ("pinned", "Dennis Heck")
    assert "pinned" in _flash(r).lower()


async def test_a_name_edit_that_keeps_the_value_pins_nothing(client, db):
    pid, (nid,) = await _person(db, ("Denny Heck", "legal", True))

    r = await client.post(
        f"/admin/people/{pid}/names/{nid}/edit-row/",
        data={"name": "Denny Heck", "name_type": "legal", "is_canonical": "true", "locale": "en"},
        headers=HX,
    )

    assert r.status_code == 200
    assert await active_pins(db, "person", pid) == []
    assert "pinned" not in _flash(r).lower()


async def test_a_name_edit_outside_the_crosswalk_pins_nothing(client, db):
    pid, (nid,) = await _person(db, ("Denny Heck", "legal", True), anchored=False)

    await client.post(
        f"/admin/people/{pid}/names/{nid}/edit-row/",
        data={"name": "Dennis Heck", "name_type": "legal", "is_canonical": "true"},
        headers=HX,
    )

    assert await active_pins(db, "person", pid) == []


async def test_deleting_the_legal_name_pins_the_slot_empty(client, db):
    pid, (legal, _) = await _person(
        db, ("Denny Heck", "legal", True), ("Denny", "preferred", False)
    )

    r = await client.delete(f"/admin/people/{pid}/names/{legal}/", headers=HX)

    assert r.status_code == 200
    assert await _pin(db, "person", pid, "name") == ("pinned", None)


async def test_the_non_htmx_fallback_says_it_pinned(client, db):
    pid, (nid,) = await _person(db, ("Denny Heck", "legal", True))

    r = await client.post(
        f"/admin/people/{pid}/names/{nid}/edit-row/",
        data={"name": "Dennis Heck", "name_type": "legal", "is_canonical": "true"},
        headers=AUTH,
    )

    assert r.status_code == 303
    assert "flash=saved_pinned" in r.headers["location"]


# --- names: organizations --------------------------------------------------------


async def test_adding_a_canonical_legal_name_to_an_org_pins_it(client, db):
    oid = await _org(db, "Capital Budget")

    r = await client.post(
        f"/admin/orgs/{oid}/names/",
        data={
            "name": "House Capital Budget Committee",
            "name_type": "legal",
            "is_canonical": "true",
        },
        headers=HX,
    )

    assert r.status_code == 200
    assert await _pin(db, "organization", oid, "legal_name") == (
        "pinned",
        "House Capital Budget Committee",
    )


# --- acronyms ----------------------------------------------------------------------


async def test_editing_the_canonical_acronym_pins_it(client, db):
    oid = await _org(db)
    aid = generate_id()
    await db.execute(
        "INSERT INTO organization_acronyms (id, organization_id, acronym, is_canonical)"
        " VALUES ($1, $2, 'ETT', TRUE)",
        aid,
        oid,
    )

    r = await client.post(
        f"/admin/orgs/{oid}/acronyms/{aid}/edit-row/",
        data={"acronym": "EN", "is_canonical": "true"},
        headers=HX,
    )

    assert r.status_code == 200
    assert await _pin(db, "organization", oid, "acronym") == ("pinned", "EN")
    assert "pinned" in _flash(r).lower()


async def _acronym(db, oid: str, acronym: str = "ETT") -> str:
    aid = generate_id()
    await db.execute(
        "INSERT INTO organization_acronyms (id, organization_id, acronym, is_canonical)"
        " VALUES ($1, $2, $3, TRUE)",
        aid,
        oid,
        acronym,
    )
    return aid


async def test_adding_the_first_acronym_pins_it(client, db):
    oid = await _org(db)

    r = await client.post(
        f"/admin/orgs/{oid}/acronyms/", data={"acronym": "ETT", "is_canonical": "true"}, headers=HX
    )

    assert r.status_code == 200
    assert await _pin(db, "organization", oid, "acronym") == ("pinned", "ETT")


async def test_deleting_the_only_acronym_pins_the_slot_empty(client, db):
    oid = await _org(db)
    aid = await _acronym(db, oid)

    r = await client.delete(f"/admin/orgs/{oid}/acronyms/{aid}/", headers=HX)

    assert r.status_code == 200
    assert await _pin(db, "organization", oid, "acronym") == ("pinned", None)


# --- parent: inline, add child, remove child --------------------------------------


async def test_setting_the_parent_inline_pins_it(client, db):
    parent, oid = await _org(db, "House"), await _org(db)

    r = await client.post(
        f"/admin/orgs/{oid}/inline/parent/", data={"parent_id": parent}, headers=HX
    )

    assert r.status_code == 200
    assert await _pin(db, "organization", oid, "parent_id") == ("pinned", parent)


async def test_linking_a_child_pins_the_childs_parent_not_this_orgs(client, db):
    parent, child = await _org(db, "Appropriations"), await _org(db, "Subcommittee")

    r = await client.post(f"/admin/orgs/{parent}/children/", data={"child_id": child}, headers=HX)

    assert r.status_code == 200
    assert await _pin(db, "organization", child, "parent_id") == ("pinned", parent)
    assert await active_pins(db, "organization", parent) == []


async def test_unlinking_a_child_pins_its_parent_empty(client, db):
    parent = await _org(db, "Appropriations")
    child = await _org(db, "Subcommittee", parent=parent)

    r = await client.delete(f"/admin/orgs/{parent}/children/{child}/", headers=HX)

    assert r.status_code == 200
    assert await _pin(db, "organization", child, "parent_id") == ("pinned", None)


# --- the dissolved event ------------------------------------------------------------


async def test_recording_a_dissolution_pins_its_year(client, db):
    oid = await _org(db)

    r = await client.post(
        f"/admin/orgs/{oid}/events/",
        data={"event_type_id": DISSOLVED, "event_year": "2018"},
        headers=HX,
    )

    assert r.status_code == 200
    assert await _pin(db, "organization", oid, "dissolved_year") == ("pinned", "2018")


async def test_archiving_the_dissolution_pins_it_empty(client, db):
    oid = await _org(db)
    eid = generate_id()
    await db.execute(
        "INSERT INTO entity_events (id, entity_type, entity_id, event_type_id, event_year)"
        " VALUES ($1, 'organization', $2, $3, 2020)",
        eid,
        oid,
        DISSOLVED,
    )

    r = await client.post(f"/admin/orgs/{oid}/events/{eid}/archive/", headers=HX)

    assert r.status_code == 200
    assert await _pin(db, "organization", oid, "dissolved_year") == ("pinned", None)


async def test_an_event_that_is_not_the_dissolution_pins_nothing(client, db):
    oid = await _org(db)
    founded = await db.fetchval("SELECT id FROM entity_event_types WHERE slug = 'founded'")

    await client.post(
        f"/admin/orgs/{oid}/events/",
        data={"event_type_id": founded, "event_year": "1991"},
        headers=HX,
    )

    assert await active_pins(db, "organization", oid) == []


async def _dissolution(db, oid: str, year: int, *, archived: bool = False) -> str:
    eid = generate_id()
    await db.execute(
        "INSERT INTO entity_events (id, entity_type, entity_id, event_type_id, event_year,"
        " archived_at) VALUES ($1, 'organization', $2, $3, $4, CASE WHEN $5 THEN NOW() END)",
        eid,
        oid,
        DISSOLVED,
        year,
        archived,
    )
    return eid


async def test_editing_the_dissolutions_year_pins_the_new_year(client, db):
    oid = await _org(db)
    eid = await _dissolution(db, oid, 2020)

    r = await client.post(
        f"/admin/orgs/{oid}/events/{eid}/edit-row/",
        data={"event_type_id": DISSOLVED, "event_year": "2021"},
        headers=HX,
    )

    assert r.status_code == 200
    assert await _pin(db, "organization", oid, "dissolved_year") == ("pinned", "2021")


async def test_unarchiving_the_dissolution_pins_its_year(client, db):
    oid = await _org(db)
    eid = await _dissolution(db, oid, 2020, archived=True)

    r = await client.post(f"/admin/orgs/{oid}/events/{eid}/unarchive/", headers=HX)

    assert r.status_code == 200
    assert await _pin(db, "organization", oid, "dissolved_year") == ("pinned", "2020")
