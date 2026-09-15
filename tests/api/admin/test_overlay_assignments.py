"""The assignment slots in the admin (#527 step 9).

usa-wa owns an assignment's `start_date`, `end_date` and `is_current` now, so a
curator's correction to any of them must survive the next apply — which holds
only if every admin write to those columns pins what it moved. There are four:
the assignment page's dates form and currency toggle, and the inline assignment
rows on the person and role pages. The assignment page carries the three slot
lines. An assignment outside the producer's row scope is direct curation.
"""

import json
from datetime import date

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
START, END = date(2021, 1, 11), date(2022, 12, 31)


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


async def _assignment(db, *, anchored=True, end=END, current=False) -> dict:
    person, org, role, ra = (generate_id() for _ in range(4))
    await db.execute("INSERT INTO people (id) VALUES ($1)", person)
    await db.execute("INSERT INTO organizations (id) VALUES ($1)", org)
    await db.execute(
        "INSERT INTO roles (id, organization_id, title) VALUES ($1, $2, 'Member')", role, org
    )
    await db.execute(
        "INSERT INTO role_assignments (id, person_id, role_id, start_date, end_date, is_current)"
        " VALUES ($1, $2, $3, $4, $5, $6)",
        ra,
        person,
        role,
        START,
        end,
        current,
    )
    if anchored:
        await db.execute(
            "INSERT INTO producer_crosswalk"
            " (id, source, kind, producer_id, exported_pm_id, pm_id, resolution)"
            " VALUES ($1, $2, 'assignment', $3, $4, $4, 'live')",
            generate_id(),
            PRODUCER_SOURCE,
            f"span-{generate_id()}",
            ra,
        )
    return {"person": person, "role": role, "ra": ra}


def _flash(r) -> str:
    return json.loads(r.headers["HX-Trigger"])["showFlash"]["body"]


async def _pinned(db, ra, field):
    held = await active_pin(db, "assignment", ra, field)
    return None if held is None else held.value


# --- the slot lines -----------------------------------------------------------------


async def test_the_assignment_page_hosts_its_three_slot_lines(client, db):
    a = await _assignment(db)

    r = await client.get(f"/admin/role-assignments/{a['ra']}/", headers=AUTH)

    assert r.status_code == 200
    for field in ("start_date", "end_date", "is_current"):
        assert f'hx-get="/admin/_overlay/assignment/{a["ra"]}/{field}/"' in r.text


async def test_the_dates_form_warns_for_both_dates_it_pins(client, db):
    """Saving the form pins whichever date moved, so each carries its note (CR 4)."""
    a = await _assignment(db)

    r = await client.get(f"/admin/role-assignments/{a['ra']}/inline/dates/edit/", headers=HX)

    assert r.status_code == 200
    for field in ("start_date", "end_date"):
        assert f'hx-get="/admin/_overlay/assignment/{a["ra"]}/{field}/?variant=note"' in r.text


async def test_an_anchored_assignments_slot_line_offers_a_pin(client, db):
    a = await _assignment(db)

    r = await client.get(f"/admin/_overlay/assignment/{a['ra']}/end_date/", headers=AUTH)

    assert r.status_code == 200
    assert "Pin current end date" in r.text


async def test_an_assignment_outside_the_crosswalk_has_no_slot_line(client, db):
    a = await _assignment(db, anchored=False)

    r = await client.get(f"/admin/_overlay/assignment/{a['ra']}/end_date/", headers=AUTH)

    assert r.status_code == 200
    assert "Pin" not in r.text


async def test_an_unknown_assignment_is_404(client, db):
    r = await client.get(f"/admin/_overlay/assignment/{generate_id()}/end_date/", headers=AUTH)

    assert r.status_code == 404


async def test_pinning_keeps_the_current_value(client, db):
    a = await _assignment(db)

    r = await client.post(f"/admin/_overlay/assignment/{a['ra']}/end_date/pin/", headers=HX)

    assert r.status_code == 200
    assert await _pinned(db, a["ra"], "end_date") == "2022-12-31"


# --- the four edit sites --------------------------------------------------------------


async def test_the_dates_form_pins_the_dates_it_moved(client, db):
    a = await _assignment(db)

    r = await client.post(
        f"/admin/role-assignments/{a['ra']}/inline/dates/",
        data={"start_date": "2021-01-11", "end_date": "2023-06-30"},
        headers=HX,
    )

    assert r.status_code == 200
    assert await _pinned(db, a["ra"], "end_date") == "2023-06-30"
    assert await active_pin(db, "assignment", a["ra"], "start_date") is None  # unmoved
    assert "pinned" in _flash(r).lower()


async def test_the_currency_toggle_pins_is_current(client, db):
    a = await _assignment(db, end=None)

    r = await client.post(
        f"/admin/role-assignments/{a['ra']}/inline/is_current/",
        data={"is_current": "true"},
        headers=HX,
    )

    assert r.status_code == 200
    assert await _pinned(db, a["ra"], "is_current") == "True"


async def test_the_person_pages_assignment_row_pins_what_it_moved(client, db):
    a = await _assignment(db)

    r = await client.post(
        f"/admin/people/{a['person']}/assignments/{a['ra']}/edit-row/",
        data={"start_date": "2021-02-01", "end_date": "", "is_current": "true"},
        headers=HX,
    )

    assert r.status_code == 200
    pins = {p.field: p.value for p in await active_pins(db, "assignment", a["ra"])}
    assert pins == {"start_date": "2021-02-01", "end_date": None, "is_current": "True"}


async def test_the_role_pages_assignment_row_pins_what_it_moved(client, db):
    a = await _assignment(db)

    r = await client.post(
        f"/admin/roles/{a['role']}/assignments/{a['ra']}/edit-row/",
        data={"start_date": "2021-01-11", "end_date": "2024-12-31", "is_current": ""},
        headers=HX,
    )

    assert r.status_code == 200
    assert await _pinned(db, a["ra"], "end_date") == "2024-12-31"


async def test_an_edit_that_keeps_the_values_pins_nothing(client, db):
    a = await _assignment(db)

    await client.post(
        f"/admin/role-assignments/{a['ra']}/inline/dates/",
        data={"start_date": "2021-01-11", "end_date": "2022-12-31"},
        headers=HX,
    )

    assert await active_pins(db, "assignment", a["ra"]) == []


async def test_an_edit_outside_the_crosswalk_pins_nothing(client, db):
    a = await _assignment(db, anchored=False)

    await client.post(
        f"/admin/role-assignments/{a['ra']}/inline/dates/",
        data={"start_date": "2021-01-11", "end_date": "2023-06-30"},
        headers=HX,
    )

    assert await active_pins(db, "assignment", a["ra"]) == []


async def test_the_non_htmx_fallback_says_it_pinned(client, db):
    a = await _assignment(db)

    r = await client.post(
        f"/admin/role-assignments/{a['ra']}/inline/dates/",
        data={"start_date": "2021-01-11", "end_date": "2023-06-30"},
        headers=AUTH,
    )

    assert r.status_code == 303
    assert "flash=saved_pinned" in r.headers["location"]
