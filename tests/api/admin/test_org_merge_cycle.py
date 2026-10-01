"""Org merge into its own descendant is refused, never a 500 (#523).

`_execute_merge` re-points the loser's children onto the winner with
``UPDATE organizations SET parent_id=$winner WHERE parent_id=$loser``. When the
winner sits anywhere below the loser, that statement closes a loop and
``trg_no_org_cycle`` raises. The merge must refuse with a warning instead — and
the preview must say so before the curator clicks, offering the reverse merge.
"""

import json

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from src.api.admin import orgs_merge
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


async def _mk_org(db, name, parent_id=None):
    oid = generate_id()
    await db.execute("INSERT INTO organizations (id, parent_id) VALUES ($1, $2)", oid, parent_id)
    await db.execute(
        "INSERT INTO organization_names (id, organization_id, name, is_canonical)"
        " VALUES ($1, $2, $3, TRUE)",
        generate_id(),
        oid,
        name,
    )
    return oid


async def _parent_of(db, org_id):
    return await db.fetchval("SELECT parent_id FROM organizations WHERE id=$1", org_id)


async def _exists(db, org_id):
    return bool(await db.fetchval("SELECT 1 FROM organizations WHERE id=$1", org_id))


def _flash(resp):
    return json.loads(resp.headers["HX-Trigger"])["showFlash"]


@pytest_asyncio.fixture(loop_scope="session")
async def tree(db):
    """Parent P with children C and S; C has child G (P → C → G, P → S)."""
    p = await _mk_org(db, "Cycle Parent Committee")
    c = await _mk_org(db, "Cycle Child Committee", parent_id=p)
    s = await _mk_org(db, "Cycle Sibling Committee", parent_id=p)
    g = await _mk_org(db, "Cycle Grandchild Committee", parent_id=c)
    return {"p": p, "c": c, "s": s, "g": g}


async def _assert_tree_untouched(db, t):
    for key in ("p", "c", "s", "g"):
        assert await _exists(db, t[key]), key
    assert await _parent_of(db, t["p"]) is None
    assert await _parent_of(db, t["c"]) == t["p"]
    assert await _parent_of(db, t["s"]) == t["p"]
    assert await _parent_of(db, t["g"]) == t["c"]


# ── merge routes refuse ────────────────────────────────────────────────────────


@pytest.mark.parametrize("survivor", ["c", "g"], ids=["child", "grandchild"])
async def test_merge_with_into_descendant_refuses_with_warning(client, db, tree, survivor):
    r = await client.post(
        f"/admin/orgs/{tree[survivor]}/merge-with/{tree['p']}/", headers=HTMX_HEADERS
    )
    assert r.status_code == 200
    assert r.headers.get("HX-Reswap") == "none"
    assert "HX-Redirect" not in r.headers
    flash = _flash(r)
    assert flash["level"] == "warning"
    assert "Not merged" in flash["body"]
    assert "Cycle Parent Committee" in flash["body"]
    await _assert_tree_untouched(db, tree)


async def test_merge_with_list_context_refusal_does_not_swap_region(client, db, tree):
    r = await client.post(
        f"/admin/orgs/{tree['c']}/merge-with/{tree['p']}/",
        data={"return_to": "list"},
        headers={**HTMX_HEADERS, "HX-Target": "orgs-list-region"},
    )
    assert r.status_code == 200
    assert r.headers.get("HX-Reswap") == "none"
    assert _flash(r)["level"] == "warning"
    await _assert_tree_untouched(db, tree)


async def test_merge_with_into_descendant_nonhtmx_redirects_with_invalid(client, db, tree):
    r = await client.post(f"/admin/orgs/{tree['c']}/merge-with/{tree['p']}/", headers=AUTH_HEADERS)
    assert r.status_code == 303
    assert r.headers["location"] == f"/admin/orgs/{tree['c']}/?flash=invalid"
    await _assert_tree_untouched(db, tree)


async def test_bulk_merge_into_descendant_refuses_with_warning(client, db, tree):
    r = await client.post(f"/admin/orgs/{tree['c']}/merge/{tree['p']}/", headers=HTMX_HEADERS)
    assert r.status_code == 200
    assert r.headers.get("HX-Reswap") == "none"
    assert _flash(r)["level"] == "warning"
    await _assert_tree_untouched(db, tree)


async def test_bulk_merge_into_descendant_nonhtmx_redirects_with_invalid(client, db, tree):
    r = await client.post(f"/admin/orgs/{tree['c']}/merge/{tree['p']}/", headers=AUTH_HEADERS)
    assert r.status_code == 303
    assert r.headers["location"] == "/admin/orgs/duplicates/?flash=invalid"
    await _assert_tree_untouched(db, tree)


async def test_trigger_backstop_maps_to_refusal(client, db, tree, monkeypatch):
    """A reparent racing the pre-check still reaches the trigger — and still refuses."""

    async def _never(*_args, **_kwargs):
        return False

    monkeypatch.setattr(orgs_merge, "_descends_from", _never)
    r = await client.post(f"/admin/orgs/{tree['c']}/merge-with/{tree['p']}/", headers=HTMX_HEADERS)
    assert r.status_code == 200
    assert _flash(r)["level"] == "warning"
    await _assert_tree_untouched(db, tree)


async def test_reverse_merge_keeps_ancestor_and_adopts_children(client, db, tree):
    """Keeping the parent is the offered alternative — it must still work."""
    r = await client.post(f"/admin/orgs/{tree['p']}/merge-with/{tree['c']}/", headers=HTMX_HEADERS)
    assert r.status_code == 200
    assert _flash(r)["level"] == "success"
    assert not await _exists(db, tree["c"])
    assert await _parent_of(db, tree["g"]) == tree["p"]
    assert await _parent_of(db, tree["s"]) == tree["p"]


async def test_unrelated_orgs_still_merge(client, db, tree):
    other = await _mk_org(db, "Cycle Unrelated Committee")
    r = await client.post(f"/admin/orgs/{tree['c']}/merge-with/{other}/", headers=HTMX_HEADERS)
    assert r.status_code == 200
    assert _flash(r)["level"] == "success"
    assert not await _exists(db, other)


# ── preview says so before the click ───────────────────────────────────────────

# The marker on the alert element; the script's `[data-merge-blocked]` selector
# appears on every preview, so a bare substring match would always hit.
BLOCKED_ALERT = 'role="alert" data-merge-blocked'


async def _preview(client, winner, loser):
    return await client.get(
        f"/admin/orgs/{winner}/merge-preview/{loser}/?winner={winner}", headers=AUTH_HEADERS
    )


@pytest.mark.parametrize("survivor", ["c", "g"], ids=["child", "grandchild"])
async def test_preview_blocks_merge_into_descendant(client, tree, survivor):
    r = await _preview(client, tree[survivor], tree["p"])
    assert r.status_code == 200
    text = " ".join(r.text.split())
    assert BLOCKED_ALERT in text
    assert "sub-organization of" in text
    # Execute ships disabled; the reverse merge is offered from inside the alert.
    assert 'id="merge-execute-btn" disabled' in text
    assert "Keep Cycle Parent Committee instead" in text


async def test_preview_reverse_direction_is_not_blocked(client, tree):
    r = await _preview(client, tree["p"], tree["c"])
    assert r.status_code == 200
    text = " ".join(r.text.split())
    assert BLOCKED_ALERT not in text
    assert "sub-organization of" not in text
    assert 'id="merge-execute-btn" disabled' not in text


async def test_preview_script_honours_blocked_marker(client, tree):
    """syncExecute runs on load; it must not re-enable a blocked Execute."""
    r = await _preview(client, tree["c"], tree["p"])
    assert "[data-merge-blocked]" in r.text
