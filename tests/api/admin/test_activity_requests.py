"""Integration tests for the admin API request-log screens (#260, steps 6-7)."""

import hashlib
import os

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient

from src.api.main import app
from src.core.db import generate_id

pytestmark = [pytest.mark.integration]

AUTH_HEADERS = {"X-ExeDev-UserID": "usr_test", "X-ExeDev-Email": "admin@test.com"}
_BASE = "/admin/activity/requests/"


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


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
async def seeded_log(db_pool):
    """Seed a key, a linkable person, and a spread of log rows. Yields ids dict."""
    uid, kid, pid = generate_id(), generate_id(), generate_id()
    raw = "pm_" + os.urandom(8).hex()
    async with db_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO app_users (id, email) VALUES ($1,$2)", uid, "arl_ui@test.com"
        )
        await conn.execute(
            "INSERT INTO api_keys (id, user_id, label, key_prefix, key_hash)"
            " VALUES ($1,$2,$3,$4,$5)",
            kid,
            uid,
            "UI Test Key",
            raw[:8],
            hashlib.sha256(raw.encode()).hexdigest(),
        )
        await conn.execute("INSERT INTO people (id) VALUES ($1)", pid)
        ids = {"key": kid, "person": pid}
        ids["new"] = await _insert_log(
            conn,
            api_key_id=kid,
            disposition="new",
            entity_type="person",
            result_entity_id=pid,
            request_body='{"identifier_type":"x"}',
            response_body='{"disposition":"new"}',
        )
        ids["rejected"] = await _insert_log(
            conn,
            api_key_id=kid,
            disposition="rejected",
            status_code=200,
            reason="unknown_identifier_type",
        )
        ids["removed"] = await _insert_log(
            conn,
            api_key_id=kid,
            disposition="new",
            entity_type="person",
            result_entity_id=generate_id(),  # not in people -> "(removed)"
        )
        ids["empty_poll"] = await _insert_log(
            conn,
            api_key_id=kid,
            method="GET",
            path="/api/v1/changes",
            route_group="changes",
            item_count=0,
            is_empty=True,
        )
        ids["other"] = await _insert_log(
            conn,
            api_key_id=kid,
            method="GET",
            path="/api/v1/link-types",
            route_group="other",
        )
    yield ids
    async with db_pool.acquire() as conn:
        await conn.execute("DELETE FROM api_request_log WHERE api_key_id=$1", kid)
        await conn.execute("DELETE FROM people WHERE id=$1", pid)
        await conn.execute("DELETE FROM api_keys WHERE id=$1", kid)
        await conn.execute("DELETE FROM app_users WHERE id=$1", uid)


# ---------------------------------------------------------------------------
# List screen
# ---------------------------------------------------------------------------


def test_list_returns_200(client, seeded_log):
    resp = client.get(_BASE, headers=AUTH_HEADERS)
    assert resp.status_code == 200
    assert "Requests" in resp.text


def test_list_redirects_unauthenticated(client):
    resp = client.get(_BASE, follow_redirects=False)
    assert resp.status_code in (302, 307)
    assert "/__exe.dev/login" in resp.headers["location"]


def test_list_default_hides_other_group(client, seeded_log):
    """Default view is observations + changes; 'other' rows excluded."""
    resp = client.get(_BASE, headers=AUTH_HEADERS)
    assert f"/admin/activity/requests/{seeded_log['new']}/" in resp.text
    assert f"/admin/activity/requests/{seeded_log['other']}/" not in resp.text


def test_list_all_group_shows_other(client, seeded_log):
    resp = client.get(_BASE + "?group=all", headers=AUTH_HEADERS)
    assert f"/admin/activity/requests/{seeded_log['other']}/" in resp.text


def test_list_default_hides_empty_polls(client, seeded_log):
    resp = client.get(_BASE, headers=AUTH_HEADERS)
    assert f"/admin/activity/requests/{seeded_log['empty_poll']}/" not in resp.text


def test_list_show_empty_includes_empty_polls(client, seeded_log):
    resp = client.get(_BASE + "?show_empty=true", headers=AUTH_HEADERS)
    assert f"/admin/activity/requests/{seeded_log['empty_poll']}/" in resp.text


def test_list_filter_by_disposition_rejected(client, seeded_log):
    resp = client.get(_BASE + "?disposition=rejected", headers=AUTH_HEADERS)
    assert f"/admin/activity/requests/{seeded_log['rejected']}/" in resp.text
    assert f"/admin/activity/requests/{seeded_log['new']}/" not in resp.text


def test_list_shows_key_label_not_hash(client, seeded_log):
    resp = client.get(_BASE, headers=AUTH_HEADERS)
    assert "UI Test Key" in resp.text


def test_list_stats_strip_present(client, seeded_log):
    resp = client.get(_BASE, headers=AUTH_HEADERS)
    # Stats strip surfaces rejection + error signals.
    assert "Rejected" in resp.text


# ---------------------------------------------------------------------------
# Detail screen
# ---------------------------------------------------------------------------


def test_detail_returns_200_with_bodies(client, seeded_log):
    resp = client.get(f"{_BASE}{seeded_log['new']}/", headers=AUTH_HEADERS)
    assert resp.status_code == 200
    assert "identifier_type" in resp.text  # request body rendered
    assert "disposition" in resp.text  # response body rendered


def test_detail_resolves_entity_link(client, seeded_log):
    resp = client.get(f"{_BASE}{seeded_log['new']}/", headers=AUTH_HEADERS)
    assert f"/admin/people/{seeded_log['person']}/" in resp.text


def test_detail_removed_entity_fallback(client, seeded_log):
    resp = client.get(f"{_BASE}{seeded_log['removed']}/", headers=AUTH_HEADERS)
    assert resp.status_code == 200
    assert "removed" in resp.text.lower()


def test_detail_404_for_unknown(client, seeded_log):
    resp = client.get(f"{_BASE}999999999/", headers=AUTH_HEADERS)
    assert resp.status_code == 404
