"""Link-as-successors verb (#469): modal preview + event-creating POST.

The dedup flow's alternative to merge for source-re-keyed institutions: pick a
direction, optionally a date, and write one `succeeded_by` event on the
predecessor. The pair then leaves the duplicate candidates via the chain
exclusion rather than via row collapse.
"""

import datetime

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
        transport=ASGITransport(app=app), base_url="http://test", follow_redirects=False
    ) as c:
        yield c
    app.dependency_overrides.pop(get_db, None)


async def _mk_org(db, name):
    oid = generate_id()
    await db.execute("INSERT INTO organizations (id) VALUES ($1)", oid)
    await db.execute(
        "INSERT INTO organization_names (id, organization_id, name, is_canonical)"
        " VALUES ($1, $2, $3, TRUE)",
        generate_id(),
        oid,
        name,
    )
    return oid


@pytest_asyncio.fixture(loop_scope="session")
async def pair(db):
    a = await _mk_org(db, "Succession Link Committee A")
    b = await _mk_org(db, "Succession Link Committee B")
    return a, b


async def _succession_events(db, pred, succ):
    return await db.fetch(
        """SELECT ev.* FROM entity_events ev
           JOIN entity_event_types t ON t.id = ev.event_type_id
           WHERE t.slug = 'succeeded_by' AND ev.entity_id = $1
             AND ev.linked_entity_id = $2 AND ev.archived_at IS NULL""",
        pred,
        succ,
    )


# -- preview modal -----------------------------------------------------------


async def test_preview_modal_renders_both_orgs_and_direction_picker(client, pair):
    a, b = pair
    r = await client.get(f"/admin/orgs/{a}/link-successor-preview/{b}/", headers=HTMX_HEADERS)
    assert r.status_code == 200
    assert "Succession Link Committee A" in r.text
    assert "Succession Link Committee B" in r.text
    assert 'name="predecessor_id"' in r.text
    assert 'name="succession_date"' in r.text
    assert "link-successor" in r.text


async def test_preview_modal_404_on_missing_org(client, pair):
    a, _ = pair
    r = await client.get(
        f"/admin/orgs/{a}/link-successor-preview/{generate_id()}/", headers=HTMX_HEADERS
    )
    assert r.status_code == 404


async def test_preview_defaults_predecessor_from_assignment_recency(client, pair, db):
    """The org whose assignment activity ended earlier defaults to predecessor."""
    a, b = pair
    person = generate_id()
    await db.execute("INSERT INTO people (id) VALUES ($1)", person)
    for org, start, end in ((a, "1999-01-01", "2000-12-31"), (b, "2021-01-01", "2022-12-31")):
        role = generate_id()
        await db.execute(
            "INSERT INTO roles (id, organization_id, title) VALUES ($1, $2, 'Member')",
            role,
            org,
        )
        await db.execute(
            "INSERT INTO role_assignments (id, role_id, person_id, start_date, end_date,"
            " is_current) VALUES ($1, $2, $3, $4::date, $5::date, FALSE)",
            generate_id(),
            role,
            person,
            datetime.date.fromisoformat(start),
            datetime.date.fromisoformat(end),
        )
    r = await client.get(f"/admin/orgs/{b}/link-successor-preview/{a}/", headers=HTMX_HEADERS)
    assert r.status_code == 200
    # Radio for org A (the older activity) is pre-checked as predecessor.
    assert f'value="{a}" checked' in " ".join(r.text.split())


# -- POST --------------------------------------------------------------------


async def test_post_creates_succession_event(client, pair, db):
    a, b = pair
    r = await client.post(f"/admin/orgs/{a}/link-successor/{b}/", headers=HTMX_HEADERS, data={})
    assert r.status_code == 200
    events = await _succession_events(db, a, b)
    assert len(events) == 1
    assert events[0]["event_year"] is None


async def test_post_with_date_creates_dated_event_bounding_lifespan(client, pair, db):
    a, b = pair
    r = await client.post(
        f"/admin/orgs/{a}/link-successor/{b}/",
        headers=HTMX_HEADERS,
        data={"succession_date": "2020-12-31"},
    )
    assert r.status_code == 200
    events = await _succession_events(db, a, b)
    assert (events[0]["event_year"], events[0]["event_month"], events[0]["event_day"]) == (
        2020,
        12,
        31,
    )
    ended = await db.fetchval("SELECT ended_on FROM v_org_lifespan WHERE organization_id = $1", a)
    assert ended == datetime.date(2020, 12, 31)


async def test_post_rejects_self_link(client, pair):
    a, _ = pair
    r = await client.post(f"/admin/orgs/{a}/link-successor/{a}/", headers=HTMX_HEADERS, data={})
    assert r.status_code == 400


async def test_post_already_chained_is_rejected_without_second_event(client, pair, db):
    """A pair already in one chain (either direction) gains no second edge."""
    a, b = pair
    await client.post(f"/admin/orgs/{a}/link-successor/{b}/", headers=HTMX_HEADERS, data={})
    r = await client.post(f"/admin/orgs/{b}/link-successor/{a}/", headers=HTMX_HEADERS, data={})
    assert r.status_code == 200  # warning flash, not an error page
    assert "warning" in r.headers.get("HX-Trigger", "")
    assert len(await _succession_events(db, a, b)) == 1
    assert len(await _succession_events(db, b, a)) == 0


async def test_post_invalid_date_is_warning(client, pair, db):
    a, b = pair
    r = await client.post(
        f"/admin/orgs/{a}/link-successor/{b}/",
        headers=HTMX_HEADERS,
        data={"succession_date": "not-a-date"},
    )
    assert r.status_code == 200
    assert "warning" in r.headers.get("HX-Trigger", "")
    assert len(await _succession_events(db, a, b)) == 0


async def test_post_from_duplicates_region_returns_refreshed_region(client, pair):
    a, b = pair
    r = await client.post(
        f"/admin/orgs/{a}/link-successor/{b}/",
        headers={**HTMX_HEADERS, "HX-Target": "orgs-duplicates-region"},
        data={},
    )
    assert r.status_code == 200
    assert 'id="orgs-duplicates-region"' in r.text
    assert "success" in r.headers.get("HX-Trigger", "")
    assert "refreshDupBadge" in r.headers.get("HX-Trigger", "")


async def test_post_non_htmx_falls_back_to_redirect_with_flash(client, pair):
    a, b = pair
    r = await client.post(f"/admin/orgs/{a}/link-successor/{b}/", headers=AUTH_HEADERS, data={})
    assert r.status_code == 303
    assert "flash=" in r.headers["location"]


# -- dedup region wiring -----------------------------------------------------


async def test_duplicates_region_offers_link_as_successors(client, db):
    a = await _mk_org(db, "Duplicated Succession Candidate Org")
    b = await _mk_org(db, "Duplicated Succession Candidate Org")
    r = await client.get("/admin/orgs/duplicates/", headers=AUTH_HEADERS)
    assert r.status_code == 200
    assert "link-successor-preview" in r.text
    assert "Link as successors" in r.text
    assert a in r.text and b in r.text


# -- ctx-aware modal target (#469 CR round 1, finding 1) ---------------------


async def test_preview_from_duplicates_ctx_targets_the_region(client, pair):
    a, b = pair
    r = await client.get(
        f"/admin/orgs/{a}/link-successor-preview/{b}/?ctx=duplicates", headers=HTMX_HEADERS
    )
    assert r.status_code == 200
    text = " ".join(r.text.split())
    assert 'hx-target="#orgs-duplicates-region"' in text


async def test_preview_without_ctx_swaps_none(client, pair):
    """Opened from the merge-preview guardrail (org list/detail pages) the
    duplicates region does not exist — a dangling hx-target would make HTMX
    abort the POST with htmx:targetError. The form must use hx-swap="none"."""
    a, b = pair
    r = await client.get(f"/admin/orgs/{a}/link-successor-preview/{b}/", headers=HTMX_HEADERS)
    assert r.status_code == 200
    text = " ".join(r.text.split())
    assert 'hx-target="#orgs-duplicates-region"' not in text
    assert 'hx-swap="none"' in text
