"""Pure-ASGI middleware: capture public API request/response telemetry (#260).

Logs one ``api_request_log`` row per ``/api/v1/*`` request. Implemented as a
pure-ASGI middleware (not ``BaseHTTPMiddleware``) so it can tee the request and
response bodies without breaking downstream ``.json()`` reads.

Identity comes from ``scope["state"]["api_key_id"]``, stashed by the auth deps
(``src.api.public.deps``) when a key resolves; unauthenticated requests log a
row with a NULL key. Domain-specific enrichment (disposition, entity_id, item
count) is layered on in ``_enrich`` for the observations/changes route groups.

Capture is strictly best-effort: any failure while recording is swallowed and
logged, so observability can never break the request path.

The row write is **fire-and-forget** (#262): the middleware builds the INSERT
parameters synchronously on the request tail (cheap — parsing only, no DB), then
schedules the pool-acquire + INSERT on a background ``asyncio`` task so it never
adds to the awaited request latency. Strong refs to in-flight tasks are held in
``_pending_writes`` (discarded on completion) so they aren't garbage-collected
mid-flight — the classic ``create_task`` footgun. Best-effort is preserved: the
swallow-and-log now lives inside the background task.

On *graceful* shutdown the ASGI lifespan calls ``drain_pending_writes`` (#286)
to flush any in-flight writes before the pool closes, restoring the pre-#262
durable-on-return contract for the deploy/restart path. A hard crash still drops
in-flight rows — acceptable under the best-effort posture.

The in-flight set is bounded by ``_MAX_PENDING_WRITES`` (#290): a burst that
would otherwise spawn unbounded tasks all queued on ``pool.acquire()`` instead
sheds the newest writes once the cap is reached, keeping the older (closer to
landing) ones. Shed writes are counted and warned (throttled), never silently
dropped — consistent with the best-effort contract.
"""

import asyncio
import json
import os
import time

import src.core.db as db
from src.core.logging import get_logger

logger = get_logger(__name__)

_V1_PREFIX = "/api/v1"

# Strong references to in-flight fire-and-forget capture writes (#262). Without
# this, ``asyncio`` only keeps a weak ref to the task and may GC it before it
# runs. Each task discards its own ref via a done-callback. Per-worker: each
# uvicorn worker process owns its own set and drains it independently (#286).
_pending_writes: set[asyncio.Task] = set()

_DRAIN_TIMEOUT_S = 5.0

# Soft cap on in-flight capture writes (#290). When the in-flight set is at the
# cap, an incoming write is *shed* (drop-newest) rather than piling another task
# onto ``pool.acquire()`` — the oldest in-flight writes are closest to landing,
# so those are the ones worth keeping. Shedding telemetry never affects the
# request (best-effort contract). Tune relative to ``DB_POOL_MAX_SIZE``: a
# shallow cap sheds early and protects handler acquire latency. The cap bounds
# acquire-queue depth + task/memory growth under a burst; it does not by itself
# resolve request/capture pool contention (that's #290 item 2).
_MAX_PENDING_WRITES = int(os.environ.get("API_REQUEST_LOG_MAX_PENDING", "50"))

# Shed accounting (#290). ``_shed_count`` is the cumulative number of capture
# writes shed since process start; ``_shed_reported`` is how much of that has
# already been surfaced in a warning. The gap between them is unreported shed
# volume awaiting the next report. Per-worker, like ``_pending_writes``.
_shed_count = 0
_shed_reported = 0
# Monotonic timestamp of the last shed warning; ``None`` until the first shed
# ever (so a fake clock reading 0.0 in tests still counts as "never warned").
_shed_last_warn_s: float | None = None

# Shed warnings are rate-limited to at most one per this interval, so neither a
# sustained saturation (many sheds) nor oscillation right at the cap (rapid
# shed/admit alternation) can turn into a log storm. Each warning carries the
# volume since the last report, and a trailing report on the first post-interval
# admit flushes a finished burst's remainder — so no burst stays hidden.
_SHED_WARN_INTERVAL_S = 10.0


def _report_shedding() -> None:
    """Surface accumulated capture-write shedding, rate-limited (#290).

    No-op when nothing is unreported (the steady state — one cheap subtraction on
    the schedule path). Otherwise warns at most once per ``_SHED_WARN_INTERVAL_S``,
    always reporting the volume shed since the last report plus the running total
    since startup. Called on both the shed path (so onset + sustained shedding
    surface) and the admit path (so the tail of a finished burst flushes on the
    first post-interval recovery instead of waiting for the next shed).
    """
    global _shed_reported, _shed_last_warn_s
    unreported = _shed_count - _shed_reported
    if unreported <= 0:
        return
    now = time.monotonic()
    if _shed_last_warn_s is not None and now - _shed_last_warn_s < _SHED_WARN_INTERVAL_S:
        return
    logger.warning(
        "api_request_log capture shedding: in-flight writes at cap (%d); "
        "%d write(s) shed since last report, %d since startup",
        _MAX_PENDING_WRITES,
        unreported,
        _shed_count,
    )
    _shed_reported = _shed_count
    _shed_last_warn_s = now


def _unfinished(task: asyncio.Task) -> bool:
    """True if a capture write didn't finish cleanly (still running, cancelled,
    or errored). The short-circuit keeps ``.exception()`` off cancelled tasks,
    which would otherwise re-raise ``CancelledError``.
    """
    return not task.done() or task.cancelled() or task.exception() is not None


async def drain_pending_writes(timeout: float = _DRAIN_TIMEOUT_S) -> None:
    """Await in-flight fire-and-forget capture writes on shutdown (#286).

    Wired into the ASGI lifespan **before** the pool closes, this restores the
    pre-#262 durable-on-return contract for *graceful* shutdown (deploy,
    ``systemctl restart``, SIGTERM): rows scheduled but not yet flushed get a
    bounded window to land instead of being silently dropped.

    Bounded and best-effort — a stalled write can't hang the deploy, and any
    failure (including timeout) is swallowed and logged. A hard crash
    (SIGKILL/OOM) still loses in-flight rows, consistent with #262's best-effort
    posture. Per-worker: drains only this process's ``_pending_writes``.
    """
    pending = list(_pending_writes)
    if not pending:
        return
    try:
        await asyncio.wait_for(asyncio.gather(*pending, return_exceptions=True), timeout)
    except TimeoutError:
        # Count from the snapshot, not the live set: the timeout cancels the
        # gather (and its children), whose done-callbacks discard from
        # _pending_writes on the next loop tick — reading the set here would
        # under-report.
        lost = sum(1 for t in pending if _unfinished(t))
        logger.warning(
            "api_request_log drain timed out after %ss; %d write(s) may be lost",
            timeout,
            lost,
        )


_INSERT_SQL = """
    INSERT INTO api_request_log
        (api_key_id, method, path, route_group, entity_type, status_code, latency_ms,
         disposition, result_entity_id, reason, item_count, is_empty,
         client_ip, user_agent, request_body, response_body)
    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15::jsonb, $16::jsonb)
"""


def route_group_for_path(path: str) -> str:
    """Classify a request path into a coarse group for filtering/aggregation.

    ``observations`` — any ``*/observations`` write endpoint.
    ``changes``      — the ``/changes`` feed.
    ``other``        — every other v1 path.
    """
    last = path.rstrip("/").rsplit("/", 1)[-1]
    if last == "observations":
        return "observations"
    if last == "changes":
        return "changes"
    return "other"


def _body_to_jsonb_param(raw: bytes) -> str | None:
    """Return a JSONB-castable string for a raw body, or None when empty.

    Valid JSON is stored verbatim. A non-JSON / malformed body is preserved
    inspectably as ``{"_unparsed": "<text>"}`` rather than dropped (#260 risk
    decision: malformed payloads must still be debuggable).
    """
    if not raw:
        return None
    text = raw.decode("utf-8", errors="replace")
    try:
        json.loads(text)
    except ValueError:
        return json.dumps({"_unparsed": text})
    return text


def _safe_json(raw: bytes):
    """Best-effort parse of a body into a Python object for enrichment; None on failure."""
    if not raw:
        return None
    try:
        return json.loads(raw)
    except ValueError:
        return None


def _enrich(route_group: str, response_obj) -> dict:
    """Extract domain columns from a parsed response for known route groups.

    Observations expose ``disposition`` / ``entity_id`` / ``entity_type`` /
    ``reason``; the change feed exposes ``meta.count`` (falling back to
    ``len(data)``). Any other group, or a non-dict body, yields all-empty
    columns — enrichment never raises.
    """
    fields = {
        "entity_type": None,
        "disposition": None,
        "result_entity_id": None,
        "reason": None,
        "item_count": None,
        "is_empty": False,
    }
    if not isinstance(response_obj, dict):
        return fields
    if route_group == "observations":
        fields["disposition"] = response_obj.get("disposition")
        fields["result_entity_id"] = response_obj.get("entity_id")
        fields["entity_type"] = response_obj.get("entity_type")
        fields["reason"] = response_obj.get("reason")
    elif route_group == "changes":
        meta = response_obj.get("meta")
        count = meta.get("count") if isinstance(meta, dict) else None
        if count is None:
            data = response_obj.get("data")
            count = len(data) if isinstance(data, list) else None
        fields["item_count"] = count
        fields["is_empty"] = count == 0
    return fields


class RequestLogMiddleware:
    """Capture ``/api/v1/*`` request/response telemetry into ``api_request_log``."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not scope.get("path", "").startswith(_V1_PREFIX):
            await self.app(scope, receive, send)
            return

        # Raw bodies are stored (and enrichment parsed) only for the two groups the
        # log surfaces — observations/changes. Other v1 traffic (e.g. large embedding
        # vectors) records metadata only, so we don't even buffer its bodies in memory.
        route_group = route_group_for_path(scope.get("path", ""))
        capture_bodies = route_group in ("observations", "changes")

        request_chunks: list[bytes] = []

        async def receive_wrapper():
            message = await receive()
            if message["type"] == "http.request":
                request_chunks.append(message.get("body", b""))
            return message

        recv = receive_wrapper if capture_bodies else receive

        status_code = 500
        response_chunks: list[bytes] = []

        async def send_wrapper(message):
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
            elif message["type"] == "http.response.body" and capture_bodies:
                response_chunks.append(message.get("body", b""))
            await send(message)

        start = time.perf_counter()
        try:
            await self.app(scope, recv, send_wrapper)
        finally:
            latency_ms = int((time.perf_counter() - start) * 1000)
            # Build INSERT params on the request tail (parsing only, no DB), then
            # fire-and-forget the write off the hot path (#262). Param assembly is
            # itself best-effort so a malformed capture can never break the request.
            try:
                params = self._build_params(
                    scope,
                    route_group,
                    capture_bodies,
                    request_chunks,
                    status_code,
                    response_chunks,
                    latency_ms,
                )
            except Exception:  # observability must never break the request
                logger.warning("api_request_log param build failed", exc_info=True)
            else:
                self._schedule_write(params)

    def _schedule_write(self, params: tuple) -> None:
        """Fire-and-forget the row INSERT on a background task (#262).

        Holds a strong ref to the task in ``_pending_writes`` until it completes
        so it isn't garbage-collected mid-flight, then discards the ref.

        Backpressure (#290): if the in-flight set is already at
        ``_MAX_PENDING_WRITES`` the incoming write is shed (drop-newest) instead
        of scheduling another task that would only queue on ``pool.acquire()``.
        The check + ``create_task`` run to completion between ``await`` points on
        the single event loop, so the cap can't be raced past. Shed writes are
        counted and surfaced via a rate-limited warning — never silently dropped.
        """
        if len(_pending_writes) >= _MAX_PENDING_WRITES:
            global _shed_count
            _shed_count += 1
            _report_shedding()
            return
        # Admit path: flush the tail of any just-ended shedding burst (no-op in
        # the steady state where nothing has been shed).
        _report_shedding()
        task = asyncio.create_task(self._write(params))
        _pending_writes.add(task)
        task.add_done_callback(_pending_writes.discard)

    def _build_params(
        self,
        scope,
        route_group,
        capture_bodies,
        request_chunks,
        status_code,
        response_chunks,
        latency_ms,
    ) -> tuple:
        path = scope.get("path", "")
        method = scope.get("method", "")
        state = scope.get("state") or {}
        api_key_id = state.get("api_key_id")

        headers = {
            k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])
        }
        user_agent = headers.get("user-agent")
        forwarded = headers.get("x-forwarded-for")
        if forwarded:
            client_ip = forwarded.split(",")[0].strip()
        elif scope.get("client"):
            client_ip = scope["client"][0]
        else:
            client_ip = None

        if capture_bodies:
            response_raw = b"".join(response_chunks)
            request_body = _body_to_jsonb_param(b"".join(request_chunks))
            response_body = _body_to_jsonb_param(response_raw)
            enriched = _enrich(route_group, _safe_json(response_raw))
        else:
            request_body = None
            response_body = None
            enriched = _enrich(route_group, None)

        return (
            api_key_id,
            method,
            path,
            route_group,
            enriched["entity_type"],
            status_code,
            latency_ms,
            enriched["disposition"],
            enriched["result_entity_id"],
            enriched["reason"],
            enriched["item_count"],
            enriched["is_empty"],
            client_ip,
            user_agent,
            request_body,
            response_body,
        )

    async def _write(self, params: tuple) -> None:
        """Execute the ``api_request_log`` INSERT on a dedicated pool connection.

        Runs off the request hot path via ``asyncio.create_task`` (#262). Acquires
        its own connection from the pool (never a request-scoped one, which may
        already be released). Best-effort: any failure is swallowed and logged so
        observability can never break — or, here, outlive — the request.
        """
        try:
            pool = db.get_pool()
            async with pool.acquire() as conn:
                await conn.execute(_INSERT_SQL, *params)
        except Exception:  # observability must never break the request
            logger.warning("api_request_log capture failed", exc_info=True)
