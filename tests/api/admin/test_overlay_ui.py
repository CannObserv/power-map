"""The overlay's admin surface, per slot (#498): legible, pinnable, unpinnable.

Every producer-owned slot on an in-scope entity says so where it is shown and
where it is edited; a pinned slot carries a badge with the value, who pinned it
and when, and an Unpin; an unpinned one shows its value and a Pin that keeps
it, or *none* and no Pin when it is empty (#838). An entity outside the
producer's row scope shows none of it. The slot line is a self-loading fragment
(the dup-badge pattern), so every panel stays a static host and refreshes on
`refreshOverlay` when an edit pins.
"""

import json

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from src.api.admin.deps import get_db
from src.api.main import app
from src.core.curation_overlay import active_pin, pin
from src.core.db import generate_id
from src.core.ingestion.crosswalk import PRODUCER_SOURCE

DISSOLVED = "01KV0000000000000000000007"  # seeded entity_event_types.dissolved

pytestmark = pytest.mark.integration

AUTH = {"X-ExeDev-UserID": "overlay-ui-curator", "X-ExeDev-Email": "curator@example.org"}
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


async def _org(db, *, anchored: bool = True, acronym: str | None = "ETT") -> str:
    oid = generate_id()
    await db.execute("INSERT INTO organizations (id) VALUES ($1)", oid)
    await db.execute(
        "INSERT INTO organization_names (id, organization_id, name, name_type, is_canonical)"
        " VALUES ($1, $2, 'Energy and Telecommunications', 'legal', TRUE)",
        generate_id(),
        oid,
    )
    if acronym:
        await db.execute(
            "INSERT INTO organization_acronyms (id, organization_id, acronym, is_canonical)"
            " VALUES ($1, $2, $3, TRUE)",
            generate_id(),
            oid,
            acronym,
        )
    if anchored:
        await db.execute(
            "INSERT INTO producer_crosswalk"
            " (id, source, kind, producer_id, exported_pm_id, pm_id, resolution)"
            " VALUES ($1, $2, 'organization', $3, $4, $4, 'live')",
            generate_id(),
            PRODUCER_SOURCE,
            f"p-{generate_id()}",
            oid,
        )
    return oid


async def _dissolve(db, oid: str, year: int) -> None:
    await db.execute(
        "INSERT INTO entity_events (id, entity_type, entity_id, event_type_id, event_year)"
        " VALUES ($1, 'organization', $2, $3, $4)",
        generate_id(),
        oid,
        DISSOLVED,
        year,
    )


def _slot(oid: str, field: str = "acronym") -> str:
    return f"/admin/_overlay/organization/{oid}/{field}/"


# --- the slot fragment -------------------------------------------------------------


async def test_an_unpinned_in_scope_slot_says_who_maintains_it_and_offers_a_pin(client, db):
    oid = await _org(db)

    r = await client.get(_slot(oid), headers=AUTH)

    assert r.status_code == 200
    assert "Maintained by usa-wa" in r.text
    assert f"{_slot(oid)}pin/" in r.text and "Pinned" not in r.text


async def test_a_pinned_slot_shows_the_badge_the_value_who_and_an_unpin(client, db, curator):
    oid = await _org(db)
    await pin(db, "organization", oid, "acronym", "EN", user_id=curator, note="the committee's own")

    r = await client.get(_slot(oid), headers=AUTH)

    assert 'class="badge badge--pinned"' in r.text
    assert "EN" in r.text and "curator@example.org" in r.text and "the committee" in r.text
    assert f"{_slot(oid)}unpin/" in r.text
    held = await active_pin(db, "organization", oid, "acronym")
    assert f'"pin_id": "{held.id}"' in r.text  # Unpin names the pin it shows


async def test_a_null_pin_reads_as_kept_empty(client, db, curator):
    oid = await _org(db)
    await pin(db, "organization", oid, "parent_id", None, user_id=curator)

    r = await client.get(_slot(oid, "parent_id"), headers=AUTH)

    assert "kept empty" in r.text


async def test_a_pinned_parent_shows_the_parents_name_not_its_id(client, db, curator):
    parent, oid = await _org(db, acronym=None), await _org(db)
    await pin(db, "organization", oid, "parent_id", parent, user_id=curator)

    r = await client.get(_slot(oid, "parent_id"), headers=AUTH)

    assert "Energy and Telecommunications" in r.text and parent not in r.text.split("hx-")[0]


async def test_an_entity_outside_the_crosswalk_shows_nothing(client, db):
    oid = await _org(db, anchored=False)

    r = await client.get(_slot(oid), headers=AUTH)

    assert r.status_code == 200 and r.text.strip() == ""


async def test_the_edit_note_says_saving_pins_and_only_in_scope(client, db):
    inside, outside = await _org(db), await _org(db, anchored=False)

    shown = await client.get(_slot(inside) + "?variant=note", headers=AUTH)
    hidden = await client.get(_slot(outside) + "?variant=note", headers=AUTH)

    assert "Saving a change pins your value" in shown.text
    assert hidden.text.strip() == ""


@pytest.mark.parametrize(
    "path",
    ["/admin/_overlay/organization/{oid}/name/", "/admin/_overlay/jurisdiction/{oid}/acronym/"],
    ids=["a field the type does not own", "a type with no slots"],
)
async def test_a_slot_the_registry_does_not_hold_is_not_found(client, db, path):
    oid = await _org(db)

    r = await client.get(path.format(oid=oid), headers=AUTH)

    assert r.status_code == 404


# --- Pin and Unpin -------------------------------------------------------------------


async def test_pin_keeps_the_current_value_and_returns_the_pinned_slot(client, db):
    oid = await _org(db)

    r = await client.post(_slot(oid) + "pin/", data={"expected": "ETT"}, headers=HX)

    assert r.status_code == 200
    assert (await active_pin(db, "organization", oid, "acronym")).value == "ETT"
    assert 'class="badge badge--pinned"' in r.text
    trigger = json.loads(r.headers["HX-Trigger"])
    assert trigger["showFlash"]["level"] == "success" and "refreshOverlay" in trigger


async def test_unpin_archives_and_returns_the_unpinned_slot(client, db, curator):
    oid = await _org(db)
    held = await pin(db, "organization", oid, "acronym", "EN", user_id=curator)

    r = await client.post(_slot(oid) + "unpin/", data={"pin_id": held.id}, headers=HX)

    assert r.status_code == 200
    assert await active_pin(db, "organization", oid, "acronym") is None
    assert "Maintained by usa-wa" in r.text
    assert "returns on the next apply" in json.loads(r.headers["HX-Trigger"])["showFlash"]["body"]


@pytest.mark.parametrize(("action", "key"), [("pin", "pinned"), ("unpin", "unpinned")])
async def test_the_non_htmx_fallback_returns_to_the_entity(client, db, curator, action, key):
    oid = await _org(db)
    data = {"expected": "ETT"}
    if action == "unpin":  # Pin is offered only on an unpinned slot, Unpin only on a pinned one
        held = await pin(db, "organization", oid, "acronym", "EN", user_id=curator)
        data = {"pin_id": held.id}

    r = await client.post(_slot(oid) + f"{action}/", data=data, headers=AUTH)

    assert r.status_code == 303
    assert r.headers["location"] == f"/admin/orgs/{oid}/?flash={key}"


async def test_unpin_from_a_stale_line_is_a_warning_and_leaves_the_newer_pin(client, db, curator):
    """The line showed a pin another curator has since replaced. Unpinning *by field*
    would archive the newer pin, never shown; the pin the line named is archived only
    while it is still the live one (`unpin_pin`), and the line re-renders as it is."""
    oid = await _org(db)
    shown = await pin(db, "organization", oid, "acronym", "EN", user_id=curator)
    await pin(db, "organization", oid, "acronym", "ENV", user_id=curator)

    r = await client.post(_slot(oid) + "unpin/", data={"pin_id": shown.id}, headers=HX)

    assert r.status_code == 200
    assert json.loads(r.headers["HX-Trigger"])["showFlash"]["level"] == "warning"
    assert (await active_pin(db, "organization", oid, "acronym")).value == "ENV"
    assert "ENV" in r.text and 'class="badge badge--pinned"' in r.text


async def test_a_pin_id_from_another_slot_unpins_nothing(client, db, curator):
    """The id is checked against *this* slot's live pin, so a posted id cannot reach
    across to archive a pin on another field."""
    oid = await _org(db)
    other = await pin(db, "organization", oid, "legal_name", "Energy Committee", user_id=curator)

    r = await client.post(_slot(oid) + "unpin/", data={"pin_id": other.id}, headers=AUTH)

    assert r.status_code == 303
    assert r.headers["location"] == f"/admin/orgs/{oid}/?flash=pin_stale"
    assert await active_pin(db, "organization", oid, "legal_name") is not None


async def test_pin_on_a_slot_pinned_since_the_page_loaded_keeps_that_pin(client, db, curator):
    """Pin is offered only while no pin is live. One live now arrived after the page
    loaded — a merge carried it here, or it was pinned elsewhere — and may hold a
    value other than the slot's; replacing it would discard a pin this curator never
    saw. So: a warning, and the line as it stands."""
    oid = await _org(db)  # the live acronym is ETT
    held = await pin(db, "organization", oid, "acronym", "EN", user_id=curator)

    r = await client.post(_slot(oid) + "pin/", data={"expected": "ETT"}, headers=HX)
    fallback = await client.post(_slot(oid) + "pin/", data={"expected": "ETT"}, headers=AUTH)

    assert json.loads(r.headers["HX-Trigger"])["showFlash"]["level"] == "warning"
    assert (await active_pin(db, "organization", oid, "acronym")).id == held.id
    assert "EN" in r.text and 'class="badge badge--pinned"' in r.text
    assert fallback.headers["location"] == f"/admin/orgs/{oid}/?flash=already_pinned"


async def test_pinning_an_entity_outside_the_crosswalk_is_refused_not_a_500(client, db):
    oid = await _org(db, anchored=False)

    r = await client.post(_slot(oid) + "pin/", data={"expected": "ETT"}, headers=HX)

    assert r.status_code == 200
    assert json.loads(r.headers["HX-Trigger"])["showFlash"]["level"] == "warning"
    assert r.headers.get("hx-reswap") == "none", "a refusal must not swap (#684)"
    assert await active_pin(db, "organization", oid, "acronym") is None


# --- #838: the value the line governs, and what an empty slot offers ----------------


async def test_an_empty_dissolved_year_reads_none_and_offers_no_pin(client, db):
    oid = await _org(db)

    r = await client.get(_slot(oid, "dissolved_year"), headers=AUTH)

    assert "Dissolved year: <em>none</em>. Maintained by usa-wa." in r.text
    assert "pin/" not in r.text and "Pin current" not in r.text


async def test_a_filled_dissolved_year_shows_the_year_and_pins_it(client, db):
    oid = await _org(db)
    await _dissolve(db, oid, 2019)

    r = await client.get(_slot(oid, "dissolved_year"), headers=AUTH)

    assert "Dissolved year: <strong>2019</strong>. Maintained by usa-wa." in r.text
    assert ">Pin dissolved year</button>" in r.text
    assert """hx-vals='{"expected": "2019"}'""" in r.text


async def test_an_empty_parent_reads_none_and_offers_no_pin(client, db):
    oid = await _org(db)

    r = await client.get(_slot(oid, "parent_id"), headers=AUTH)

    assert "Parent organization: <em>none</em>. Maintained by usa-wa." in r.text
    assert "pin/" not in r.text


async def test_a_filled_parent_shows_its_name_and_posts_its_id(client, db):
    parent, oid = await _org(db, acronym=None), await _org(db)
    await db.execute("UPDATE organizations SET parent_id = $1 WHERE id = $2", parent, oid)

    r = await client.get(_slot(oid, "parent_id"), headers=AUTH)

    assert "Parent organization: <strong>Energy and Telecommunications</strong>." in r.text
    assert f"""hx-vals='{{"expected": "{parent}"}}'""" in r.text
    assert ">Pin parent organization</button>" in r.text


async def test_a_pin_that_differs_from_the_live_value_says_what_pm_reads(client, db, curator):
    oid = await _org(db)  # the live acronym is ETT
    await pin(db, "organization", oid, "acronym", "EN", user_id=curator)

    r = await client.get(_slot(oid), headers=AUTH)

    assert "Acronym: <strong>EN</strong>." in r.text
    assert "PM's record reads <strong>ETT</strong> until the next apply restores the pin." in r.text


async def test_pin_on_an_empty_slot_is_refused_and_pins_nothing(client, db):
    """The line offers no Pin over an empty slot; a stale page that still has one
    (the value since gone) must not pin NULL, a *keep empty* nobody chose."""
    oid = await _org(db)

    url, data = _slot(oid, "dissolved_year") + "pin/", {"expected": "2019"}

    r = await client.post(url, data=data, headers=HX)
    fallback = await client.post(url, data=data, headers=AUTH)

    flash = json.loads(r.headers["HX-Trigger"])["showFlash"]
    assert flash["level"] == "warning" and "changed since the page loaded" in flash["body"]
    assert "Dissolved year: <em>none</em>" in r.text  # the line as it now stands
    assert fallback.status_code == 303
    assert fallback.headers["location"] == f"/admin/orgs/{oid}/?flash=slot_changed"
    assert await active_pin(db, "organization", oid, "dissolved_year") is None


@pytest.mark.parametrize("data", [{"expected": "EN"}, {}], ids=["another value", "none posted"])
async def test_pin_of_a_value_the_line_did_not_show_is_refused(client, db, data):
    oid = await _org(db)  # the live acronym is ETT

    r = await client.post(_slot(oid) + "pin/", data=data, headers=HX)

    assert json.loads(r.headers["HX-Trigger"])["showFlash"]["level"] == "warning"
    assert "Acronym: <strong>ETT</strong>" in r.text
    assert await active_pin(db, "organization", oid, "acronym") is None


# --- the hosts, and the refresh after a pinning edit ---------------------------------


async def test_the_detail_pages_host_every_slot_line(client, db):
    oid = await _org(db)
    pid = generate_id()
    await db.execute("INSERT INTO people (id) VALUES ($1)", pid)

    org_page = (await client.get(f"/admin/orgs/{oid}/", headers=AUTH)).text
    person_page = (await client.get(f"/admin/people/{pid}/", headers=AUTH)).text

    for field in ("legal_name", "acronym", "parent_id", "dissolved_year"):
        assert f'hx-get="/admin/_overlay/organization/{oid}/{field}/"' in org_page, field
    assert f'hx-get="/admin/_overlay/person/{pid}/name/"' in person_page
    assert "refreshOverlay from:body" in org_page


async def test_an_edit_form_hosts_the_note(client, db):
    oid = await _org(db)

    r = await client.get(f"/admin/orgs/{oid}/acronyms/new-row/", headers=HX)

    assert f'hx-get="/admin/_overlay/organization/{oid}/acronym/?variant=note"' in r.text


async def test_an_edit_that_pins_asks_the_slot_lines_to_refresh(client, db):
    oid = await _org(db)
    aid = await db.fetchval("SELECT id FROM organization_acronyms WHERE organization_id = $1", oid)

    r = await client.post(
        f"/admin/orgs/{oid}/acronyms/{aid}/edit-row/",
        data={"acronym": "EN", "is_canonical": "true"},
        headers=HX,
    )

    assert "refreshOverlay" in json.loads(r.headers["HX-Trigger"])
