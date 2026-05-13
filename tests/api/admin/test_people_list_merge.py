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
    pytest.mark.asyncio(loop_scope="session"),
]

AUTH_HEADERS = {
    "X-ExeDev-UserID": "usr_test",
    "X-ExeDev-Email": "admin@test.com",
}
HTMX_HEADERS = {**AUTH_HEADERS, "HX-Request": "true"}
LIST_TARGET_HEADERS = {**HTMX_HEADERS, "HX-Target": "people-table-body"}


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
    """list.html must reference the people-merge.js asset so the script runs."""
    response = client.get("/admin/people/", headers=AUTH_HEADERS)
    assert "people-merge.js" in response.text


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
    assert 'data-person-id=' in response.text


async def test_people_list_rows_have_data_title(client, person_pair):
    """JS reads `data-title` from `<tr>` to label the Keep buttons."""
    response = client.get("/admin/people/?q=Xyzzy", headers=AUTH_HEADERS)
    assert 'data-title="Xyzzy McMergeCandidate"' in response.text


# ── List-flow merge route branch ────────────────────────────────────────────


async def test_merge_with_list_target_returns_rows_partial(client, person_pair):
    """HX-Target=people-table-body must return the rows partial, not duplicates region."""
    id_a, id_b = person_pair
    response = client.post(
        f"/admin/people/{id_a}/merge/{id_b}/",
        headers={**LIST_TARGET_HEADERS, "HX-Current-URL": "/admin/people/?q=Xyzzy"},
        follow_redirects=False,
    )
    assert response.status_code == 200
    # Rows partial: <tr> elements only; no duplicates-region wrapper
    assert 'id="people-duplicates-region"' not in response.text
    assert "No duplicate candidates found" not in response.text
    assert "<tr" in response.text


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
