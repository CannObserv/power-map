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
"""

import json
import time

import src.core.db as db
from src.core.logging import get_logger

logger = get_logger(__name__)

_V1_PREFIX = "/api/v1"

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

        request_chunks: list[bytes] = []

        async def receive_wrapper():
            message = await receive()
            if message["type"] == "http.request":
                request_chunks.append(message.get("body", b""))
            return message

        status_code = 500
        response_chunks: list[bytes] = []

        async def send_wrapper(message):
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
            elif message["type"] == "http.response.body":
                response_chunks.append(message.get("body", b""))
            await send(message)

        start = time.perf_counter()
        try:
            await self.app(scope, receive_wrapper, send_wrapper)
        finally:
            latency_ms = int((time.perf_counter() - start) * 1000)
            try:
                await self._record(scope, request_chunks, status_code, response_chunks, latency_ms)
            except Exception:  # observability must never break the request
                logger.warning("api_request_log capture failed", exc_info=True)

    async def _record(self, scope, request_chunks, status_code, response_chunks, latency_ms):
        path = scope.get("path", "")
        method = scope.get("method", "")
        state = scope.get("state") or {}
        api_key_id = state.get("api_key_id")
        route_group = route_group_for_path(path)

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

        response_raw = b"".join(response_chunks)
        request_body = _body_to_jsonb_param(b"".join(request_chunks))
        response_body = _body_to_jsonb_param(response_raw)

        parsed = _safe_json(response_raw) if route_group in ("observations", "changes") else None
        enriched = _enrich(route_group, parsed)

        pool = db.get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                _INSERT_SQL,
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
