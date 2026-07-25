"""Admin routes reject assignment writes outside the org's lifespan (#307).

Covers the three write surfaces: the role-assignments section (create,
is_current toggle, dates inline), the role-detail inline rows, and the
person-detail inline rows. Ended org = one with a ``dissolved`` entity event
(see ``v_org_lifespan``). Windows within the lifespan still save.
"""

import json

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from src.api.admin.deps import get_db
from src.api.main import app
from src.core.db import generate_id

pytestmark = [
    pytest.mark.integration,
]

AUTH_HEADERS = {
    "X-ExeDev-UserID": "usr_test",
    "X-ExeDev-Email": "admin@test.com",
}
HTMX_HEADERS = {**AUTH_HEADERS, "HX-Request": "true"}


@pytest_asyncio.fixture(loop_scope="session")
async def db(db_pool):
    """Pool-acquired connection wrapped in a rolled-back transaction."""
    async with db_pool.acquire() as conn:
        tr = conn.transaction()
        await tr.start()
        try:
            yield conn
        finally:
            await tr.rollback()


@pytest_asyncio.fixture(loop_scope="session")
async def client(db):
    """AsyncClient with app, overriding get_db to use the test connection."""

    async def _get_db_override():
        yield db

    app.dependency_overrides[get_db] = _get_db_override
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test", follow_redirects=True
    ) as c:
        yield c
    app.dependency_overrides.pop(get_db, None)


@pytest_asyncio.fixture(loop_scope="session")
async def ended_org_id(db):
    """Org dissolved 2023-01-09."""
    oid = generate_id()
    await db.execute("INSERT INTO organizations (id, active) VALUES ($1, FALSE)", oid)
    await db.execute(
        "INSERT INTO organization_names (id, organization_id, name, is_canonical)"
        " VALUES ($1, $2, 'Ended Org', TRUE)",
        generate_id(),
        oid,
    )
    await db.execute(
        """INSERT INTO entity_events
               (id, entity_type, entity_id, event_type_id, event_year, event_month, event_day)
           SELECT $1, 'organization', $2, t.id, 2023, 1, 9
           FROM entity_event_types t WHERE t.slug = 'dissolved'""",
        generate_id(),
        oid,
    )
    yield oid


@pytest_asyncio.fixture(loop_scope="session")
async def person_id(db):
    pid = generate_id()
    await db.execute("INSERT INTO people (id) VALUES ($1)", pid)
    await db.execute(
        "INSERT INTO person_names (id, person_id, name, is_canonical)"
        " VALUES ($1, $2, 'Test Person', TRUE)",
        generate_id(),
        pid,
    )
    yield pid


@pytest_asyncio.fixture(loop_scope="session")
async def ended_role_id(db, ended_org_id):
    rid = generate_id()
    await db.execute(
        "INSERT INTO roles (id, organization_id, title) VALUES ($1, $2, 'Member')",
        rid,
        ended_org_id,
    )
    yield rid


@pytest_asyncio.fixture(loop_scope="session")
async def former_ra_id(db, person_id, ended_role_id):
    """Former assignment with unknown end on the ended org (legal state)."""
    raid = generate_id()
    await db.execute(
        "INSERT INTO role_assignments (id, person_id, role_id, is_current)"
        " VALUES ($1, $2, $3, FALSE)",
        raid,
        person_id,
        ended_role_id,
    )
    yield raid


def _flash_level(response):
    return json.loads(response.headers["hx-trigger"])["showFlash"]["level"]


# ---------------------------------------------------------------------------
# /admin/role-assignments/ section
# ---------------------------------------------------------------------------


async def test_ra_create_current_on_ended_org_rejected(client, db, person_id, ended_role_id):
    r = await client.post(
        "/admin/role-assignments/new/",
        headers=AUTH_HEADERS,
        data={"person_id": person_id, "role_id": ended_role_id, "is_current": "true"},
    )
    assert r.status_code == 200
    assert b"cannot be current" in r.content
    count = await db.fetchval(
        "SELECT count(*) FROM role_assignments WHERE role_id = $1 AND is_current",
        ended_role_id,
    )
    assert count == 0


async def test_ra_create_end_after_org_end_rejected(client, db, person_id, ended_role_id):
    r = await client.post(
        "/admin/role-assignments/new/",
        headers=AUTH_HEADERS,
        data={
            "person_id": person_id,
            "role_id": ended_role_id,
            "start_date": "2022-01-01",
            "end_date": "2024-06-01",
        },
    )
    assert r.status_code == 200
    assert b"organization" in r.content and b"2023-01-09" in r.content
    count = await db.fetchval(
        "SELECT count(*) FROM role_assignments WHERE role_id = $1 AND end_date IS NOT NULL",
        ended_role_id,
    )
    assert count == 0


async def test_ra_create_window_within_lifespan_saves(client, db, person_id, ended_role_id):
    r = await client.post(
        "/admin/role-assignments/new/",
        headers=AUTH_HEADERS,
        data={
            "person_id": person_id,
            "role_id": ended_role_id,
            "start_date": "2021-02-03",
            "end_date": "2023-01-09",
        },
    )
    assert r.status_code == 200
    count = await db.fetchval(
        "SELECT count(*) FROM role_assignments WHERE role_id = $1 AND end_date = '2023-01-09'",
        ended_role_id,
    )
    assert count == 1


async def test_ra_toggle_current_on_ended_org_rejected(client, db, former_ra_id):
    r = await client.post(
        f"/admin/role-assignments/{former_ra_id}/inline/is_current/",
        headers=HTMX_HEADERS,
        data={"is_current": "true"},
    )
    assert r.status_code == 200
    assert _flash_level(r) == "error"
    assert (
        await db.fetchval("SELECT is_current FROM role_assignments WHERE id = $1", former_ra_id)
        is False
    )


async def test_ra_dates_end_after_org_end_rejected(client, db, former_ra_id):
    r = await client.post(
        f"/admin/role-assignments/{former_ra_id}/inline/dates/",
        headers=HTMX_HEADERS,
        data={"start_date": "2022-01-01", "end_date": "2024-06-01"},
    )
    assert r.status_code == 200
    assert b"2023-01-09" in r.content
    assert (
        await db.fetchval("SELECT end_date FROM role_assignments WHERE id = $1", former_ra_id)
        is None
    )


# ---------------------------------------------------------------------------
# Role-detail inline rows
# ---------------------------------------------------------------------------


async def test_role_inline_create_current_on_ended_org_rejected(
    client, db, person_id, ended_role_id
):
    r = await client.post(
        f"/admin/roles/{ended_role_id}/assignments/",
        headers=HTMX_HEADERS,
        data={"person_id": person_id, "is_current": "true"},
    )
    assert r.status_code == 200
    assert _flash_level(r) == "error"
    count = await db.fetchval(
        "SELECT count(*) FROM role_assignments WHERE role_id = $1 AND is_current",
        ended_role_id,
    )
    assert count == 0


async def test_role_inline_edit_current_on_ended_org_rejected(
    client, db, former_ra_id, ended_role_id
):
    r = await client.post(
        f"/admin/roles/{ended_role_id}/assignments/{former_ra_id}/edit-row/",
        headers=HTMX_HEADERS,
        data={"is_current": "true"},
    )
    assert r.status_code == 200
    assert _flash_level(r) == "error"
    assert (
        await db.fetchval("SELECT is_current FROM role_assignments WHERE id = $1", former_ra_id)
        is False
    )


# ---------------------------------------------------------------------------
# Person-detail inline rows
# ---------------------------------------------------------------------------


async def test_person_inline_create_current_on_ended_org_rejected(
    client, db, person_id, ended_role_id
):
    r = await client.post(
        f"/admin/people/{person_id}/assignments/",
        headers=HTMX_HEADERS,
        data={"role_id": ended_role_id, "is_current": "true"},
    )
    assert r.status_code == 200
    assert _flash_level(r) == "error"
    count = await db.fetchval(
        "SELECT count(*) FROM role_assignments WHERE role_id = $1 AND is_current",
        ended_role_id,
    )
    assert count == 0


async def test_person_inline_edit_end_after_org_end_rejected(client, db, person_id, former_ra_id):
    r = await client.post(
        f"/admin/people/{person_id}/assignments/{former_ra_id}/edit-row/",
        headers=HTMX_HEADERS,
        data={"start_date": "2022-01-01", "end_date": "2024-06-01"},
    )
    assert r.status_code == 200
    assert _flash_level(r) == "error"
    assert (
        await db.fetchval("SELECT end_date FROM role_assignments WHERE id = $1", former_ra_id)
        is None
    )


# ---------------------------------------------------------------------------
# Org detail banner + deactivate warning (#307 UX)
# ---------------------------------------------------------------------------


async def test_org_detail_shows_open_assignment_banner(client, ended_org_id, former_ra_id):
    r = await client.get(f"/admin/orgs/{ended_org_id}/", headers=AUTH_HEADERS)
    assert r.status_code == 200
    assert b"open assignment" in r.content
    assert b"2023-01-09" in r.content


async def test_org_detail_no_banner_on_active_org(client, db, person_id):
    oid = generate_id()
    await db.execute("INSERT INTO organizations (id) VALUES ($1)", oid)
    rid = generate_id()
    await db.execute(
        "INSERT INTO roles (id, organization_id, title) VALUES ($1, $2, 'Member')", rid, oid
    )
    await db.execute(
        "INSERT INTO role_assignments (id, person_id, role_id, is_current)"
        " VALUES ($1, $2, $3, TRUE)",
        generate_id(),
        person_id,
        rid,
    )
    r = await client.get(f"/admin/orgs/{oid}/", headers=AUTH_HEADERS)
    assert r.status_code == 200
    assert b"open assignment" not in r.content


async def test_deactivate_flash_warns_about_open_assignments(client, db, person_id):
    oid = generate_id()
    await db.execute("INSERT INTO organizations (id) VALUES ($1)", oid)
    rid = generate_id()
    await db.execute(
        "INSERT INTO roles (id, organization_id, title) VALUES ($1, $2, 'Member')", rid, oid
    )
    await db.execute(
        "INSERT INTO role_assignments (id, person_id, role_id, is_current)"
        " VALUES ($1, $2, $3, TRUE)",
        generate_id(),
        person_id,
        rid,
    )
    r = await client.post(
        f"/admin/orgs/{oid}/inline/active/",
        headers=HTMX_HEADERS,
        data={"active": ""},
    )
    assert r.status_code == 200
    trigger = json.loads(r.headers["hx-trigger"])
    assert trigger["showFlash"]["level"] == "warning"
    assert "open assignment" in trigger["showFlash"]["body"]


async def test_deactivate_flash_plain_when_no_open_assignments(client, db):
    oid = generate_id()
    await db.execute("INSERT INTO organizations (id) VALUES ($1)", oid)
    r = await client.post(
        f"/admin/orgs/{oid}/inline/active/",
        headers=HTMX_HEADERS,
        data={"active": ""},
    )
    assert r.status_code == 200
    trigger = json.loads(r.headers["hx-trigger"])
    assert trigger["showFlash"]["level"] == "info"
    assert "open assignment" not in trigger["showFlash"]["body"]


# ---------------------------------------------------------------------------
# Active toggle re-renders the open-assignment banner in place (#320)
# ---------------------------------------------------------------------------


async def test_activate_clears_open_assignment_banner_oob(client, db, person_id):
    """Re-activating an inactive org clears its open-assignment banner (#320).

    The banner is server-rendered on GET; the toggle POST must OOB-swap the
    banner container so it disappears without a full reload.
    """
    oid = generate_id()
    await db.execute("INSERT INTO organizations (id, active) VALUES ($1, FALSE)", oid)
    rid = generate_id()
    await db.execute(
        "INSERT INTO roles (id, organization_id, title) VALUES ($1, $2, 'Member')", rid, oid
    )
    await db.execute(
        "INSERT INTO role_assignments (id, person_id, role_id, is_current)"
        " VALUES ($1, $2, $3, TRUE)",
        generate_id(),
        person_id,
        rid,
    )
    r = await client.post(
        f"/admin/orgs/{oid}/inline/active/",
        headers=HTMX_HEADERS,
        data={"active": "true"},
    )
    assert r.status_code == 200
    # Container comes back for the OOB swap, but empty (no banner on active org).
    assert b'id="org-lifespan-banner"' in r.content
    assert b'hx-swap-oob="true"' in r.content
    assert b"open assignment" not in r.content


async def test_deactivate_renders_open_assignment_banner_oob(client, db, person_id):
    """Marking an org inactive surfaces its open-assignment banner (#320 inverse)."""
    oid = generate_id()
    await db.execute("INSERT INTO organizations (id) VALUES ($1)", oid)
    rid = generate_id()
    await db.execute(
        "INSERT INTO roles (id, organization_id, title) VALUES ($1, $2, 'Member')", rid, oid
    )
    await db.execute(
        "INSERT INTO role_assignments (id, person_id, role_id, is_current)"
        " VALUES ($1, $2, $3, TRUE)",
        generate_id(),
        person_id,
        rid,
    )
    r = await client.post(
        f"/admin/orgs/{oid}/inline/active/",
        headers=HTMX_HEADERS,
        data={"active": ""},
    )
    assert r.status_code == 200
    assert b'id="org-lifespan-banner"' in r.content
    assert b'hx-swap-oob="true"' in r.content
    assert b"open assignment" in r.content


async def test_activate_ended_org_keeps_banner_oob(client, db, person_id, ended_role_id):
    """Re-activating an *ended* org keeps the banner — active≠lifespan (#320).

    The gating counts open assignments on any terminal-ish org (archived /
    inactive / ended), so flipping the active flag on a dissolved org must not
    clear the banner; the lifespan end still bounds its members.
    """
    await db.execute(
        "INSERT INTO role_assignments (id, person_id, role_id, is_current)"
        " VALUES ($1, $2, $3, FALSE)",
        generate_id(),
        person_id,
        ended_role_id,
    )
    # ended_org_id is seeded active=FALSE; toggle it active.
    oid = await db.fetchval("SELECT organization_id FROM roles WHERE id=$1", ended_role_id)
    r = await client.post(
        f"/admin/orgs/{oid}/inline/active/",
        headers=HTMX_HEADERS,
        data={"active": "true"},
    )
    assert r.status_code == 200
    assert b'id="org-lifespan-banner"' in r.content
    assert b"open assignment" in r.content
    assert b"2023-01-09" in r.content  # ended-on date still surfaced


# ---------------------------------------------------------------------------
# Dates route on an already-violating (current-on-ended) row — CR round 1
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(loop_scope="session")
async def violating_ra_id(db, person_id, ended_role_id):
    """Current assignment on the ended org (pre-#307 legacy state)."""
    raid = generate_id()
    await db.execute(
        "INSERT INTO role_assignments (id, person_id, role_id, is_current)"
        " VALUES ($1, $2, $3, TRUE)",
        raid,
        person_id,
        ended_role_id,
    )
    yield raid


async def test_dates_start_only_edit_allowed_on_violating_row(client, db, violating_ra_id):
    """The dates form cannot change currency, so it must not enforce it."""
    r = await client.post(
        f"/admin/role-assignments/{violating_ra_id}/inline/dates/",
        headers=HTMX_HEADERS,
        data={"start_date": "2022-01-01", "end_date": ""},
    )
    assert r.status_code == 200
    row = await db.fetchrow(
        "SELECT start_date, is_current FROM role_assignments WHERE id = $1", violating_ra_id
    )
    assert row["start_date"] is not None and row["is_current"] is True


async def test_dates_repair_gets_actionable_check_message(client, db, violating_ra_id):
    """End ≤ ended_on on a current row hits the DB CHECK, whose message says
    to mark as former first — not the circular lifespan message."""
    r = await client.post(
        f"/admin/role-assignments/{violating_ra_id}/inline/dates/",
        headers=HTMX_HEADERS,
        data={"start_date": "", "end_date": "2023-01-09"},
    )
    assert r.status_code == 200
    assert b"former" in r.content
    assert b"cannot be current" not in r.content
    assert (
        await db.fetchval("SELECT end_date FROM role_assignments WHERE id = $1", violating_ra_id)
        is None
    )


async def test_dates_end_after_org_end_still_rejected_on_violating_row(client, db, violating_ra_id):
    r = await client.post(
        f"/admin/role-assignments/{violating_ra_id}/inline/dates/",
        headers=HTMX_HEADERS,
        data={"start_date": "", "end_date": "2024-06-01"},
    )
    assert r.status_code == 200
    assert b"2023-01-09" in r.content
    assert (
        await db.fetchval("SELECT end_date FROM role_assignments WHERE id = $1", violating_ra_id)
        is None
    )


# ---------------------------------------------------------------------------
# Merge flash warns when the surviving org is past its lifespan — CR round 1
# ---------------------------------------------------------------------------


async def test_merge_into_ended_winner_warns_about_open_assignments(
    client, db, ended_org_id, person_id
):
    loser = generate_id()
    await db.execute("INSERT INTO organizations (id) VALUES ($1)", loser)
    await db.execute(
        "INSERT INTO organization_names (id, organization_id, name, is_canonical)"
        " VALUES ($1, $2, 'Loser Org', TRUE)",
        generate_id(),
        loser,
    )
    rid = generate_id()
    await db.execute(
        "INSERT INTO roles (id, organization_id, title) VALUES ($1, $2, 'Chair')", rid, loser
    )
    await db.execute(
        "INSERT INTO role_assignments (id, person_id, role_id, is_current)"
        " VALUES ($1, $2, $3, TRUE)",
        generate_id(),
        person_id,
        rid,
    )
    r = await client.post(
        f"/admin/orgs/{ended_org_id}/merge-with/{loser}/",
        headers=HTMX_HEADERS,
    )
    assert r.status_code == 200
    trigger = json.loads(r.headers["hx-trigger"])
    assert "open assignment" in trigger["showFlash"]["body"]


async def test_merge_into_active_winner_has_no_lifespan_warning(client, db, person_id):
    winner = generate_id()
    loser = generate_id()
    for oid, name in ((winner, "Winner Org"), (loser, "Loser Org 2")):
        await db.execute("INSERT INTO organizations (id) VALUES ($1)", oid)
        await db.execute(
            "INSERT INTO organization_names (id, organization_id, name, is_canonical)"
            " VALUES ($1, $2, $3, TRUE)",
            generate_id(),
            oid,
            name,
        )
    rid = generate_id()
    await db.execute(
        "INSERT INTO roles (id, organization_id, title) VALUES ($1, $2, 'Chair')", rid, loser
    )
    await db.execute(
        "INSERT INTO role_assignments (id, person_id, role_id, is_current)"
        " VALUES ($1, $2, $3, TRUE)",
        generate_id(),
        person_id,
        rid,
    )
    r = await client.post(
        f"/admin/orgs/{winner}/merge-with/{loser}/",
        headers=HTMX_HEADERS,
    )
    assert r.status_code == 200
    trigger = json.loads(r.headers["hx-trigger"])
    assert "open assignment" not in trigger["showFlash"]["body"]
