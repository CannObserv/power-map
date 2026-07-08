---
title: Client API request/response observability in the Activity dashboard
date: 2026-07-03
issue: 260
status: approved
---

# Client API request/response observability

## Problem

An active client (the sibling USA-WA project) now depends on the public API —
submitting content through the `POST /*/observations` endpoints and tracking
updates through the `GET /api/v1/changes` feed. We currently persist **nothing**
about that traffic:

- No request-logging middleware and no access-log table exist.
- Observation dispositions (`new` / `auto-attached` / `rejected`) are computed
  and returned to the client but never stored.
- `api_keys` records only `last_used_at`, not per-request history.

So there is no way to debug the client's integration, monitor operational
health, or audit what content the client is producing. "Observability of
requests/responses" therefore requires building a **capture layer first**, then
admin screens + a dashboard panel on top of it.

## Goals (from the interview)

1. **Debug client integration** — inspect individual requests, payloads, and the
   exact reason an observation was rejected.
2. **Monitor operational health** — volume, error/rejection rates, latency,
   liveness/last-seen per key.
3. **Audit editorial output** — tie requests to the entities they created or
   attached to.

Explicitly *not* a goal: SLA/liveness proof as a shareable artifact.

## Approved approach

### Capture — pure-ASGI middleware (Approach A)

A single pure-ASGI middleware registered on the app tees the request body (in)
and response body (out), times the call, and writes exactly one
`api_request_log` row per `/api/v1/*` request.

- **Why pure-ASGI, not `BaseHTTPMiddleware`:** teeing both bodies without
  breaking downstream `.json()` reads requires wrapping `receive`/`send` at the
  ASGI layer. `BaseHTTPMiddleware`'s request/response objects make safe
  body-teeing awkward. This is the one fiddly piece; it is a well-trodden
  pattern.
- **Identity without re-hashing:** the auth deps (`require_key` /
  `require_api_key` in `src/api/public/deps.py`) stash
  `request.state.api_key_id`. The middleware reads it *after* `call_next`. For
  requests that never authenticate (401/403), `api_key_id` is `NULL` and the row
  is still written (useful for debugging auth failures).
- **Domain enrichment:** for the two known `route_group`s the middleware parses
  the already-buffered JSON response to extract structured columns —
  observations → `disposition` / `result_entity_id` / `reason`; changes →
  `item_count` / `is_empty`. All other v1 traffic logs generic metadata only.
- **Breadth:** all `/api/v1/*` traffic is captured (future endpoints covered
  automatically); the Activity screens default-filter to
  `route_group IN ('observations','changes')` with an "all endpoints" toggle.
- **Write path:** one `INSERT` per request, scoped to `/api/v1/*` only
  (admin/static never touch the table). Originally an inline write; **#262 moved
  it off the request hot path** to a fire-and-forget `asyncio.create_task` write
  (params built synchronously on the tail, INSERT scheduled on a background
  task), so it no longer adds to request tail latency.

**Rejected alternatives:**

- **B — per-endpoint `record_request(...)` helper.** Richest domain context and
  no body-teeing, but every future endpoint must remember to call it; cannot
  satisfy the "all v1 automatically" decision.
- **C — FastAPI dependency.** Clean key access, but dependencies run before the
  response exists, so status/latency/response-body capture is not clean.

### Fidelity — hybrid, single 90-day window

Store structured metadata columns **and** the raw request/response JSON bodies,
all under one 90-day retention window (consistent with the existing outbox /
tombstone TTL). Observation payloads carry PII (names, addresses, contacts); the
90-day window bounds that footprint the same way every other recent-history
surface is bounded, and PII is confined to the detail screen (list shows
metadata only).

### `/changes` polls — log all, flag empties

Every poll is recorded (cursor in, count out, latency). Zero-result polls are
tagged `is_empty = TRUE` so the UI can collapse them by default while retaining
the full liveness signal for health monitoring.

## Data model

New table, one row per `/api/v1/*` request. `BIGSERIAL` PK — cheap, monotonic,
mirrors the `entity_changes` outbox pattern.

```sql
CREATE TABLE IF NOT EXISTS api_request_log (
    id            BIGSERIAL   PRIMARY KEY,
    occurred_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    api_key_id    TEXT        REFERENCES api_keys(id) ON DELETE SET NULL,  -- NULL = unauthenticated/invalid
    method        TEXT        NOT NULL,
    path          TEXT        NOT NULL,
    route_group   TEXT        NOT NULL,          -- 'observations' | 'changes' | 'other'
    entity_type   TEXT,                          -- observations: people|orgs|jurisdictions
    status_code   INT         NOT NULL,
    latency_ms    INT         NOT NULL,
    disposition   TEXT,                          -- observations: new|auto-attached|rejected
    result_entity_id TEXT,                       -- observations: matched/created entity (no FK — may be deleted/merged)
    reason        TEXT,                          -- rejection/error reason code
    item_count    INT,                           -- changes: rows delivered (0 = empty poll)
    is_empty      BOOLEAN     NOT NULL DEFAULT FALSE,  -- changes: zero-result poll flag
    client_ip     TEXT,
    user_agent    TEXT,
    request_body  JSONB,                         -- raw; NULL for GET/changes
    response_body JSONB                          -- raw
);

CREATE INDEX idx_arl_occurred       ON api_request_log (occurred_at DESC);
CREATE INDEX idx_arl_key_occurred   ON api_request_log (api_key_id, occurred_at DESC);
CREATE INDEX idx_arl_group_occurred ON api_request_log (route_group, occurred_at DESC);
-- partial index for the "problems" view (rejections + 4xx/5xx)
CREATE INDEX idx_arl_problems ON api_request_log (occurred_at DESC)
    WHERE status_code >= 400 OR disposition = 'rejected';
```

Key decisions:

- **`result_entity_id` has no FK** — the referenced entity may be hard-deleted or
  merged; the historical log record must survive. The UI resolves it to a link
  and shows "(removed)" on a 404.
- **`route_group`** is derived in the middleware from the path prefix so the
  common filters and the dashboard aggregates are index-only (no `LIKE` scans).
- **`is_empty`** is the explicit empty-poll flag; the UI filters on it.
- **Retention** — extend `src.core.maintenance` + `scripts/prune_outbox.py` to
  also delete `api_request_log` rows older than 90 days, under the existing
  daily `power-map-prune.timer`. One window, everything consistent.

## Screens & affordances

All under the existing `Activity` nav (`/admin/activity/`), plus a dashboard
panel.

### 1. Activity landing — new card

An "API Requests" card alongside "Import History" → `/admin/activity/requests/`.
Subtitle shows a live pulse: "N requests · M rejections (24h)".

### 2. Request log — `/admin/activity/requests/`

The workhorse. Filterable table matching the existing admin list pattern (HTMX
pagination, keyset on `id DESC`):

- **Stats strip** (top): last-24h / 7d toggle — total, observations by
  disposition (new / attached / **rejected**), changes polls + rows delivered,
  error rate, distinct active keys. One aggregate query.
- **Filters**: route group (default observations + changes), key (by **label**,
  never hash), status class (2xx/4xx/5xx), disposition, time range, free-text on
  `reason` / `path`.
- **Empty-poll collapse**: `is_empty` polls hidden by default; a "show N empty
  polls" toggle re-includes them.
- **Row**: time · key label · method+path · status pill · disposition badge ·
  latency · entity link. Rejections/errors row-tinted (danger).
- **Deep-link**: filters live in query params so a filtered view is
  shareable/bookmarkable for debugging hand-off.

### 3. Request detail — `/admin/activity/requests/{id}/`

Full metadata; pretty-printed request + response JSON (collapsible, monospace);
`result_entity_id` resolved to a clickable entity link with "(removed)"
fallback; resolved API key label + scopes; copy-body button; prev/next nav
within the active filter.

### 4. Dashboard panel — on `/admin/`

Compact "API Activity (24h)" card: total requests, observations split
new/attached/rejected, changes polls + rows delivered, error rate, most-recent
request timestamp. Links into the log. Single `fetchrow` of aggregates,
consistent with the existing dashboard counts.

### Best-practice affordances baked in

- Rejections/errors are the **visual priority** (tinted, one-click filter) —
  debug is the #1 goal.
- Empty polls hidden but recoverable.
- Keys shown by **label**, never by hash.
- **PII confined** to the detail view (list is metadata-only); detail inherits
  admin auth — no new exposure surface.
- Default the log to `route_group IN ('observations','changes')` with an "all
  endpoints" toggle.
- **No live auto-refresh/websockets** — a manual "refresh" button only.

## Testing strategy (TDD, red → green)

- **Middleware unit** — pure-ASGI capture writes a row with correct
  `route_group` / `status` / `latency` / `api_key_id`; body-teeing does not break
  downstream `.json()`; 401/403 logs with a null key.
- **Enrichment** — observation response → `disposition` / `result_entity_id` /
  `reason`; changes response → `item_count` / `is_empty`.
- **Schema** — table + indexes apply; `ON DELETE SET NULL` on key removal; prune
  deletes >90d rows (extend the existing `test_maintenance`).
- **Admin routes** — list filters (group/key/status/disposition/empty); detail
  renders bodies + resolves the entity link + "(removed)" fallback; auth
  redirect when exe.dev headers absent; dashboard panel aggregates.
- Integration tests use `TEST_DATABASE_URL` + the session-scoped `db_pool`
  fixture per `docs/CONVENTIONS.md`.

## Out of scope (YAGNI)

- Websocket / live-tail.
- Per-key rate-limit enforcement or alerting.
- Time-series charts / graphs (the stats strip is numeric counters only).
- CSV export.
- ~~Buffered async log writer (revisit only if poll volume bites).~~ **Shipped in
  #262** as a fire-and-forget writer (off the hot path); a *buffered/batched*
  writer remains out of scope unless volume bites further.
- Logging non-`/api/v1/*` traffic.

## Files touched

- `src/core/schema.sql` — `api_request_log` table + indexes (then `apply_schema`).
- `src/api/public/middleware.py` (new) — pure-ASGI capture + enrichment.
- `src/api/public/deps.py` — stash `request.state.api_key_id` in the auth deps.
- `src/api/main.py` — register the middleware.
- `src/core/maintenance.py` + `scripts/prune_outbox.py` — extend prune to
  `api_request_log`.
- `src/api/admin/activity_requests.py` (new) + templates under
  `src/templates/admin/activity/` — list + detail screens.
- `src/api/admin/activity.py` + `src/templates/admin/activity/index.html` — new
  card.
- `src/api/admin/dashboard.py` + `src/templates/admin/dashboard.html` — panel.
- `docs/` — CONVENTIONS (log table + capture contract) and STYLE (new screens).
