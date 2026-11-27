"""Admin Kind panel on the org detail page (#877 phase 1).

The panel shows an org's resolved kind, where it came from, and each source's
latest suggestion; an operator accepts, sets, dismisses or clears. Every action
goes through ``classification.record_decision`` — the only writer of
``organizations.org_kind_id``. Mutations answer HTMX with the panel partial and
a flash, and anything else with a 303 ``with_flash`` redirect (docs/ADMIN.md).
"""

import json

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from src.api.admin import orgs_kind
from src.api.admin.deps import get_db
from src.api.main import app
from src.core import jev_client as jc
from src.core.db import generate_id

pytestmark = [pytest.mark.integration]

AUTH = {"X-ExeDev-UserID": "usr_kind_test", "X-ExeDev-Email": "kind@test.com"}
HTMX = {**AUTH, "HX-Request": "true"}


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


async def _org(db, *, archived: bool = False) -> str:
    oid = generate_id()
    await db.execute(
        "INSERT INTO organizations (id, archived_at) VALUES ($1, CASE WHEN $2 THEN NOW() END)",
        oid,
        archived,
    )
    return oid


async def _rules(db, org_id: str, value: str) -> None:
    await db.execute(
        "INSERT INTO classification_suggestions"
        " (id, entity_type, entity_id, field, source, value, model)"
        " VALUES ($1, 'organization', $2, 'org_kind', 'rules', $3, 'rules-1')",
        generate_id(),
        org_id,
        value,
    )


async def _kind(db, org_id: str) -> str | None:
    return await db.fetchval(
        "SELECT k.slug FROM organizations o LEFT JOIN org_kinds k ON k.id = o.org_kind_id"
        " WHERE o.id = $1",
        org_id,
    )


def _flash(resp) -> dict:
    return json.loads(resp.headers["HX-Trigger"])["showFlash"]


async def test_panel_shows_undecided_and_the_rules_suggestion(client, db):
    org = await _org(db)
    await _rules(db, org, "legislature")
    r = await client.get(f"/admin/orgs/{org}/kind/", headers=HTMX)
    assert r.status_code == 200
    assert 'id="kind-panel"' in r.text
    assert "Undecided" in r.text
    assert "Legislature" in r.text
    assert 'value="accept"' in r.text or "&#34;accept&#34;" in r.text or '"accept"' in r.text
    # Every kind is offered in the Set select, with a label for it.
    assert r.text.count("<option") >= 11
    assert f'for="kind-select-{org}"' in r.text


async def test_detail_page_hosts_the_panel(client, db):
    org = await _org(db)
    await db.execute(
        "INSERT INTO organization_names (id, organization_id, name, is_canonical)"
        " VALUES ($1, $2, 'Kind Host Org', TRUE)",
        generate_id(),
        org,
    )
    r = await client.get(f"/admin/orgs/{org}/", headers=AUTH)
    assert r.status_code == 200
    assert f'hx-get="/admin/orgs/{org}/kind/"' in r.text


async def test_set_htmx_records_the_kind_and_flashes(client, db):
    org = await _org(db)
    r = await client.post(
        f"/admin/orgs/{org}/kind/", data={"action": "set", "kind": "nonprofit"}, headers=HTMX
    )
    assert r.status_code == 200
    assert await _kind(db, org) == "nonprofit"
    assert _flash(r)["level"] == "success"
    assert "Nonprofit" in r.text
    assert "Reviewed" in r.text
    created_by = await db.fetchval(
        "SELECT created_by FROM classification_suggestions WHERE entity_id=$1 AND source='manual'",
        org,
    )
    assert created_by == AUTH["X-ExeDev-UserID"]


async def test_accept_takes_the_rules_answer(client, db):
    org = await _org(db)
    await _rules(db, org, "legislature")
    r = await client.post(
        f"/admin/orgs/{org}/kind/",
        data={"action": "accept", "kind": "legislature", "source": "rules"},
        headers=HTMX,
    )
    assert r.status_code == 200
    assert await _kind(db, org) == "legislature"


async def test_stale_accept_warns_and_changes_nothing(client, db):
    org = await _org(db)
    await _rules(db, org, "legislature")
    r = await client.post(
        f"/admin/orgs/{org}/kind/",
        data={"action": "accept", "kind": "business", "source": "rules"},
        headers=HTMX,
    )
    assert r.status_code == 200
    assert _flash(r)["level"] == "warning"
    assert await _kind(db, org) is None


async def test_dismiss_then_clear(client, db):
    org = await _org(db)
    await client.post(
        f"/admin/orgs/{org}/kind/", data={"action": "set", "kind": "business"}, headers=HTMX
    )
    r = await client.post(f"/admin/orgs/{org}/kind/", data={"action": "dismiss"}, headers=HTMX)
    assert await _kind(db, org) is None
    assert "Dismissed" in r.text
    r = await client.post(f"/admin/orgs/{org}/kind/", data={"action": "clear"}, headers=HTMX)
    assert "Undecided" in r.text


async def test_non_htmx_redirects_with_flash(client, db):
    org = await _org(db)
    ok = await client.post(
        f"/admin/orgs/{org}/kind/", data={"action": "set", "kind": "tribe"}, headers=AUTH
    )
    assert ok.status_code == 303
    assert ok.headers["location"] == f"/admin/orgs/{org}/?flash=saved"
    bad = await client.post(
        f"/admin/orgs/{org}/kind/", data={"action": "set", "kind": "guild"}, headers=AUTH
    )
    assert bad.status_code == 303
    assert bad.headers["location"] == f"/admin/orgs/{org}/?flash=invalid"


async def test_archived_org_panel_is_read_only(client, db):
    org = await _org(db, archived=True)
    r = await client.get(f"/admin/orgs/{org}/kind/", headers=HTMX)
    assert r.status_code == 200
    assert "hx-post" not in r.text


async def test_unknown_org_is_404(client):
    r = await client.get(f"/admin/orgs/{generate_id()}/kind/", headers=HTMX)
    assert r.status_code == 404
    r = await client.post(
        f"/admin/orgs/{generate_id()}/kind/", data={"action": "clear"}, headers=HTMX
    )
    assert r.status_code == 404


async def test_requires_admin_auth(client, db):
    org = await _org(db)
    r = await client.get(f"/admin/orgs/{org}/kind/")
    assert r.status_code in (303, 307)


async def test_undecided_select_starts_on_a_placeholder(client, db):
    """No kind is preselected for an undecided org, so Set can't record one by accident."""
    org = await _org(db)
    r = await client.get(f"/admin/orgs/{org}/kind/", headers=HTMX)
    assert '<option value="" selected>Choose a kind…</option>' in r.text


async def test_set_without_a_kind_warns_and_changes_nothing(client, db):
    org = await _org(db)
    r = await client.post(
        f"/admin/orgs/{org}/kind/", data={"action": "set", "kind": ""}, headers=HTMX
    )
    assert _flash(r)["level"] == "warning"
    assert await _kind(db, org) is None


# --- phase 2: Jev answers, provenance, Re-ask ------------------------------


async def _jev(db, org_id: str, value: str, confidence: float) -> None:
    await db.execute(
        "INSERT INTO classification_suggestions"
        " (id, entity_type, entity_id, field, source, value, confidence, model, input_tokens)"
        " VALUES ($1, 'organization', $2, 'org_kind', 'jev', $3, $4, 'jev-1.14.0', 700)",
        generate_id(),
        org_id,
        value,
        confidence,
    )


@pytest.fixture
def jev_on(monkeypatch):
    monkeypatch.setenv("JEV_SUGGESTIONS_ENABLED", "1")
    monkeypatch.setenv("TYPESAFE_API_KEY", "sk-test")
    monkeypatch.delenv("JEV_MODEL", raising=False)


@pytest.fixture
def jev_off(monkeypatch):
    monkeypatch.setenv("JEV_SUGGESTIONS_ENABLED", "0")


def _fake_reask(monkeypatch, result):
    calls = []

    async def ask(org, crit, **kw):
        calls.append(org)
        if isinstance(result, Exception):
            raise result
        return jc.JevAnswer(result, 0.83, {result: 0.83}, kw["model"], 650)

    monkeypatch.setattr(orgs_kind, "_reask_ask", ask)
    return calls


async def test_panel_shows_the_ai_answer_with_its_confidence(client, db, jev_off):
    org = await _org(db)
    await _jev(db, org, "coalition", 0.91)
    r = await client.get(f"/admin/orgs/{org}/kind/", headers=HTMX)
    assert "AI" in r.text
    assert "91%" in r.text
    assert "Accept Coalition" in r.text


async def test_an_accepted_ai_answer_says_so(client, db, jev_off):
    org = await _org(db)
    await _jev(db, org, "coalition", 0.91)
    r = await client.post(
        f"/admin/orgs/{org}/kind/",
        data={"action": "accept", "kind": "coalition", "source": "jev"},
        headers=HTMX,
    )
    assert "Reviewed" in r.text
    assert "accepted from AI" in r.text


async def test_reask_is_disabled_with_its_reason_when_the_flag_is_off(client, db, jev_off):
    org = await _org(db)
    r = await client.get(f"/admin/orgs/{org}/kind/", headers=HTMX)
    assert "Re-ask AI" in r.text
    assert "disabled" in r.text
    assert "AI suggestions are off" in r.text


async def test_reask_with_the_flag_off_warns_and_asks_nothing(client, db, jev_off, monkeypatch):
    org = await _org(db)
    calls = _fake_reask(monkeypatch, "business")
    r = await client.post(f"/admin/orgs/{org}/kind/reask/", headers=HTMX)
    assert r.status_code == 200
    assert _flash(r)["level"] == "warning"
    assert calls == []


async def test_reask_stores_a_new_answer_and_never_sets_the_kind(client, db, jev_on, monkeypatch):
    org = await _org(db)
    await _jev(db, org, "business", 0.6)
    calls = _fake_reask(monkeypatch, "association")
    r = await client.post(f"/admin/orgs/{org}/kind/reask/", headers=HTMX)
    assert r.status_code == 200
    assert len(calls) == 1
    assert _flash(r)["level"] == "success"
    assert "Association" in _flash(r)["body"]
    assert "83%" in r.text
    assert await _kind(db, org) is None
    live = await db.fetch(
        "SELECT value FROM classification_suggestions"
        " WHERE entity_id=$1 AND source='jev' AND ignored_at IS NULL",
        org,
    )
    assert [x["value"] for x in live] == ["association"]


async def test_reask_failure_warns_with_the_escaped_error(client, db, jev_on, monkeypatch):
    org = await _org(db)
    _fake_reask(monkeypatch, jc.JevError("Jev: HTTP 400: <bad>", status=400))
    r = await client.post(f"/admin/orgs/{org}/kind/reask/", headers=HTMX)
    assert _flash(r)["level"] == "warning"
    assert "&lt;bad&gt;" in _flash(r)["body"]


async def test_reask_on_an_archived_org_warns(client, db, jev_on, monkeypatch):
    org = await _org(db, archived=True)
    calls = _fake_reask(monkeypatch, "business")
    r = await client.post(f"/admin/orgs/{org}/kind/reask/", headers=HTMX)
    assert _flash(r)["level"] == "warning"
    assert calls == []


async def test_reask_non_htmx_redirects_with_flash(client, db, jev_on, monkeypatch):
    org = await _org(db)
    _fake_reask(monkeypatch, "business")
    r = await client.post(f"/admin/orgs/{org}/kind/reask/", headers=AUTH)
    assert r.status_code == 303
    assert r.headers["location"] == f"/admin/orgs/{org}/?flash=saved"


async def test_reask_unknown_org_is_404(client, jev_on):
    r = await client.post("/admin/orgs/nope/kind/reask/", headers=HTMX)
    assert r.status_code == 404
