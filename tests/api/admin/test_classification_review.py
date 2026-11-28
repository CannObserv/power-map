"""Admin review queue for org kinds (#877 phase 3b, design D5).

``/admin/classification/review/`` lists what bulk accept leaves for an
operator (``classification.review_queue``), disputes first. Each row offers
Accept rules, Accept AI, Set and Dismiss: one POST through
``record_decision(undecided_only=True)``. HTMX gets the current page's region
re-rendered and a flash; other clients a 303 ``with_flash`` to the queue.
"""

import json

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from src.api.admin.deps import get_db
from src.api.main import app
from src.core import classification as cls
from src.core.db import generate_id

pytestmark = [pytest.mark.integration]

AUTH = {"X-ExeDev-UserID": "usr_review_test", "X-ExeDev-Email": "review@test.com"}
HTMX = {**AUTH, "HX-Request": "true"}
URL = "/admin/classification/review/"


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


async def _org(db, name: str) -> str:
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


async def _suggest(db, org_id, source, value, confidence=None):
    await db.execute(
        "INSERT INTO classification_suggestions"
        " (id, entity_type, entity_id, field, source, value, model, confidence)"
        " VALUES ($1,'organization',$2,'org_kind',$3,$4,$5,$6)",
        generate_id(),
        org_id,
        source,
        value,
        cls.RULES_VERSION if source == "rules" else "jev-1.14.0",
        confidence,
    )


async def _disputed(db, name="Review Disputed Org") -> str:
    org = await _org(db, name)
    await _suggest(db, org, "rules", "business")
    await _suggest(db, org, "jev", "tribe", 0.96)
    return org


async def _kind(db, org_id):
    return await db.fetchval(
        "SELECT k.slug FROM organizations o LEFT JOIN org_kinds k ON k.id = o.org_kind_id"
        " WHERE o.id = $1",
        org_id,
    )


def _flash(resp) -> dict:
    return json.loads(resp.headers["HX-Trigger"])["showFlash"]


# --- the page --------------------------------------------------------------


async def test_page_lists_a_queued_org_with_its_answers_and_actions(client, db):
    org = await _disputed(db)
    r = await client.get(URL, params={"page_size": 500}, headers=AUTH)
    assert r.status_code == 200
    assert "<h1>Kind review</h1>" in r.text
    assert f'data-org-id="{org}"' in r.text
    assert f'href="/admin/orgs/{org}/"' in r.text
    assert "Review Disputed Org" in r.text
    assert "Rules and AI disagree" in r.text
    assert "96%" in r.text
    # Both answers name a kind, so both can be accepted; the labels name the org.
    assert 'aria-label="Accept rules kind Business for Review Disputed Org"' in r.text
    assert 'aria-label="Accept AI kind Tribe for Review Disputed Org"' in r.text
    assert 'aria-label="Set kind for Review Disputed Org"' in r.text
    assert 'aria-label="Dismiss Review Disputed Org"' in r.text
    assert f'id="review-kind-{org}"' in r.text


async def test_htmx_get_returns_the_region_only(client, db):
    await _disputed(db)
    r = await client.get(URL, headers=HTMX)
    assert r.status_code == 200
    assert 'id="review-table"' in r.text
    assert "<h1>" not in r.text


async def test_an_org_bulk_accept_would_take_is_not_listed(client, db):
    ready = await _org(db, "Review Ready Org")
    await _suggest(db, ready, "rules", "legislature")
    r = await client.get(URL, params={"page_size": 500}, headers=AUTH)
    assert f'data-org-id="{ready}"' not in r.text


async def test_no_accept_button_for_an_answer_that_names_no_kind(client, db):
    org = await _org(db, "Review No Kind Org")
    await _suggest(db, org, "jev", "none", 0.8)
    r = await client.get(URL, params={"page_size": 500}, headers=AUTH)
    assert f'data-org-id="{org}"' in r.text
    assert "Accept AI kind" not in r.text.split(f'data-org-id="{org}"')[1].split("</tr>")[0]


async def test_a_cleared_org_is_badged(client, db):
    org = await _org(db, "Review Cleared Org")
    await cls.record_decision(db, org_id=org, action="dismiss")
    await cls.record_decision(db, org_id=org, action="clear")
    r = await client.get(URL, params={"page_size": 500}, headers=AUTH)
    row = r.text.split(f'data-org-id="{org}"')[1].split("</tr>")[0]
    assert "Cleared" in row


async def test_pages_by_page_number(client, db):
    for n in range(12):
        await _disputed(db, f"Review Paged Org {n:02}")
    total = len((await cls.review_queue(db, threshold=0.7)).items)
    r = await client.get(URL, params={"page": 2, "page_size": 10}, headers=HTMX)
    assert r.status_code == 200
    assert f"Showing 11–{min(20, total)} of {total}" in r.text


# --- decisions -------------------------------------------------------------


async def test_accept_rules_sets_the_kind_and_drops_the_row(client, db):
    org = await _disputed(db)
    r = await client.post(
        f"{URL}{org}/",
        data={"action": "accept", "kind": "business", "source": "rules", "page_size": "500"},
        headers=HTMX,
    )
    assert r.status_code == 200
    assert await _kind(db, org) == "business"
    flash = _flash(r)
    assert flash["level"] == "success"
    assert "Business" in flash["body"] and "Review Disputed Org" in flash["body"]
    assert 'id="review-table"' in r.text
    assert f'data-org-id="{org}"' not in r.text


async def test_accept_ai_and_set_and_dismiss(client, db):
    by_ai, by_set, dismissed = await _disputed(db), await _disputed(db), await _disputed(db)
    for org, data in (
        (by_ai, {"action": "accept", "kind": "tribe", "source": "jev"}),
        (by_set, {"action": "set", "kind": "nonprofit"}),
        (dismissed, {"action": "dismiss"}),
    ):
        r = await client.post(f"{URL}{org}/", data=data, headers=HTMX)
        assert _flash(r)["level"] == "success", data
    assert (await _kind(db, by_ai), await _kind(db, by_set)) == ("tribe", "nonprofit")
    assert await cls.is_decided(db, dismissed)
    assert await _kind(db, dismissed) is None


async def test_an_org_decided_since_the_page_loaded_is_refused(client, db):
    org = await _disputed(db)
    await cls.record_decision(db, org_id=org, action="set", kind="association")
    r = await client.post(f"{URL}{org}/", data={"action": "set", "kind": "tribe"}, headers=HTMX)
    assert r.status_code == 200
    flash = _flash(r)
    assert flash["level"] == "warning"
    assert "already decided" in flash["body"]
    assert await _kind(db, org) == "association"
    # The region is re-rendered, so the stale row goes.
    assert 'id="review-table"' in r.text
    assert f'data-org-id="{org}"' not in r.text


@pytest.mark.parametrize(
    "data",
    [
        {"action": "clear"},  # not offered here: the org is already undecided
        {"action": "set", "kind": ""},
        {"action": "accept", "kind": "tribe", "source": "rules"},  # rules says business
    ],
)
async def test_a_refused_decision_changes_nothing(client, db, data):
    org = await _disputed(db)
    r = await client.post(f"{URL}{org}/", data=data, headers=HTMX)
    assert _flash(r)["level"] == "warning"
    assert await _kind(db, org) is None
    assert not await cls.is_decided(db, org)


async def test_unknown_org_is_404(client):
    r = await client.post(f"{URL}{generate_id()}/", data={"action": "dismiss"}, headers=HTMX)
    assert r.status_code == 404


async def test_non_htmx_falls_back_to_a_303_with_flash(client, db):
    ok, refused = await _disputed(db), await _disputed(db)
    r = await client.post(f"{URL}{ok}/", data={"action": "dismiss"}, headers=AUTH)
    assert (r.status_code, r.headers["location"]) == (303, f"{URL}?flash=saved")
    r = await client.post(f"{URL}{refused}/", data={"action": "clear"}, headers=AUTH)
    assert (r.status_code, r.headers["location"]) == (303, f"{URL}?flash=invalid")


async def test_the_query_flash_shows_on_the_page(client):
    r = await client.get(URL, params={"flash": "saved"}, headers=AUTH)
    assert "Saved." in r.text


# --- the dashboard ---------------------------------------------------------


async def test_dashboard_counts_the_queue_and_links_to_it(client, db):
    await _disputed(db)
    queue = await cls.review_queue(db, threshold=0.7)
    r = await client.get("/admin/", headers=AUTH)
    assert r.status_code == 200
    assert f'href="{URL}"' in r.text
    assert f'data-review-count="{len(queue.items)}"' in r.text
    assert f'data-ready-count="{queue.ready}"' in r.text
