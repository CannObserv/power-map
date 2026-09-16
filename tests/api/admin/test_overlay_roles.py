"""The role title slot in the admin (#529 step 7).

usa-wa owns a role's title now, so a curator's correction must survive the next
apply — which holds only if the admin write pins it. The title editor is the one
route that moves it; the structural editor writes it back unchanged (#497) and so
pins nothing. A role outside the producer's row scope is direct curation.
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


async def _role(db, *, anchored=True, title="Member") -> str:
    org, role = generate_id(), generate_id()
    await db.execute("INSERT INTO organizations (id) VALUES ($1)", org)
    await db.execute(
        "INSERT INTO roles (id, organization_id, title) VALUES ($1, $2, $3)", role, org, title
    )
    if anchored:
        await db.execute(
            "INSERT INTO producer_crosswalk"
            " (id, source, kind, producer_id, exported_pm_id, pm_id, resolution)"
            " VALUES ($1, $2, 'role', $3, $4, $4, 'live')",
            generate_id(),
            PRODUCER_SOURCE,
            f"role-{generate_id()}",
            role,
        )
    return role


def _flash(r) -> str:
    return json.loads(r.headers["HX-Trigger"])["showFlash"]["body"]


# --- the slot line ------------------------------------------------------------------


async def test_the_role_page_hosts_its_slot_line(client, db):
    role = await _role(db)

    r = await client.get(f"/admin/roles/{role}/", headers=AUTH)

    assert r.status_code == 200
    assert f'hx-get="/admin/_overlay/role/{role}/title/"' in r.text


async def test_an_anchored_roles_slot_line_offers_a_pin(client, db):
    role = await _role(db)

    r = await client.get(f"/admin/_overlay/role/{role}/title/", headers=AUTH)

    assert r.status_code == 200
    assert "Pin current title" in r.text


async def test_a_role_outside_the_crosswalk_has_no_slot_line(client, db):
    role = await _role(db, anchored=False)

    r = await client.get(f"/admin/_overlay/role/{role}/title/", headers=AUTH)

    assert r.status_code == 200
    assert "Pin" not in r.text


async def test_an_unknown_role_is_404(client, db):
    r = await client.get(f"/admin/_overlay/role/{generate_id()}/title/", headers=AUTH)

    assert r.status_code == 404


async def test_pinning_keeps_the_current_title(client, db):
    role = await _role(db, title="Chair")

    r = await client.post(f"/admin/_overlay/role/{role}/title/pin/", headers=HX)

    assert r.status_code == 200
    held = await active_pin(db, "role", role, "title")
    assert held is not None and held.value == "Chair"


# --- the edit site ------------------------------------------------------------------


async def test_the_title_editor_pins_what_it_moved(client, db):
    role = await _role(db)

    r = await client.post(
        f"/admin/roles/{role}/inline/title/", data={"title": "Ranking Member"}, headers=HX
    )

    assert r.status_code == 200
    held = await active_pin(db, "role", role, "title")
    assert held is not None and held.value == "Ranking Member"
    assert "pinned" in _flash(r).lower()


async def test_an_edit_that_keeps_the_title_pins_nothing(client, db):
    role = await _role(db, title="Member")

    await client.post(f"/admin/roles/{role}/inline/title/", data={"title": "Member"}, headers=HX)

    assert await active_pins(db, "role", role) == []


async def test_an_edit_outside_the_crosswalk_pins_nothing(client, db):
    role = await _role(db, anchored=False)

    await client.post(f"/admin/roles/{role}/inline/title/", data={"title": "Chair"}, headers=HX)

    assert await active_pins(db, "role", role) == []


async def test_the_non_htmx_fallback_says_it_pinned(client, db):
    role = await _role(db)

    r = await client.post(
        f"/admin/roles/{role}/inline/title/", data={"title": "Chair"}, headers=AUTH
    )

    assert r.status_code == 303
    assert "flash=saved_pinned" in r.headers["location"]
