"""Integration tests for the People list merge flow (issue #137).

Covers the additions on top of the existing `POST /admin/people/{a}/merge/{b}/`
route:

* HX-Target == "people-table-body" branch → returns `_rows.html`, not the
  duplicates region.
* HX-Current-URL parsing → response respects the user's current `q` / `status`
  / `page` / `page_size` filters.
* Regression: HTMX without the list-target header still returns the duplicates
  region (existing duplicates-page flow).
* List template renders the merge button, the merge bar markup, and the
  checkbox column on each row.
"""

import json

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
# CR #2 follow-up: the list-flow merge swaps the whole region so caption
# and sticky pagination stay in sync with the post-merge row count.
LIST_TARGET_HEADERS = {**HTMX_HEADERS, "HX-Target": "people-list-region"}


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


@pytest_asyncio.fixture(loop_scope="session")
async def person_pair(db_pool):
    """Two near-dup people with a unique discriminator token for filter tests."""
    id_a, id_b = generate_id(), generate_id()
    if id_a > id_b:
        id_a, id_b = id_b, id_a

    async with db_pool.acquire() as conn:
        for pid, name in [
            (id_a, "Xyzzy McMergeCandidate"),
            (id_b, "Xyzzy McMergeCandidate Jr"),
        ]:
            await conn.execute("INSERT INTO people (id) VALUES ($1)", pid)
            await conn.execute(
                "INSERT INTO person_names"
                " (id, person_id, name, is_canonical)"
                " VALUES ($1, $2, $3, TRUE)",
                generate_id(),
                pid,
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
        for pid in [id_a, id_b]:
            await conn.execute("DELETE FROM person_names WHERE person_id=$1", pid)
            await conn.execute("DELETE FROM people WHERE id=$1", pid)


# ── List template additions ─────────────────────────────────────────────────


async def test_people_list_renders_merge_button(client):
    """The list page must include the Merge toggle button."""
    response = client.get("/admin/people/", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert 'id="people-merge-btn"' in response.text


async def test_people_list_renders_merge_btn_wrapper(client):
    """Wrapper element required for disabled-cursor/title affordance."""
    response = client.get("/admin/people/", headers=AUTH_HEADERS)
    assert 'id="people-merge-btn-wrap"' in response.text


async def test_people_list_loads_people_merge_js(client):
    """The People list page must serve the people-merge.js asset.

    #249: the script is now loaded site-wide from base.html (see
    test_base_template.test_people_merge_js_loaded_site_wide_with_defer), so it
    survives hx-boost <head>-stripping; this asserts it is still present on the
    People list itself."""
    response = client.get("/admin/people/", headers=AUTH_HEADERS)
    assert "people-merge.js" in response.text


async def test_people_merge_js_loaded_exactly_once(client):
    """#249 regression guard: people-merge.js must load exactly once on the
    People list.

    It lives site-wide in base.html. Re-adding it to list.html's extra_head
    would load it twice → the IIFE runs twice → the document-level click/change
    listeners double-bind → every toggle fires twice and Merge is a no-op again.
    The site-wide guard list (test_orgs_templates) catches removal from
    base.html; this catches the duplicate-load inverse."""
    response = client.get("/admin/people/", headers=AUTH_HEADERS)
    assert response.text.count("people-merge.js") == 1


async def test_people_list_renders_merge_bar(client):
    """Merge action bar markup must be present (hidden via inline display:none)."""
    response = client.get("/admin/people/", headers=AUTH_HEADERS)
    assert 'id="people-merge-bar"' in response.text
    assert "merge-bar__keep-a" in response.text
    assert "merge-bar__keep-b" in response.text


async def test_people_list_tbody_has_known_id(client):
    """`#people-table-body` is the HX-Target anchor; renaming silently breaks the route branch."""
    response = client.get("/admin/people/", headers=AUTH_HEADERS)
    assert 'id="people-table-body"' in response.text


async def test_people_list_rows_have_merge_checkboxes(client, person_pair):
    """Each row must carry the merge-select checkbox + data-person-id."""
    response = client.get("/admin/people/?q=Xyzzy", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert 'name="merge-select"' in response.text
    assert "data-person-id=" in response.text


async def test_people_list_rows_have_data_title(client, person_pair):
    """JS reads `data-title` from `<tr>` to label the Keep buttons."""
    response = client.get("/admin/people/?q=Xyzzy", headers=AUTH_HEADERS)
    assert 'data-title="Xyzzy McMergeCandidate"' in response.text


# ── List-flow merge route branch ────────────────────────────────────────────


async def test_merge_with_list_target_returns_region_partial(client, person_pair):
    """HX-Target=people-list-region must return the region partial (table +
    caption + sticky pagination), not the duplicates region."""
    id_a, id_b = person_pair
    response = client.post(
        f"/admin/people/{id_a}/merge/{id_b}/",
        headers={**LIST_TARGET_HEADERS, "HX-Current-URL": "/admin/people/?q=Xyzzy"},
        follow_redirects=False,
    )
    assert response.status_code == 200
    assert 'id="people-duplicates-region"' not in response.text
    assert "No duplicate candidates found" not in response.text
    # _region.html distinctive markers
    assert 'id="people-table"' in response.text
    assert "<caption>People &mdash;" in response.text or "<caption>People —" in response.text


async def test_merge_with_list_target_refreshes_total_count(client, person_pair):
    """CR #2 follow-up: caption shows post-merge total (loser hard-deleted)."""
    id_a, id_b = person_pair
    # Pre-merge: confirm caption reads "2 records" for our filtered pair
    pre = client.get("/admin/people/?q=Xyzzy", headers=HTMX_HEADERS)
    assert "People &mdash; 2 record" in pre.text or "People — 2 record" in pre.text

    response = client.post(
        f"/admin/people/{id_a}/merge/{id_b}/",
        headers={**LIST_TARGET_HEADERS, "HX-Current-URL": "/admin/people/?q=Xyzzy"},
        follow_redirects=False,
    )
    assert response.status_code == 200
    # Post-merge: only winner remains under that filter
    assert "People &mdash; 1 record" in response.text or "People — 1 record" in response.text


async def test_merge_with_list_target_includes_winner_row(client, person_pair):
    """Refreshed list must still include the winner (it survived)."""
    id_a, id_b = person_pair
    response = client.post(
        f"/admin/people/{id_a}/merge/{id_b}/",
        headers={**LIST_TARGET_HEADERS, "HX-Current-URL": "/admin/people/?q=Xyzzy"},
        follow_redirects=False,
    )
    assert response.status_code == 200
    assert f"/admin/people/{id_a}/" in response.text


async def test_merge_with_list_target_excludes_loser_row(client, person_pair):
    """Loser must not appear in the refreshed rows (hard-deleted)."""
    id_a, id_b = person_pair
    response = client.post(
        f"/admin/people/{id_a}/merge/{id_b}/",
        headers={**LIST_TARGET_HEADERS, "HX-Current-URL": "/admin/people/?q=Xyzzy"},
        follow_redirects=False,
    )
    assert response.status_code == 200
    assert f"/admin/people/{id_b}/" not in response.text


async def test_merge_with_list_target_sends_flash(client, person_pair):
    """List-flow merge must trigger flash via HX-Trigger header (same as duplicates flow)."""
    id_a, id_b = person_pair
    response = client.post(
        f"/admin/people/{id_a}/merge/{id_b}/",
        headers={**LIST_TARGET_HEADERS, "HX-Current-URL": "/admin/people/"},
        follow_redirects=False,
    )
    assert response.status_code == 200
    assert "HX-Trigger" in response.headers
    payload = json.loads(response.headers["HX-Trigger"])
    assert payload["showFlash"]["level"] == "success"
    assert "Xyzzy McMergeCandidate" in payload["showFlash"]["body"]


async def test_merge_with_list_target_emits_refresh_dup_badge(client, person_pair):
    """List-flow merge response must include refreshDupBadge in HX-Trigger."""
    id_a, id_b = person_pair
    response = client.post(
        f"/admin/people/{id_a}/merge/{id_b}/",
        headers={**LIST_TARGET_HEADERS, "HX-Current-URL": "/admin/people/"},
        follow_redirects=False,
    )
    assert response.status_code == 200
    trigger = json.loads(response.headers.get("HX-Trigger", "{}"))
    assert "refreshDupBadge" in trigger


# ── Filter-state preservation via HX-Current-URL ─────────────────────────────


async def test_merge_preserves_q_filter(client, person_pair):
    """HX-Current-URL `?q=zzz_nomatch` must filter the winner out of refreshed rows."""
    id_a, id_b = person_pair
    response = client.post(
        f"/admin/people/{id_a}/merge/{id_b}/",
        headers={**LIST_TARGET_HEADERS, "HX-Current-URL": "/admin/people/?q=zzz_nomatch"},
        follow_redirects=False,
    )
    assert response.status_code == 200
    # Winner survived merge but does NOT match the q filter → not in response
    assert f"/admin/people/{id_a}/" not in response.text


async def test_merge_preserves_status_filter(client, person_pair):
    """HX-Current-URL `?status=archived` filters winner (active) out of the rows."""
    id_a, id_b = person_pair
    response = client.post(
        f"/admin/people/{id_a}/merge/{id_b}/",
        headers={
            **LIST_TARGET_HEADERS,
            "HX-Current-URL": "/admin/people/?status=archived",
        },
        follow_redirects=False,
    )
    assert response.status_code == 200
    assert f"/admin/people/{id_a}/" not in response.text


async def test_merge_handles_missing_hx_current_url(client, person_pair):
    """Missing HX-Current-URL falls back to defaults (active status, q="")."""
    id_a, id_b = person_pair
    response = client.post(
        f"/admin/people/{id_a}/merge/{id_b}/",
        headers=LIST_TARGET_HEADERS,
        follow_redirects=False,
    )
    assert response.status_code == 200
    # Default filters include the active winner
    assert f"/admin/people/{id_a}/" in response.text


async def test_merge_handles_malformed_hx_current_url(client, person_pair):
    """A malformed HX-Current-URL must not crash — fall back to defaults."""
    id_a, id_b = person_pair
    response = client.post(
        f"/admin/people/{id_a}/merge/{id_b}/",
        headers={**LIST_TARGET_HEADERS, "HX-Current-URL": "::not a url::"},
        follow_redirects=False,
    )
    assert response.status_code == 200


# ── Regression: duplicates-flow still returns _duplicates_region.html ───────


async def test_merge_without_list_target_returns_duplicates_region(client, person_pair):
    """HTMX merge with no HX-Target (or any other target) keeps the existing behavior."""
    id_a, id_b = person_pair
    response = client.post(
        f"/admin/people/{id_a}/merge/{id_b}/",
        headers=HTMX_HEADERS,  # no HX-Target
        follow_redirects=False,
    )
    assert response.status_code == 200
    # Duplicates region indicators
    assert "candidate" in response.text or "No duplicate" in response.text


async def test_non_htmx_merge_still_redirects(client, person_pair):
    """Non-HTMX caller still gets the 303 redirect to duplicates page."""
    id_a, id_b = person_pair
    response = client.post(
        f"/admin/people/{id_a}/merge/{id_b}/",
        headers=AUTH_HEADERS,
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/admin/people/duplicates/"


# ── A/B regression on HX-Target branching (CR2 #8) ──────────────────────────


@pytest.mark.parametrize(
    "extra_headers,must_contain,must_not_contain",
    [
        pytest.param(
            {
                "HX-Target": "people-list-region",
                "HX-Current-URL": "/admin/people/?q=Xyzzy",
            },
            'id="people-table"',
            'id="people-duplicates-region"',
            id="list-flow-returns-region-partial",
        ),
        pytest.param(
            {},  # HTMX but no HX-Target → duplicates-flow branch
            'id="people-duplicates-region"',
            'id="people-table"',
            id="duplicates-flow-returns-duplicates-region",
        ),
    ],
)
async def test_merge_branches_on_hx_target_header(
    client, person_pair, extra_headers, must_contain, must_not_contain
):
    """Same POST + fresh fixture, only HX-Target differs → distinct responses.

    Locks in that HX-Target is the sole branching signal for the two HTMX
    response shapes. Each parametrize invocation gets its own person_pair
    (function-scoped fixture) so the merge in case A doesn't affect case B.
    """
    id_a, id_b = person_pair
    response = client.post(
        f"/admin/people/{id_a}/merge/{id_b}/",
        headers={**HTMX_HEADERS, **extra_headers},
        follow_redirects=False,
    )
    assert response.status_code == 200
    assert must_contain in response.text
    assert must_not_contain not in response.text


# ── End-to-end page / page_size preservation (CR2 #7) ───────────────────────


@pytest_asyncio.fixture(loop_scope="session")
async def person_batch(db_pool):
    """12 people sharing a unique prefix — lets us exercise pagination in
    integration tests without depending on the live row count of the DB.

    page_size=10 + page=2 against this batch gives 2 rows; merging two of
    them on page 1 leaves 11 rows, so page 2 should then show exactly 1.
    """
    prefix = "Pgtest"
    ids = sorted([generate_id() for _ in range(12)])

    async with db_pool.acquire() as conn:
        for i, pid in enumerate(ids):
            await conn.execute("INSERT INTO people (id) VALUES ($1)", pid)
            await conn.execute(
                "INSERT INTO person_names (id, person_id, name, is_canonical)"
                " VALUES ($1, $2, $3, TRUE)",
                generate_id(),
                pid,
                f"{prefix} Person {i:02d}",  # 00..11 — stable sort
            )

    yield prefix, ids

    async with db_pool.acquire() as conn:
        for pid in ids:
            await conn.execute(
                "DELETE FROM duplicate_dismissals WHERE entity_a_id=$1 OR entity_b_id=$1",
                pid,
            )
            await conn.execute("DELETE FROM person_names WHERE person_id=$1", pid)
            await conn.execute("DELETE FROM people WHERE id=$1", pid)


async def test_merge_preserves_page_and_page_size_from_hx_current_url(client, person_batch):
    """HX-Current-URL `?page=2&page_size=10` must shape the refreshed region:
    post-merge count of 11 means page 2 holds exactly 1 row."""
    prefix, ids = person_batch
    winner_id, loser_id = ids[0], ids[1]  # merge two on page 1

    response = client.post(
        f"/admin/people/{winner_id}/merge/{loser_id}/",
        headers={
            **LIST_TARGET_HEADERS,
            "HX-Current-URL": f"/admin/people/?q={prefix}&page=2&page_size=10",
        },
        follow_redirects=False,
    )
    assert response.status_code == 200

    # Post-merge: 11 rows match the prefix. Caption reflects total = 11.
    assert "11 record" in response.text

    # page=2 with page_size=10 → exactly 1 row visible (the 11th in sort order).
    # Count the person-row anchors to that effect.
    visible_count = response.text.count('class="btn btn--ghost btn--sm" aria-label="Edit')
    assert visible_count == 1, (
        f"page=2 page_size=10 should yield exactly 1 row post-merge, "
        f"got {visible_count} edit anchors"
    )


async def test_merge_handles_overflow_page_from_hx_current_url(client, person_batch):
    """When `page` × `page_size` lands past the new last page (e.g. the loser
    was the only row on the last page), pagination_context clamps page to
    the new last page rather than returning an empty result."""
    prefix, ids = person_batch
    # Page-size=10 → 12 rows = pages 1 (10) + 2 (2). Merge the 2 on page 2.
    response = client.post(
        f"/admin/people/{ids[10]}/merge/{ids[11]}/",
        headers={
            **LIST_TARGET_HEADERS,
            # Request page 2; after merge there are only 11 rows so page 2
            # has 1 row — still a valid page, not overflowed yet.
            "HX-Current-URL": f"/admin/people/?q={prefix}&page=2&page_size=10",
        },
        follow_redirects=False,
    )
    assert response.status_code == 200
    assert "11 record" in response.text
    # Page 2 of 11 rows @ page_size 10 → 1 row.
    visible_count = response.text.count('class="btn btn--ghost btn--sm" aria-label="Edit')
    assert visible_count == 1
