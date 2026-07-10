"""Tests for the pure-ASGI request-capture middleware (#260, step 3)."""

import asyncio
import hashlib
import json
import logging
import os
from unittest.mock import patch

import pytest
import pytest_asyncio

from src.api.public.middleware import (
    RequestLogMiddleware,
    _enrich,
    _pending_writes,
    drain_pending_writes,
    route_group_for_path,
)
from src.core.db import generate_id


async def _drain_pending_writes(timeout: float = 5.0):
    """Await any in-flight fire-and-forget capture writes (test helper).

    Thin wrapper over the production ``drain_pending_writes`` (#286) so the
    fixtures/tests exercise the same drain the ASGI shutdown hook uses.
    """
    await drain_pending_writes(timeout)


async def _poll_row(db, sql, *args, tries: int = 50, delay: float = 0.05):
    """Poll for a capture row written by the fire-and-forget task (#262).

    The capture INSERT now runs on a background task on the app's event loop, so
    it may land just after the sync TestClient request returns. Retry a few times
    before giving up rather than assuming the row is present immediately.
    """
    row = None
    for _ in range(tries):
        row = await db.fetchrow(sql, *args)
        if row is not None:
            return row
        await asyncio.sleep(delay)
    return row


# ---------------------------------------------------------------------------
# Unit — route classification (no DB)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/people/observations",
        "/api/v1/orgs/observations",
        "/api/v1/jurisdictions/observations",
        "/api/v1/observations",
        "/api/v1/people/observations/",
    ],
)
def test_route_group_observations(path):
    assert route_group_for_path(path) == "observations"


def test_route_group_changes():
    assert route_group_for_path("/api/v1/changes") == "changes"
    assert route_group_for_path("/api/v1/changes/") == "changes"


@pytest.mark.parametrize(
    "path",
    ["/api/v1/", "/api/v1/people/01ABC", "/api/v1/orgs", "/api/v1/link-types"],
)
def test_route_group_other(path):
    assert route_group_for_path(path) == "other"


# ---------------------------------------------------------------------------
# Unit — fire-and-forget scheduling (#262, no DB)
# ---------------------------------------------------------------------------


async def _run_middleware(monkeypatch, *, downstream=None):
    """Drive one GET /api/v1/ request through the middleware with a stub app.

    Returns (send_messages, response_returned_at, write_scheduled). ``_write`` is
    patched so no DB is touched; scheduling is what we assert on.
    """

    async def _default_app(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"{}"})

    app = downstream or _default_app
    middleware = RequestLogMiddleware(app)

    scope = {
        "type": "http",
        "path": "/api/v1/",
        "method": "GET",
        "headers": [],
        "state": {},
    }

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    sent = []

    async def send(message):
        sent.append(message)

    await middleware(scope, receive, send)
    return sent


async def test_write_scheduled_not_awaited_in_request_path(monkeypatch):
    """The INSERT is scheduled as a background task, not awaited synchronously.

    Simulated by a slow ``_write``: the request must return before the write
    resolves. We assert the middleware call completes with the write still
    pending, then drain it.
    """
    started = asyncio.Event()
    release = asyncio.Event()

    async def slow_write(_params):
        started.set()
        await release.wait()

    with patch.object(RequestLogMiddleware, "_write", side_effect=slow_write, autospec=False):
        sent = await _run_middleware(monkeypatch)
        # Response already produced by send()...
        assert any(m["type"] == "http.response.start" for m in sent)
        # ...but the write task is still pending (not awaited in the request path).
        await asyncio.wait_for(started.wait(), timeout=1)
        assert len(_pending_writes) >= 1
        assert not all(t.done() for t in _pending_writes)
        release.set()
        await _drain_pending_writes()

    assert len(_pending_writes) == 0  # task-reference set drained on completion


async def test_capture_failure_is_swallowed(monkeypatch):
    """A background write failure must never propagate to the request path."""

    async def boom(_self, _params):
        raise RuntimeError("insert exploded")

    with patch.object(RequestLogMiddleware, "_write", boom):
        # Request completes normally despite the scheduled write raising.
        sent = await _run_middleware(monkeypatch)
        assert any(m["type"] == "http.response.start" and m["status"] == 200 for m in sent)
        await _drain_pending_writes()

    # No lingering task references and no exception surfaced.
    assert len(_pending_writes) == 0


async def test_write_runs_and_reference_discarded(monkeypatch):
    """The scheduled write actually runs; its task reference is discarded on done."""
    calls = []

    async def record_write(_self, params):
        calls.append(params)

    with patch.object(RequestLogMiddleware, "_write", record_write):
        await _run_middleware(monkeypatch)
        await _drain_pending_writes()

    assert len(calls) == 1  # write executed exactly once
    assert len(_pending_writes) == 0  # reference set cleaned up


# ---------------------------------------------------------------------------
# Unit — shutdown drain (#286, no DB)
# ---------------------------------------------------------------------------


async def test_drain_awaits_in_flight_writes(monkeypatch):
    """``drain_pending_writes`` awaits a scheduled write to completion.

    Restores the pre-#262 durable-on-return contract for graceful shutdown:
    the row write finishes before the drain returns.
    """
    started = asyncio.Event()
    completed = []

    async def slow_write(_self, _params):
        started.set()
        await asyncio.sleep(0.05)
        completed.append(True)

    with patch.object(RequestLogMiddleware, "_write", slow_write):
        await _run_middleware(monkeypatch)
        await asyncio.wait_for(started.wait(), timeout=1)
        assert not completed  # not yet done when drain begins
        await drain_pending_writes()

    assert completed == [True]  # write finished during the drain
    assert len(_pending_writes) == 0


async def test_drain_is_bounded_and_swallows_timeout(monkeypatch):
    """A stalled write must not hang shutdown; the drain returns within its bound."""
    release = asyncio.Event()

    async def stalled_write(_self, _params):
        await release.wait()  # never released during the drain

    with patch.object(RequestLogMiddleware, "_write", stalled_write):
        await _run_middleware(monkeypatch)
        # Bounded: returns (no raise) even though the write never completes.
        await asyncio.wait_for(drain_pending_writes(timeout=0.1), timeout=1)
        # Cleanup: release the stalled task so it doesn't leak into other tests.
        release.set()
        await _drain_pending_writes()

    assert len(_pending_writes) == 0


async def test_drain_noop_when_nothing_pending():
    """Draining with an empty pending set returns immediately without error."""
    assert len(_pending_writes) == 0
    await drain_pending_writes()


async def test_drain_timeout_logs_accurate_lost_count(caplog):
    """The timeout warning reports how many writes were actually lost, not the
    total scheduled — a completed write in the same batch must not be counted."""
    release = asyncio.Event()
    calls = {"n": 0}

    async def mixed_write(_self, _params):
        calls["n"] += 1
        if calls["n"] == 1:
            await release.wait()  # first write stalls past the drain timeout
        # second write returns immediately (completes within the window)

    async def _noop_app(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"{}"})

    mw = RequestLogMiddleware(_noop_app)
    with patch.object(RequestLogMiddleware, "_write", mixed_write):
        mw._schedule_write(("stalls",))  # scheduled first → stalls
        mw._schedule_write(("completes",))  # scheduled second → completes
        with caplog.at_level(logging.WARNING):
            await drain_pending_writes(timeout=0.1)
        # Exactly one write was lost; the other completed. Naive len() would say 2.
        assert "1 write(s) may be lost" in caplog.text
        # Cleanup: release the stalled task so it can't leak into other tests.
        release.set()
        await _drain_pending_writes()

    assert len(_pending_writes) == 0


# ---------------------------------------------------------------------------
# Integration — capture end-to-end
# ---------------------------------------------------------------------------

pytestmark_integration = pytest.mark.integration


# The request-log middleware writes its row on a background task via the *global*
# pool (a deliberately separate, committed connection — never the request-scoped
# one). That connection can't see rows created in the rollback client's
# uncommitted transaction, so the api_key would FK-fail and no row would be
# written. Shadow db/client with the committing (autocommit) variants so the
# api_key is committed and visible to the middleware's connection (#288).
@pytest_asyncio.fixture(loop_scope="session")
async def db(committing_db):
    return committing_db


@pytest_asyncio.fixture(loop_scope="session")
async def client(committing_client):
    return committing_client


@pytest_asyncio.fixture(loop_scope="session")
async def plain_key(db):
    """A valid API key with no scopes (enough for require_api_key routes)."""
    uid = generate_id()
    kid = generate_id()
    raw = "pm_" + os.urandom(16).hex()
    key_hash = hashlib.sha256(raw.encode()).hexdigest()
    await db.execute("INSERT INTO app_users (id, email) VALUES ($1,$2)", uid, "arl_plain@test.com")
    await db.execute(
        "INSERT INTO api_keys (id, user_id, label, key_prefix, key_hash) VALUES ($1,$2,$3,$4,$5)",
        kid,
        uid,
        "ARL Plain Key",
        raw[:8],
        key_hash,
    )
    yield raw, kid
    # Committing fixture: drain in-flight middleware writes first, then delete the
    # log rows before the key (api_request_log.api_key_id FK → api_keys). Best
    # effort — a stalled write must not turn teardown into an error.
    try:
        await _drain_pending_writes()
    except TimeoutError:
        pass
    await db.execute("DELETE FROM api_request_log WHERE api_key_id=$1", kid)
    await db.execute("DELETE FROM api_keys WHERE id=$1", kid)
    await db.execute("DELETE FROM app_users WHERE id=$1", uid)


@pytest_asyncio.fixture(loop_scope="session")
async def obs_key(db):
    """API key with observations:write scope (for the body-tee POST)."""
    scope_id = "observations:write"
    existing = await db.fetchrow("SELECT id FROM api_key_scope_types WHERE id=$1", scope_id)
    if not existing:
        await db.execute(
            "INSERT INTO api_key_scope_types (id, display_name, description) VALUES ($1,$2,$3)",
            scope_id,
            "Observations Write",
            "Create and update observations",
        )
    uid = generate_id()
    kid = generate_id()
    raw = "pm_" + os.urandom(16).hex()
    key_hash = hashlib.sha256(raw.encode()).hexdigest()
    await db.execute("INSERT INTO app_users (id, email) VALUES ($1,$2)", uid, "arl_obs@test.com")
    await db.execute(
        "INSERT INTO api_keys (id, user_id, label, key_prefix, key_hash) VALUES ($1,$2,$3,$4,$5)",
        kid,
        uid,
        "ARL Obs Key",
        raw[:8],
        key_hash,
    )
    await db.execute(
        "INSERT INTO api_key_scopes (api_key_id, scope_id) VALUES ($1,$2)", kid, scope_id
    )
    yield raw, kid
    # Committing fixture: drain in-flight middleware writes, then delete in
    # FK-safe order (log rows + scopes reference the key). Best effort — a stalled
    # write must not turn teardown into an error.
    try:
        await _drain_pending_writes()
    except TimeoutError:
        pass
    await db.execute("DELETE FROM api_request_log WHERE api_key_id=$1", kid)
    await db.execute("DELETE FROM api_key_scopes WHERE api_key_id=$1", kid)
    await db.execute("DELETE FROM api_keys WHERE id=$1", kid)
    await db.execute("DELETE FROM app_users WHERE id=$1", uid)


@pytest.mark.integration
async def test_valid_v1_get_logs_row(client, db, plain_key):
    raw, kid = plain_key
    resp = await client.get("/api/v1/", headers={"X-API-Key": raw})
    assert resp.status_code == 200
    row = await _poll_row(
        db, "SELECT * FROM api_request_log WHERE api_key_id=$1 ORDER BY id DESC LIMIT 1", kid
    )
    assert row is not None
    assert row["method"] == "GET"
    assert row["path"] == "/api/v1/"
    assert row["route_group"] == "other"
    assert row["status_code"] == 200
    assert row["latency_ms"] >= 0
    assert row["api_key_id"] == kid
    # Bodies are captured only for observations/changes; 'other' stores metadata only.
    assert row["request_body"] is None
    assert row["response_body"] is None


@pytest.mark.integration
async def test_non_v1_path_not_logged(client, db):
    before = await db.fetchval("SELECT COUNT(*) FROM api_request_log WHERE path LIKE '/admin%'")
    await client.get("/admin/")  # 307 redirect (no exe.dev headers) — must not be logged
    after = await db.fetchval("SELECT COUNT(*) FROM api_request_log WHERE path LIKE '/admin%'")
    assert after == before


@pytest.mark.integration
async def test_invalid_key_logs_null_key_row(client, db):
    resp = await client.get("/api/v1/", headers={"X-API-Key": "pm_definitely_invalid"})
    assert resp.status_code == 401
    row = await _poll_row(
        db,
        "SELECT * FROM api_request_log WHERE path='/api/v1/' AND status_code=401"
        " ORDER BY id DESC LIMIT 1",
    )
    assert row is not None
    assert row["api_key_id"] is None
    assert row["route_group"] == "other"


@pytest.mark.integration
async def test_body_tee_preserves_downstream_and_captures_body(client, db, obs_key):
    """POSTing a body: downstream must still parse it (normal response) and we capture it."""
    raw, kid = obs_key
    payload = {"identifier_type": "definitely_unknown_type", "identifier_value": "x123"}
    resp = await client.post(
        "/api/v1/people/observations", json=payload, headers={"X-API-Key": raw}
    )
    # Downstream read the JSON body and produced a normal ObservationResponse.
    assert resp.status_code == 200
    assert "disposition" in resp.json()
    row = await _poll_row(
        db,
        "SELECT * FROM api_request_log WHERE api_key_id=$1 AND route_group='observations'"
        " ORDER BY id DESC LIMIT 1",
        kid,
    )
    assert row is not None
    assert row["method"] == "POST"
    assert json.loads(row["request_body"]) == payload
    assert row["response_body"] is not None


# ---------------------------------------------------------------------------
# Step 4 — domain enrichment (unit, no DB)
# ---------------------------------------------------------------------------


def test_enrich_observation_success():
    out = _enrich(
        "observations",
        {"disposition": "new", "entity_id": "01ENTITY", "entity_type": "person", "reason": None},
    )
    assert out["disposition"] == "new"
    assert out["result_entity_id"] == "01ENTITY"
    assert out["entity_type"] == "person"
    assert out["reason"] is None
    assert out["item_count"] is None
    assert out["is_empty"] is False


def test_enrich_observation_rejected():
    out = _enrich(
        "observations",
        {"disposition": "rejected", "entity_id": None, "entity_type": None, "reason": "bad_type"},
    )
    assert out["disposition"] == "rejected"
    assert out["result_entity_id"] is None
    assert out["entity_type"] is None
    assert out["reason"] == "bad_type"


def test_enrich_changes_empty():
    out = _enrich(
        "changes",
        {"data": [], "meta": {"count": 0, "limit": 50, "has_more": False, "next_after": 0}},
    )
    assert out["item_count"] == 0
    assert out["is_empty"] is True


def test_enrich_changes_nonempty():
    out = _enrich("changes", {"data": [{}, {}, {}], "meta": {"count": 3}})
    assert out["item_count"] == 3
    assert out["is_empty"] is False


def test_enrich_changes_count_falls_back_to_data_len():
    out = _enrich("changes", {"data": [{}, {}], "meta": {}})
    assert out["item_count"] == 2
    assert out["is_empty"] is False


def test_enrich_other_and_nondict_noop():
    assert _enrich("other", {"disposition": "new"})["disposition"] is None
    assert _enrich("observations", None)["disposition"] is None
    assert _enrich("changes", "not-a-dict")["item_count"] is None


# ---------------------------------------------------------------------------
# Step 4 — domain enrichment (integration)
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_observation_new_enriched(client, db, obs_key):
    raw, kid = obs_key
    payload = {"identifier_type": "person_wa_pdc", "identifier_value": "arl_" + os.urandom(6).hex()}
    resp = await client.post(
        "/api/v1/people/observations", json=payload, headers={"X-API-Key": raw}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["disposition"] == "new"
    row = await _poll_row(
        db,
        "SELECT * FROM api_request_log WHERE api_key_id=$1 AND route_group='observations'"
        " ORDER BY id DESC LIMIT 1",
        kid,
    )
    assert row["disposition"] == "new"
    assert row["entity_type"] == "person"
    assert row["result_entity_id"] == body["entity_id"]
    assert row["reason"] is None


@pytest.mark.integration
async def test_observation_rejected_enriched(client, db, obs_key):
    raw, kid = obs_key
    payload = {"identifier_type": "zzz_nonexistent_xyz", "identifier_value": "v"}
    resp = await client.post(
        "/api/v1/people/observations", json=payload, headers={"X-API-Key": raw}
    )
    assert resp.status_code == 200
    assert resp.json()["disposition"] == "rejected"
    row = await _poll_row(
        db,
        "SELECT * FROM api_request_log WHERE api_key_id=$1 AND route_group='observations'"
        " ORDER BY id DESC LIMIT 1",
        kid,
    )
    assert row["disposition"] == "rejected"
    assert row["result_entity_id"] is None
    assert row["reason"] is not None


@pytest.mark.integration
async def test_changes_empty_poll_enriched(client, db, plain_key):
    raw, kid = plain_key
    resp = await client.get("/api/v1/changes?after=0", headers={"X-API-Key": raw})
    assert resp.status_code == 200
    row = await _poll_row(
        db,
        "SELECT * FROM api_request_log WHERE api_key_id=$1 AND route_group='changes'"
        " ORDER BY id DESC LIMIT 1",
        kid,
    )
    assert row is not None
    assert row["item_count"] == 0
    assert row["is_empty"] is True
    # changes is a captured group: the response body is stored (GET has no request body).
    assert row["response_body"] is not None
    assert row["request_body"] is None
