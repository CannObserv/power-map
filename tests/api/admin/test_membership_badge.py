"""An assignment's membership type in the admin (#671).

usa-wa says who holds a seat in their own right (`principal`) and who serves
under one (`substitute`, and the declared `ex_officio` / `acting`). The admin
shows a non-principal as a badge on the person page's and the role page's
assignment rows and on the assignment page, and never offers an editor: the
value is the producer's. The assignment page carries its overlay slot line, so a
curator can pin the current value.
"""

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from src.api.admin.deps import get_db
from src.api.main import app
from src.core.curation_overlay import active_pin
from src.core.db import generate_id
from src.core.ingestion.crosswalk import PRODUCER_SOURCE

pytestmark = pytest.mark.integration

AUTH = {"X-ExeDev-UserID": "membership-curator", "X-ExeDev-Email": "curator@example.org"}
HX = {**AUTH, "HX-Request": "true"}
BADGE = '<span class="badge badge--neutral">{}</span>'


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


async def _assignment(db, membership: str = "principal") -> dict:
    person, org, role, ra = (generate_id() for _ in range(4))
    await db.execute("INSERT INTO people (id) VALUES ($1)", person)
    await db.execute("INSERT INTO organizations (id) VALUES ($1)", org)
    await db.execute(
        "INSERT INTO roles (id, organization_id, title) VALUES ($1, $2, 'Senator')", role, org
    )
    await db.execute(
        "INSERT INTO role_assignments"
        " (id, person_id, role_id, start_date, end_date, membership_type)"
        " VALUES ($1, $2, $3, '1991-01-14', '1991-04-18', $4)",
        ra,
        person,
        role,
        membership,
    )
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


@pytest.mark.parametrize(
    ("membership", "label"),
    [("substitute", "Substitute"), ("ex_officio", "Ex officio"), ("acting", "Acting")],
)
async def test_every_page_showing_the_assignment_badges_a_non_principal(
    client, db, membership, label
):
    a = await _assignment(db, membership)

    for url in (
        f"/admin/people/{a['person']}/",
        f"/admin/roles/{a['role']}/",
        f"/admin/role-assignments/{a['ra']}/",
    ):
        r = await client.get(url, headers=AUTH)
        assert r.status_code == 200, url
        assert BADGE.format(label) in r.text, url


async def test_a_principal_carries_no_badge(client, db):
    """Nearly every row is a principal: a badge there would be noise."""
    a = await _assignment(db)

    for url in (
        f"/admin/people/{a['person']}/",
        f"/admin/roles/{a['role']}/",
        f"/admin/role-assignments/{a['ra']}/",
    ):
        r = await client.get(url, headers=AUTH)
        assert "badge badge--neutral" not in r.text, url


async def test_the_assignment_page_hosts_the_membership_slot_line(client, db):
    a = await _assignment(db, "substitute")

    r = await client.get(f"/admin/role-assignments/{a['ra']}/", headers=AUTH)

    assert f'hx-get="/admin/_overlay/assignment/{a["ra"]}/membership_type/"' in r.text


async def test_pinning_keeps_the_current_membership(client, db):
    a = await _assignment(db, "substitute")

    r = await client.post(f"/admin/_overlay/assignment/{a['ra']}/membership_type/pin/", headers=HX)

    assert r.status_code == 200
    pin = await active_pin(db, "assignment", a["ra"], "membership_type")
    assert pin is not None and pin.value == "substitute"
