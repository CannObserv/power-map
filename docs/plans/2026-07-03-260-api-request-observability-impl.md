---
title: "#260 — Client API request/response observability — implementation plan"
date: 2026-07-03
status: draft
---

# #260 — Client API request/response observability — implementation plan

Design: `docs/plans/2026-07-03-api-request-observability-design.md` (approved).

## Problem

The public API's observation write endpoints and `/changes` feed now have an
active client (USA-WA), but the service persists nothing about that traffic —
no request log, no stored dispositions, only `api_keys.last_used_at`. There is
no way to debug the integration, monitor health, or audit editorial output. The
design is approved; this plan sequences the build as independently-verifiable
TDD steps.

## Approach

Build bottom-up so each layer is testable before the next depends on it: schema
first, then the identity hook, then the pure-ASGI capture middleware (generic),
then domain enrichment, then retention, then the admin surfaces (list → detail →
card → dashboard panel), then docs. Each step is red→green with tests committed
alongside. All work in the `feature/260-api-request-observability` worktree; dev
verification on port 8001 from the worktree.

## Tradeoffs / alternatives

- **Build screens before capture** — rejected; screens have nothing to render
  and can't be tested without the table + middleware producing rows.
- **One giant commit** — rejected; the layers have clean seams (schema, deps,
  middleware, enrichment, prune, screens, docs) that each carry their own tests.
- **Skip the deps `request.state` hook, re-hash the key in middleware** —
  rejected; duplicates the auth lookup on every request and diverges from the
  single source of truth for key resolution.

## Steps

1. **Schema** — add `api_request_log` (BIGSERIAL PK + columns + four indexes per
   design) to `src/core/schema.sql`; apply to dev DB via `apply_schema`. *Verify:*
   schema test asserts table + indexes exist and `ON DELETE SET NULL` on
   `api_key_id`; `apply-schema.sh` runs clean.

2. **Identity hook** — `require_key` / `require_api_key` set
   `request.state.api_key_id` after resolving the key. *Verify:* unit test that
   each dep populates `request.state.api_key_id` (and leaves it unset on auth
   failure).

3. **Capture middleware (generic)** — new `src/api/public/middleware.py`,
   pure-ASGI, tees request+response bodies, times the call, writes one
   `api_request_log` row for `/api/v1/*` only; derives `route_group` from the
   path prefix; registers in `src/api/main.py`. *Verify:* tests assert a row with
   correct method/path/route_group/status/latency/api_key_id; downstream
   `.json()` still works (body-tee); non-`/api/v1/*` writes nothing; 401/403 logs
   a null-key row.

4. **Domain enrichment** — extend the middleware to parse buffered responses for
   the two known groups: observations → `entity_type` / `disposition` /
   `result_entity_id` / `reason`; changes → `item_count` / `is_empty`. *Verify:*
   tests drive real observation (new/attached/rejected) + changes (non-empty /
   empty) responses and assert the extracted columns.

5. **Retention** — extend `src/core/maintenance.py` + `scripts/prune_outbox.py`
   to also prune `api_request_log` older than the 90-day window. *Verify:*
   extend `test_maintenance` — >90d rows deleted, recent rows kept, dry-run
   counts include the table.

6. **Request-log list screen** — `src/api/admin/activity_requests.py` +
   template: `GET /admin/activity/requests/` with stats strip, filters (group /
   key label / status class / disposition / time range / free-text), empty-poll
   collapse, keyset pagination on `id DESC`; mount in the admin router. *Verify:*
   route tests for each filter, empty-poll default-hidden + toggle, auth redirect
   when exe.dev headers absent.

7. **Request detail screen** — `GET /admin/activity/requests/{id}/`: metadata,
   pretty request/response JSON, `result_entity_id` resolved to an entity link
   with "(removed)" fallback, key label + scopes, prev/next within filter.
   *Verify:* detail renders bodies; entity link resolves and falls back on 404;
   404 for unknown id.

8. **Activity card + dashboard panel** — add the "API Requests" card to
   `activity.py` / `activity/index.html`, and the "API Activity (24h)" panel to
   `dashboard.py` / `dashboard.html`. *Verify:* card subtitle + panel aggregates
   (totals, dispositions, changes polls/rows, error rate, last request) match
   seeded rows.

9. **Docs + version** — update `docs/CONVENTIONS.md` (log table + capture
   contract, PII/retention note) and `docs/STYLE.md` (new Activity screens);
   bump `pyproject.toml` + `package.json` together. *Verify:* full suite green
   (`uv run pytest --no-cov -q`), pre-commit clean.

10. **Manual smoke** — dev server on 8001 from the worktree; submit an
    observation + poll `/changes` against dev; confirm rows appear in the list,
    detail renders, entity link works, dashboard panel updates. *Verify:*
    checklist walked in the browser at `https://power-map.exe.xyz:8001/admin/`.

## Open questions / risks

- **Poll-volume write cost** — inline INSERT per `/changes` poll is fine at
  current volume; the buffered-writer swap is deferred (design §Out of scope).
  Risk is low but flagged; revisit if dev smoke shows latency regression.
- **Body-tee correctness** — the pure-ASGI receive/send wrapping is the one
  fiddly piece; step 3's downstream-`.json()` test is the guard. If a streaming
  or non-JSON response type sneaks into v1 later, enrichment must no-op safely
  (parse guarded by content-type + try/except).
- **`request_body` on rejected/malformed input** — capture the raw bytes even
  when JSON parsing fails so a malformed payload is still inspectable; store as
  `NULL` JSONB only when the body is genuinely empty.
