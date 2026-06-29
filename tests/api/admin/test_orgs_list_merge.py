"""Integration tests for the Orgs list merge flow (issue #250).

Parity with the People list merge (`test_people_list_merge.py`). Covers the
additions on top of the existing `POST /admin/orgs/{a}/merge/{b}/` route:

* HX-Target == "orgs-list-region" branch → returns `_region.html` (rows +
  caption total + sticky pagination), not the duplicates region.
* HX-Current-URL parsing → response respects the user's current `q` / `status`
  / `page` / `page_size` filters, including the org-only `inactive` status.
* Regression: HTMX without the list-target header still returns the duplicates
  region (existing duplicates-page flow); non-HTMX still 303-redirects.
* List template renders the merge button, merge bar markup, and the checkbox
  column on each row.
"""

import json
import re

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient

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
# The list-flow merge swaps the whole region so caption and sticky pagination
# stay in sync with the post-merge row count.
LIST_TARGET_HEADERS = {**HTMX_HEADERS, "HX-Target": "orgs-list-region"}

EDIT_ANCHOR = 'class="btn btn--ghost btn--sm" aria-label="Edit'


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


@pytest_asyncio.fixture(loop_scope="session")
async def org_pair(db_pool):
    """Two near-dup orgs with a unique discriminator token for filter tests."""
    id_a, id_b = generate_id(), generate_id()
    if id_a > id_b:
        id_a, id_b = id_b, id_a

    async with db_pool.acquire() as conn:
        for oid, name in [
            (id_a, "Xyzzy Merge Holdings"),
            (id_b, "Xyzzy Merge Holdings LLC"),
        ]:
            await conn.execute("INSERT INTO organizations (id) VALUES ($1)", oid)
            await conn.execute(
                "INSERT INTO organization_names"
                " (id, organization_id, name, is_canonical)"
                " VALUES ($1, $2, $3, TRUE)",
                generate_id(),
                oid,
                name,
            )

    yield id_a, id_b

    async with db_pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM duplicate_dismissals"
            " WHERE entity_a_id=$1 OR entity_b_id=$1"
            " OR entity_a_id=$2 OR entity_b_id=$2",
            id_a,
            id_b,
        )
        for oid in [id_a, id_b]:
            await conn.execute("DELETE FROM organization_names WHERE organization_id=$1", oid)
            await conn.execute("DELETE FROM organizations WHERE id=$1", oid)


# ── List template additions ─────────────────────────────────────────────────


async def test_orgs_list_renders_merge_button(client):
    """The list page must include the Merge toggle button."""
    response = client.get("/admin/orgs/", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert 'id="orgs-merge-btn"' in response.text


async def test_orgs_list_renders_merge_btn_wrapper(client):
    """Wrapper element required for disabled-cursor/title affordance."""
    response = client.get("/admin/orgs/", headers=AUTH_HEADERS)
    assert 'id="orgs-merge-btn-wrap"' in response.text


async def test_orgs_list_loads_merge_js(client):
    """The Orgs list page must serve the orgs merge assets (loaded site-wide)."""
    response = client.get("/admin/orgs/", headers=AUTH_HEADERS)
    assert "orgs-merge.js" in response.text
    assert "merge-mode.js" in response.text


async def test_orgs_merge_js_loaded_exactly_once(client):
    """orgs-merge.js must load exactly once — a duplicate load double-binds the
    document-level listeners and turns Merge into a no-op (cf. #249)."""
    response = client.get("/admin/orgs/", headers=AUTH_HEADERS)
    tags = re.findall(r"<script[^>]+orgs-merge\.js", response.text)
    assert len(tags) == 1


async def test_orgs_list_renders_merge_bar(client):
    """Merge action bar markup must be present (hidden via inline display:none)."""
    response = client.get("/admin/orgs/", headers=AUTH_HEADERS)
    assert 'id="orgs-merge-bar"' in response.text
    assert "merge-bar__keep-a" in response.text
    assert "merge-bar__keep-b" in response.text


async def test_orgs_list_tbody_has_known_id(client):
    """`#orgs-table-body` is the table anchor; renaming silently breaks merge JS."""
    response = client.get("/admin/orgs/", headers=AUTH_HEADERS)
    assert 'id="orgs-table-body"' in response.text


async def test_orgs_list_rows_have_merge_checkboxes(client, org_pair):
    """Each row must carry the merge-select checkbox + data-org-id."""
    response = client.get("/admin/orgs/?q=Xyzzy", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert 'name="merge-select"' in response.text
    assert "data-org-id=" in response.text


async def test_orgs_list_rows_have_data_title(client, org_pair):
    """JS reads `data-title` from `<tr>` to label the Keep buttons."""
    response = client.get("/admin/orgs/?q=Xyzzy", headers=AUTH_HEADERS)
    assert 'data-title="Xyzzy Merge Holdings"' in response.text


# ── List-flow merge route branch ────────────────────────────────────────────


async def test_merge_with_list_target_returns_region_partial(client, org_pair):
    """HX-Target=orgs-list-region must return the region partial (table +
    caption + sticky pagination), not the duplicates region."""
    id_a, id_b = org_pair
    response = client.post(
        f"/admin/orgs/{id_a}/merge/{id_b}/",
        headers={**LIST_TARGET_HEADERS, "HX-Current-URL": "/admin/orgs/?q=Xyzzy"},
        follow_redirects=False,
    )
    assert response.status_code == 200
    assert 'id="orgs-duplicates-region"' not in response.text
    assert 'id="orgs-table"' in response.text
    assert (
        "<caption>Organizations &mdash;" in response.text
        or "<caption>Organizations —" in response.text
    )


async def test_merge_with_list_target_refreshes_total_count(client, org_pair):
    """Caption shows post-merge total (loser hard-deleted)."""
    id_a, id_b = org_pair
    pre = client.get("/admin/orgs/?q=Xyzzy", headers=HTMX_HEADERS)
    assert "Organizations &mdash; 2 record" in pre.text or "Organizations — 2 record" in pre.text

    response = client.post(
        f"/admin/orgs/{id_a}/merge/{id_b}/",
        headers={**LIST_TARGET_HEADERS, "HX-Current-URL": "/admin/orgs/?q=Xyzzy"},
        follow_redirects=False,
    )
    assert response.status_code == 200
    assert "Organizations &mdash; 1 record" in response.text or "Organizations — 1 record" in (
        response.text
    )


async def test_merge_with_list_target_includes_winner_row(client, org_pair):
    """Refreshed list must still include the winner (it survived)."""
    id_a, id_b = org_pair
    response = client.post(
        f"/admin/orgs/{id_a}/merge/{id_b}/",
        headers={**LIST_TARGET_HEADERS, "HX-Current-URL": "/admin/orgs/?q=Xyzzy"},
        follow_redirects=False,
    )
    assert response.status_code == 200
    assert f"/admin/orgs/{id_a}/" in response.text


async def test_merge_with_list_target_excludes_loser_row(client, org_pair):
    """Loser must not appear in the refreshed rows (hard-deleted)."""
    id_a, id_b = org_pair
    response = client.post(
        f"/admin/orgs/{id_a}/merge/{id_b}/",
        headers={**LIST_TARGET_HEADERS, "HX-Current-URL": "/admin/orgs/?q=Xyzzy"},
        follow_redirects=False,
    )
    assert response.status_code == 200
    assert f"/admin/orgs/{id_b}/" not in response.text


async def test_merge_with_list_target_sends_flash(client, org_pair):
    """List-flow merge must trigger a success flash via HX-Trigger."""
    id_a, id_b = org_pair
    response = client.post(
        f"/admin/orgs/{id_a}/merge/{id_b}/",
        headers={**LIST_TARGET_HEADERS, "HX-Current-URL": "/admin/orgs/"},
        follow_redirects=False,
    )
    assert response.status_code == 200
    assert "HX-Trigger" in response.headers
    payload = json.loads(response.headers["HX-Trigger"])
    assert payload["showFlash"]["level"] == "success"
    assert "Xyzzy Merge Holdings" in payload["showFlash"]["body"]


async def test_merge_with_list_target_emits_refresh_dup_badge(client, org_pair):
    """List-flow merge response must include refreshDupBadge in HX-Trigger."""
    id_a, id_b = org_pair
    response = client.post(
        f"/admin/orgs/{id_a}/merge/{id_b}/",
        headers={**LIST_TARGET_HEADERS, "HX-Current-URL": "/admin/orgs/"},
        follow_redirects=False,
    )
    assert response.status_code == 200
    trigger = json.loads(response.headers.get("HX-Trigger", "{}"))
    assert "refreshDupBadge" in trigger


# ── Filter-state preservation via HX-Current-URL ─────────────────────────────


async def test_merge_preserves_q_filter(client, org_pair):
    """HX-Current-URL `?q=zzz_nomatch` must filter the winner out of refreshed rows."""
    id_a, id_b = org_pair
    response = client.post(
        f"/admin/orgs/{id_a}/merge/{id_b}/",
        headers={**LIST_TARGET_HEADERS, "HX-Current-URL": "/admin/orgs/?q=zzz_nomatch"},
        follow_redirects=False,
    )
    assert response.status_code == 200
    assert f"/admin/orgs/{id_a}/" not in response.text


async def test_merge_preserves_status_filter(client, org_pair):
    """HX-Current-URL `?status=archived` filters winner (active) out of the rows."""
    id_a, id_b = org_pair
    response = client.post(
        f"/admin/orgs/{id_a}/merge/{id_b}/",
        headers={**LIST_TARGET_HEADERS, "HX-Current-URL": "/admin/orgs/?status=archived"},
        follow_redirects=False,
    )
    assert response.status_code == 200
    assert f"/admin/orgs/{id_a}/" not in response.text


async def test_merge_preserves_inactive_status_filter(client, org_pair):
    """Org-only `?status=inactive` must round-trip and filter the active winner out.

    Guards against copy-pasting People's two-value status set (which would
    collapse `inactive` → `active` and wrongly include the winner)."""
    id_a, id_b = org_pair
    response = client.post(
        f"/admin/orgs/{id_a}/merge/{id_b}/",
        headers={**LIST_TARGET_HEADERS, "HX-Current-URL": "/admin/orgs/?status=inactive"},
        follow_redirects=False,
    )
    assert response.status_code == 200
    # Winner survived but is active, so the inactive filter excludes it.
    assert f"/admin/orgs/{id_a}/" not in response.text


async def test_merge_handles_missing_hx_current_url(client, org_pair):
    """Missing HX-Current-URL falls back to defaults (active status, q="")."""
    id_a, id_b = org_pair
    response = client.post(
        f"/admin/orgs/{id_a}/merge/{id_b}/",
        headers=LIST_TARGET_HEADERS,
        follow_redirects=False,
    )
    assert response.status_code == 200
    assert f"/admin/orgs/{id_a}/" in response.text


async def test_merge_handles_malformed_hx_current_url(client, org_pair):
    """A malformed HX-Current-URL must not crash — fall back to defaults."""
    id_a, id_b = org_pair
    response = client.post(
        f"/admin/orgs/{id_a}/merge/{id_b}/",
        headers={**LIST_TARGET_HEADERS, "HX-Current-URL": "::not a url::"},
        follow_redirects=False,
    )
    assert response.status_code == 200


# ── Regression: duplicates-flow still returns _duplicates_region.html ───────


async def test_merge_without_list_target_returns_duplicates_region(client, org_pair):
    """HTMX merge with no HX-Target keeps the existing duplicates-flow behavior."""
    id_a, id_b = org_pair
    response = client.post(
        f"/admin/orgs/{id_a}/merge/{id_b}/",
        headers=HTMX_HEADERS,  # no HX-Target
        follow_redirects=False,
    )
    assert response.status_code == 200
    assert "candidate" in response.text or "No duplicate" in response.text


async def test_non_htmx_merge_still_redirects(client, org_pair):
    """Non-HTMX caller still gets the 303 redirect to the duplicates page."""
    id_a, id_b = org_pair
    response = client.post(
        f"/admin/orgs/{id_a}/merge/{id_b}/",
        headers=AUTH_HEADERS,
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/admin/orgs/duplicates/"


# ── A/B regression on HX-Target branching ───────────────────────────────────


@pytest.mark.parametrize(
    "extra_headers,must_contain,must_not_contain",
    [
        pytest.param(
            {"HX-Target": "orgs-list-region", "HX-Current-URL": "/admin/orgs/?q=Xyzzy"},
            'id="orgs-table"',
            'id="orgs-duplicates-region"',
            id="list-flow-returns-region-partial",
        ),
        pytest.param(
            {},  # HTMX but no HX-Target → duplicates-flow branch
            'id="orgs-duplicates-region"',
            'id="orgs-table"',
            id="duplicates-flow-returns-duplicates-region",
        ),
    ],
)
async def test_merge_branches_on_hx_target_header(
    client, org_pair, extra_headers, must_contain, must_not_contain
):
    """Same POST + fresh fixture, only HX-Target differs → distinct responses."""
    id_a, id_b = org_pair
    response = client.post(
        f"/admin/orgs/{id_a}/merge/{id_b}/",
        headers={**HTMX_HEADERS, **extra_headers},
        follow_redirects=False,
    )
    assert response.status_code == 200
    assert must_contain in response.text
    assert must_not_contain not in response.text


# ── End-to-end page / page_size preservation ────────────────────────────────


@pytest_asyncio.fixture(loop_scope="session")
async def org_batch(db_pool):
    """12 orgs sharing a unique prefix — exercises pagination without depending
    on the live row count. page_size=10 + page=2 gives 2 rows; merging two on
    page 1 leaves 11 rows, so page 2 then shows exactly 1."""
    prefix = "Pgorg"
    ids = sorted([generate_id() for _ in range(12)])

    async with db_pool.acquire() as conn:
        for i, oid in enumerate(ids):
            await conn.execute("INSERT INTO organizations (id) VALUES ($1)", oid)
            await conn.execute(
                "INSERT INTO organization_names (id, organization_id, name, is_canonical)"
                " VALUES ($1, $2, $3, TRUE)",
                generate_id(),
                oid,
                f"{prefix} Org {i:02d}",  # 00..11 — stable sort
            )

    yield prefix, ids

    async with db_pool.acquire() as conn:
        for oid in ids:
            await conn.execute(
                "DELETE FROM duplicate_dismissals WHERE entity_a_id=$1 OR entity_b_id=$1",
                oid,
            )
            await conn.execute("DELETE FROM organization_names WHERE organization_id=$1", oid)
            await conn.execute("DELETE FROM organizations WHERE id=$1", oid)


async def test_merge_preserves_page_and_page_size_from_hx_current_url(client, org_batch):
    """HX-Current-URL `?page=2&page_size=10` must shape the refreshed region:
    post-merge count of 11 means page 2 holds exactly 1 row."""
    prefix, ids = org_batch
    winner_id, loser_id = ids[0], ids[1]  # merge two on page 1

    response = client.post(
        f"/admin/orgs/{winner_id}/merge/{loser_id}/",
        headers={
            **LIST_TARGET_HEADERS,
            "HX-Current-URL": f"/admin/orgs/?q={prefix}&page=2&page_size=10",
        },
        follow_redirects=False,
    )
    assert response.status_code == 200
    assert "11 record" in response.text
    visible_count = response.text.count(EDIT_ANCHOR)
    assert visible_count == 1, (
        f"page=2 page_size=10 should yield exactly 1 row post-merge, "
        f"got {visible_count} edit anchors"
    )
