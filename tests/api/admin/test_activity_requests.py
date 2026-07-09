"""Integration tests for the admin API request-log screens (#260, steps 6-7)."""

import hashlib
import os

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from src.api.admin.deps import get_db
from src.api.main import app
from src.core.db import generate_id

pytestmark = [pytest.mark.integration]

AUTH_HEADERS = {"X-ExeDev-UserID": "usr_test", "X-ExeDev-Email": "admin@test.com"}
_BASE = "/admin/activity/requests/"


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


async def _insert_log(conn, **cols):
    """Insert one api_request_log row; return its id. Sensible defaults per column."""
    row = {
        "api_key_id": None,
        "method": "POST",
        "path": "/api/v1/people/observations",
        "route_group": "observations",
        "entity_type": None,
        "status_code": 200,
        "latency_ms": 5,
        "disposition": None,
        "result_entity_id": None,
        "reason": None,
        "item_count": None,
        "is_empty": False,
        "request_body": None,
        "response_body": None,
    }
    row.update(cols)
    return await conn.fetchval(
        """
        INSERT INTO api_request_log
            (api_key_id, method, path, route_group, entity_type, status_code,
             latency_ms, disposition, result_entity_id, reason, item_count,
             is_empty, request_body, response_body)
        VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13::jsonb,$14::jsonb)
        RETURNING id
        """,
        row["api_key_id"],
        row["method"],
        row["path"],
        row["route_group"],
        row["entity_type"],
        row["status_code"],
        row["latency_ms"],
        row["disposition"],
        row["result_entity_id"],
        row["reason"],
        row["item_count"],
        row["is_empty"],
        row["request_body"],
        row["response_body"],
    )


@pytest_asyncio.fixture(loop_scope="session")
async def seeded_log(db):
    """Seed a key, a linkable person, and a spread of log rows. Yields ids dict."""
    uid, kid, pid = generate_id(), generate_id(), generate_id()
    raw = "pm_" + os.urandom(8).hex()
    await db.execute("INSERT INTO app_users (id, email) VALUES ($1,$2)", uid, "arl_ui@test.com")
    await db.execute(
        "INSERT INTO api_keys (id, user_id, label, key_prefix, key_hash) VALUES ($1,$2,$3,$4,$5)",
        kid,
        uid,
        "UI Test Key",
        raw[:8],
        hashlib.sha256(raw.encode()).hexdigest(),
    )
    await db.execute("INSERT INTO people (id) VALUES ($1)", pid)
    ids = {"key": kid, "person": pid}
    ids["new"] = await _insert_log(
        db,
        api_key_id=kid,
        disposition="new",
        entity_type="person",
        result_entity_id=pid,
        request_body='{"identifier_type":"x"}',
        response_body='{"disposition":"new"}',
    )
    ids["rejected"] = await _insert_log(
        db,
        api_key_id=kid,
        disposition="rejected",
        status_code=200,
        reason="unknown_identifier_type",
    )
    ids["removed"] = await _insert_log(
        db,
        api_key_id=kid,
        disposition="new",
        entity_type="person",
        result_entity_id=generate_id(),  # not in people -> "(removed)"
    )
    ids["empty_poll"] = await _insert_log(
        db,
        api_key_id=kid,
        method="GET",
        path="/api/v1/changes",
        route_group="changes",
        item_count=0,
        is_empty=True,
    )
    ids["other"] = await _insert_log(
        db,
        api_key_id=kid,
        method="GET",
        path="/api/v1/link-types",
        route_group="other",
    )
    return ids


# ---------------------------------------------------------------------------
# List screen
# ---------------------------------------------------------------------------


async def test_list_returns_200(client, seeded_log):
    resp = await client.get(_BASE, headers=AUTH_HEADERS)
    assert resp.status_code == 200
    assert "Requests" in resp.text


async def test_list_redirects_unauthenticated(client):
    resp = await client.get(_BASE, follow_redirects=False)
    assert resp.status_code in (302, 307)
    assert "/__exe.dev/login" in resp.headers["location"]


async def test_list_default_hides_other_group(client, seeded_log):
    """Default view is observations + changes; 'other' rows excluded."""
    resp = await client.get(_BASE, headers=AUTH_HEADERS)
    assert f"/admin/activity/requests/{seeded_log['new']}/" in resp.text
    assert f"/admin/activity/requests/{seeded_log['other']}/" not in resp.text


async def test_list_all_group_shows_other(client, seeded_log):
    resp = await client.get(_BASE + "?group=all", headers=AUTH_HEADERS)
    assert f"/admin/activity/requests/{seeded_log['other']}/" in resp.text


async def test_list_default_hides_empty_polls(client, seeded_log):
    resp = await client.get(_BASE, headers=AUTH_HEADERS)
    assert f"/admin/activity/requests/{seeded_log['empty_poll']}/" not in resp.text


async def test_list_show_empty_includes_empty_polls(client, seeded_log):
    resp = await client.get(_BASE + "?show_empty=true", headers=AUTH_HEADERS)
    assert f"/admin/activity/requests/{seeded_log['empty_poll']}/" in resp.text


async def test_list_filter_by_disposition_rejected(client, seeded_log):
    resp = await client.get(_BASE + "?disposition=rejected", headers=AUTH_HEADERS)
    assert f"/admin/activity/requests/{seeded_log['rejected']}/" in resp.text
    assert f"/admin/activity/requests/{seeded_log['new']}/" not in resp.text


async def test_list_shows_key_label_not_hash(client, seeded_log):
    resp = await client.get(_BASE, headers=AUTH_HEADERS)
    assert "UI Test Key" in resp.text


async def test_list_stats_strip_present(client, seeded_log):
    resp = await client.get(_BASE, headers=AUTH_HEADERS)
    # Stats strip surfaces rejection + error signals.
    assert "Rejected" in resp.text


# ---------------------------------------------------------------------------
# Detail screen
# ---------------------------------------------------------------------------


async def test_detail_returns_200_with_bodies(client, seeded_log):
    resp = await client.get(f"{_BASE}{seeded_log['new']}/", headers=AUTH_HEADERS)
    assert resp.status_code == 200
    assert "identifier_type" in resp.text  # request body rendered
    assert "disposition" in resp.text  # response body rendered


async def test_detail_resolves_entity_link(client, seeded_log):
    resp = await client.get(f"{_BASE}{seeded_log['new']}/", headers=AUTH_HEADERS)
    assert f"/admin/people/{seeded_log['person']}/" in resp.text


async def test_detail_removed_entity_fallback(client, seeded_log):
    resp = await client.get(f"{_BASE}{seeded_log['removed']}/", headers=AUTH_HEADERS)
    assert resp.status_code == 200
    assert "removed" in resp.text.lower()


async def test_detail_404_for_unknown(client, seeded_log):
    resp = await client.get(f"{_BASE}999999999/", headers=AUTH_HEADERS)
    assert resp.status_code == 404


async def test_list_has_active_sidebar_sublink(client, seeded_log):
    """API Requests sidebar sublink is present and active on the list page (#260 CR)."""
    resp = await client.get(_BASE, headers=AUTH_HEADERS)
    assert 'href="/admin/activity/requests/" aria-current="page"' in resp.text


async def test_detail_resolves_role_link(client, db):
    """result_entity_id of a role observation deep-links to the role admin screen (#260 CR)."""
    org_id, role_id = generate_id(), generate_id()
    await db.execute("INSERT INTO organizations (id) VALUES ($1)", org_id)
    await db.execute(
        "INSERT INTO roles (id, organization_id, title) VALUES ($1,$2,'ARL Role')",
        role_id,
        org_id,
    )
    lid = await _insert_log(
        db,
        path="/api/v1/roles/observations",
        disposition="new",
        entity_type="role",
        result_entity_id=role_id,
    )
    resp = await client.get(f"{_BASE}{lid}/", headers=AUTH_HEADERS)
    assert resp.status_code == 200
    assert f"/admin/roles/{role_id}/" in resp.text


async def test_detail_resolves_role_assignment_link(client, db):
    """role_assignment result_entity_id deep-links to its admin screen (#260 CR)."""
    org_id, role_id, person_id, ra_id = (generate_id() for _ in range(4))
    await db.execute("INSERT INTO organizations (id) VALUES ($1)", org_id)
    await db.execute("INSERT INTO people (id) VALUES ($1)", person_id)
    await db.execute(
        "INSERT INTO roles (id, organization_id, title) VALUES ($1,$2,'ARL RA Role')",
        role_id,
        org_id,
    )
    await db.execute(
        "INSERT INTO role_assignments (id, person_id, role_id) VALUES ($1,$2,$3)",
        ra_id,
        person_id,
        role_id,
    )
    lid = await _insert_log(
        db,
        path="/api/v1/assignments/observations",
        disposition="new",
        entity_type="role_assignment",
        result_entity_id=ra_id,
    )
    resp = await client.get(f"{_BASE}{lid}/", headers=AUTH_HEADERS)
    assert resp.status_code == 200
    assert f"/admin/role-assignments/{ra_id}/" in resp.text


async def test_sidebar_sublink_renders_on_non_activity_page(client, seeded_log):
    """The API Requests sidebar sublink renders on other admin pages, un-highlighted (#260 CR)."""
    # dashboard, active_section='dashboard'
    resp = await client.get("/admin/", headers=AUTH_HEADERS)
    assert resp.status_code == 200
    assert 'class="admin-sidebar__link" href="/admin/activity/requests/"' in resp.text
    # Not the current page here, so the sidebar link must not be marked active.
    assert 'href="/admin/activity/requests/" aria-current="page"' not in resp.text
