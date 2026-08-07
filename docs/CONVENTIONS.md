# power-map — API & Ingestion Conventions

Public API request/response contracts, the API request log, ingestion patterns, and
the dry-run discipline every operational script follows. Database tables and
invariants live in `docs/SCHEMA.md`, write semantics in `docs/OBSERVATIONS.md`,
person-name rules in `docs/NAMES.md`, endpoint behaviour in `docs/PUBLIC_API.md`.

---

## Public API



- Auth: `X-API-Key` header → `require_api_key` dep (in `src.api.public.deps`); 403 on missing, 401 on invalid, 429 on rate limit (#292 — per-key read/write token buckets in `src.api.public.ratelimit`, checked in `_resolve_api_key`; per-worker, env-tunable via `RATE_LIMIT_*`); updates `api_keys.last_used_at` (debounced, `API_KEY_LAST_USED_DEBOUNCE_S`, default 60s)
- Versioning: path-based (`/api/v1/`). Bump the prefix when introducing breaking changes
- Response models: all routes must declare a Pydantic `response_model` (in `schemas.py`) and an explicit `operation_id`; `dict[str, Any]` return types are not allowed — OpenAPI schema must be fully typed
- List endpoints: return `{"data": [...], "meta": {"limit", "offset", "count", "has_more"}}`; fetch `limit + 1` rows to compute `has_more` without a `COUNT(*)` query; return only `limit` rows in `data`
- Stable pagination: every paginated `ORDER BY` **must end with a unique column** (usually the PK, e.g. `, id` / `, o.id`). Ordering by non-unique keys alone (name, rank, `created_at`) lets Postgres return tied rows in a different order per query, so offset windows overlap and gap — consumers paging the full list silently skip and duplicate rows (#297). Append the tiebreaker even when a partial unique index makes ties rare (archived rows, jurisdictional variants), since offset pagination must be total-ordered across *all* returned rows
- Single-resource endpoints: return the resource object directly (no envelope)
- Timestamps: use `datetime` fields in Pydantic response models + `@field_serializer` calling `fmt_ts()` from `schemas.py`; ISO 8601 with `Z` suffix; never pre-serialize as `str` in handlers
- CORS: not required — the public API is server-to-server only (no browser callers)
- Conditional GET (#292/#392): everything a route needs lives in `src/api/public/etag.py` — `make_etag` (strong detail tag `"<id>-<updated_at_ms>"`), `cache_headers` (ETag + `Cache-Control: no-cache` + `Vary: X-API-Key`, `Last-Modified` only when there is one) via `http_date` (`email.utils.format_datetime(…, usegmt=True)` — **never** `strftime("%a, %d %b …")`: `%a`/`%b` are locale-dependent, so one `setlocale(LC_TIME, …)` in-process would emit an invalid HTTP-date from every conditional GET at once; naive input is stamped UTC, offset-aware input converted rather than relabelled `GMT`), `NOT_MODIFIED` (the `responses=` 304 declaration), and `conditional_response(request, response, etag, last_modified)` — the one call a route makes: returns a 304 `Response` to return immediately, or None having stamped the headers. **A route must never read the `if-none-match` header itself.** Raw equality against the whole header (the pre-#392 shape at all seven sites) fails a comma-separated list, a `W/`-prefixed weak tag, and `*`; `If-None-Match` on GET is compared **weakly**, so `W/"x"` matches `"x"`, and a comma inside a quoted tag is not a separator. Partial adoption is worse than none — a client that learns list syntax works on `/people/{id}` but not elsewhere is a nastier bug than uniform strictness — so `tests/api/public/test_conditional_get.py` sweeps `src/api/public/*.py` (AST) for any module other than `etag.py` **reading** the header — `headers.get("if-none-match")` args and `headers["if-none-match"]` subscripts; prose mentions in a docstring or comment are fine, and indirection through a constant would slip past (ratchet, not proof). Two validator shapes, no third — both in `etag.py`: **watermark** `collection_etag(prefix, count, last, *params)` (`count(*)` + `max(updated_at)` over the *visible* set; count catches a row entering/leaving the filtered set — a retract **archives**, so the row's own bump is invisible once the active-only filter excludes it — and max catches an in-place edit; every filter *and* the `limit`/`offset` window is baked in, `-` escaped per param so adjacent values can't fuse) for a table carrying `updated_at`; **content hash** `catalog_validator(rows)` (sha256 over `repr(tuple(row.items()))` per row — keys included so a column rename is caught, `repr` keeps `None`/`""` and `True`/`1` distinct, row order significant) for a small fully-materialized resource whose table has none. The aggregate **must** use the same `WHERE` as the body minus LIMIT/OFFSET. The hash saves serialization + transfer, not the query — on a catalog of tens of rows that is the whole win. Search/list endpoints deliberately have none (a validator over a filtered, paginated, ranked set costs about as much as serving it); `/changes` is a cursor feed and needs none. A mutable table with no `updated_at` cannot host a watermark at all — `jurisdiction_relationships` shipped that way and #392 added the column + `trg_updated_at_jurisdiction_relationships` (with an idempotent `ADD COLUMN IF NOT EXISTS` → backfill from `COALESCE(superseded_at, created_at)` → `SET DEFAULT`/`SET NOT NULL` reconciliation, per the #315 drift rule; a straight `ADD COLUMN ... NOT NULL DEFAULT NOW()` would stamp every historical row with the deploy time). **Ordering rule (CR #392/11):** when a table gains both a backfilled column and a `set_updated_at()` trigger, the reconciliation must sit **before** the trigger in `schema.sql` — `apply_schema` runs the file top-to-bottom in one transaction and the BEFORE-UPDATE trigger overwrites `NEW.updated_at` with NOW() unconditionally, so a trigger created first silently clobbers the backfill into the deploy timestamp. A fresh-DB test cannot catch this (the inline column makes the backfill a zero-row no-op) — `tests/core/test_schema_jurisdiction_rel_updated_at.py` rebuilds the pre-migration shape and replays the real statements in file order. A traversal endpoint (`/jurisdictions/{id}/lineage`) takes the content hash over its **result** and deliberately omits the `depth` param — the hash already tracks what was returned, so baking depth in would only manufacture misses. New conditional GETs also declare `responses=NOT_MODIFIED` and join `_CONDITIONAL_GETS` in `tests/api/public/test_openapi_responses.py`. A 304 still consumes a rate-limit token (the limiter runs in the auth dep, ahead of the handler)
- Health probes (#343): `GET /health` (liveness — `{"status", "build"}`, no external calls) and `GET /ready` (readiness — bounded pool acquire + `SELECT 1`, 503 with generic reason slug `no_pool` / `pool_timeout` / `db_error` on failure) live at root level in `src/api/health.py`, **outside** `/api/v1`: unauthenticated, exempt from `RequestLogMiddleware` and rate limiting (both `/api/v1`-scoped). `/ready` is the sole sanctioned exception to the `Depends(get_db)`-only rule — it calls `db.check_ready()` directly because a failing `Depends` surfaces as 500, not a catchable 503, and the probe must exercise the real pool. The bounded acquire **and probe query** (`READY_ACQUIRE_TIMEOUT_S`, 2s each — worst-case probe latency ~2×) are load-bearing: a bare acquire on an exhausted pool hangs forever, and an idle pooled connection acquires instantly then an unbounded `SELECT` hangs on a wedged DB; `pool_timeout` covers either timing out — never remove the `fetchval` timeout as "unspecified". Never leak DB error text on these unauthenticated routes — detail goes to the log. App version is derived once in `health.py` (`importlib.metadata.version("power-map")`) and feeds both `FastAPI(version=...)` and `/health.build`; never hardcode it (the `check-version-sync` hook only guards `pyproject.toml` ↔ `package.json`). Per-worker caveat: with `--workers 2`, each worker has its own pool — a probe samples whichever worker accepted.

### Public API key rules at a glance

One-line-per-rule index of this section — read it first, then the subsection
a rule names. Where an entry ends `Full rules → …`, the target is a
subsection of this same section.

- Auth deps (all from `src.api.public.deps`): `require_api_key` (read-only, returns `user_id`); `require_key` (returns `AuthedKey(user_id, key_id)` — use when handler needs the key_id, e.g. subscription join); `require_scope("scope:id")` (write, enforces scope); 403 on missing/insufficient scope or absent header, 401 on invalid key
- Lists: `{"data": [...], "meta": {"limit", "offset", "count", "has_more"}}`; fetch `limit+1` rows for `has_more`. Every paginated `ORDER BY` **must end with a unique column** (usually the PK) — otherwise offset windows over tied rows skip and duplicate (#297)
- Timestamps: `datetime` fields in response models + `@field_serializer` calling `fmt_ts()` from `schemas.py`; ISO 8601 with `Z` suffix; never pre-serialize as `str` in handlers
- Conditional GET (#292/#392): all of it lives in `src/api/public/etag.py`. A route calls `conditional_response(request, response, etag, last_modified)` and returns its result when non-None; it **never reads the `if-none-match` header itself**. Two validator shapes only — `collection_etag` (watermark) where the table has `updated_at`, `catalog_validator` (content hash) where no single one covers the rows; the aggregate must share the body's `WHERE`, and the validator is computed **before** the body. New ones declare `responses=NOT_MODIFIED` + join `_CONDITIONAL_GETS`. Three sweeps in `tests/api/public/test_conditional_get.py` enforce all of this. Full rules → `docs/CONVENTIONS.md` §"Public API"
- Assignment observations (#311): `(person, role, start_date)` is identity, not payload — PM-native (`pm_assignment_id`) observations **update in place** (`update_assignment_fields`: move start, explicit `end_date: null` clears, tri-state `is_current`); natural-key auto-attach applies only the open-tenure close, other deltas come back in `unapplied`. Provenance: `role_assignments.source_key_id` stamped on NEW, updates gated to same-or-NULL source. Dup cleanup: `scripts/audit_assignment_duplicates.py`. **Retract (#391):** `op="retract"` archives the `pm_assignment_id` tenure → new `retracted` disposition — the correction for a produced *artifact* (never happened), which closing can't express and un-producing only orphans. Always id-addressed (`invalid` otherwise); `person_id`/`role_id` must match (`identity_immutable`); same-or-NULL provenance (`source_key_mismatch`); payload + ancillary ignored; re-emit is a quiet `auto-attached` no-op (lookup deliberately **not** archived-filtered, unlike `update_assignment_fields`, and not routed through `resolve_entity`). **Authoritative** — both create doors skip an archived twin instead of resurrecting (`resolve_assignment` + the embedded `write_role_assignments`), so a re-emitting producer can't defeat the retract; that attach **writes nothing** (bound deltas *and* ancillary withheld, every name echoed in `unapplied` — reported even when the value equals what the archived row stores, unlike the active path); un-retract = admin unarchive only, no `archived:false` verb. Archiving cascades free: outbox emit + `staff_of` edges archive (#301). Full rules → `docs/OBSERVATIONS.md` §"Assignment observations — update semantics & provenance".
- Event observations (#321): `(event_type, linked_entity)` is identity, `date`/`notes`/`place`/`visibility` mutable — a `pm_event_id` observation **refines in place** (immutable identity → `identity_immutable`; narrow diff-before-write no-op; `source_key_id` same-or-NULL gate → `provenance_conflict`). Embedded (`events: [...]`) is all-or-nothing; the event-native `POST /orgs/{id}/events/observations` is **partial-success** (per-event savepoint → per-event disposition + reason slug; one transient `linked_entity_unresolved` gives ordering-tolerance). `succeeded_by` = renamed-continuity link on the predecessor. **Retract (#322):** `op="retract"` archives the `pm_event_id` event (`archived_at`, never hard-delete) — the only correction for a dateless linked event, so a re-link is create-new + retract-old; always id-addressed, `event_type` must match (`identity_immutable`), same-or-NULL provenance, already-archived re-emit is a `auto-attached` no-op (no clock bump), archiving fires the outbox so subscribers drop the anchor. Full rules → `docs/OBSERVATIONS.md` §"Event observations — refine-in-place, partial-success, `succeeded_by` & retract".

---

## API request log (#260)


`api_request_log` is an append-only observability record of public API traffic —
one row per `/api/v1/*` request. It backs the admin **Activity › API Requests**
screens and the dashboard API-activity panel.

- **Capture** — `src.api.public.middleware.RequestLogMiddleware`, a **pure-ASGI**
  middleware (not `BaseHTTPMiddleware`, so it can tee request+response bodies
  without breaking downstream `.json()` reads). Registered in `src/api/main.py`
  via `app.add_middleware`. Non-`/api/v1/*` paths early-return uncaptured.
- **Identity** — read from `request.state.api_key_id`, which the auth deps
  (`require_api_key` / `require_key` / `require_scope`) stash on a successful key
  resolve. Unauthenticated/invalid requests (401/403) log a row with a NULL key.
- **Enrichment** — for the `observations` and `changes` route groups only, the
  middleware parses the response body into structured columns (`disposition`,
  `result_entity_id`, `entity_type`, `reason`; `item_count`, `is_empty`). Any
  other group, or a non-JSON body, logs generic metadata only.
- **Fidelity / PII** — raw request/response JSONB bodies are stored **only for
  the `observations` and `changes` groups** (the ones the log surfaces); all
  other v1 traffic records structured metadata only and its bodies are never even
  buffered (avoids memory + JSONB bloat from e.g. large embedding vectors).
  Observation payloads carry PII (names, addresses, contacts); the 90-day
  retention window bounds that footprint, and the admin list surface shows
  metadata only — bodies are confined to the detail view (admin-authed).
- **`result_entity_id` has no FK** — the referenced entity may be hard-deleted or
  merged; the log record must survive. The UI resolves it to an admin link and
  shows "(removed)" on a 404.
- **Best-effort** — a capture failure is swallowed and logged; observability must
  never break the request path. The row write is **fire-and-forget** (#262):
  params are built on the request tail, then the pool-acquire + INSERT run on a
  background `asyncio` task so capture never adds to request latency. Graceful
  shutdown drains in-flight writes before the pool closes (#286,
  `drain_pending_writes`); a hard crash (SIGKILL/OOM) drops them. Under load the
  in-flight set is **bounded** by `API_REQUEST_LOG_MAX_PENDING` (#290): above the
  cap, incoming writes are *shed* (drop-newest, keeping the older writes closest
  to landing) rather than piling onto `pool.acquire()`. So `api_request_log` can
  legitimately under-count during sustained high traffic even with zero errors —
  shed volume is counted and surfaced via a rate-limited `WARNING`.
- **Retention** — pruned on the same 90-day window as the outbox by
  `scripts/prune_outbox.py` (see `docs/COMMANDS.md`).
- **Anomaly surfacing** (#294) — per-key aggregates via
  `src.core.anomaly.key_activity` back both the admin per-key panel and the
  hourly `power-map-anomaly.timer` check (`scripts/check_api_anomalies.py`,
  WARNING + exit 3 at/above `API_ANOMALY_HOURLY_THRESHOLD` req/hr, `<= 0`
  disables; see `docs/COMMANDS.md`). Threshold is deliberately below the
  rate-limit ceiling — rationale in `src/core/anomaly.py`.

---

## Ingestion


- EVTL pattern: Extract (CSV read) → Validate (Pydantic) → Transform (normalize fields) → Load (DB insert)
- `RowResult` envelope: `errors` = fatal (entity skipped), `warnings` = non-fatal (field skipped)
- `field_confidence` is append-only; query latest with `ORDER BY assessed_at DESC LIMIT 1`
- `import_batches.file_hash` is unique; re-running with same files reuses the existing batch
- Address standardization uses the external address-validator service when `ADDRESS_VALIDATOR_API_KEY` is set; falls back to local `usaddress` parsing otherwise
- `addresses.precision` indicates the specificity tier of the geocoded result (`street`, `postal`, `city`, `region`, `country`; NULL = unset or pre-geocoding historical record). Event place linkage (`entity_events.event_place_address_id`) requires city-level or finer (`city`, `postal`, `street`) — or NULL; `country`/`region` precision is rejected. See `EVENT_PLACE_PRECISIONS` in `src/core/types.py`.
- `VALIDATE_ADDRESSES=true` (or `--validate-addresses` CLI flag) enables the `/validate` endpoint
- `ImportConfig.local_addresses_only=True` (#402) forces local `usaddress` parsing regardless of `ADDRESS_VALIDATOR_API_KEY` — standardization otherwise fires whenever the key is set, so this is the only lever that keeps a run off the external service. Set by `import_cannabis_observer.py` on a dry run so a preview does not spend the rate-limited quota; address fields in a preview may therefore differ from a committed run
- `role_index` is pre-populated from the DB at pipeline startup (Pass 3) so re-runs are idempotent across batches

---

## Operational scripts — dry run by default & target echo (#402)


`DATABASE_URL` comes from `/etc/power-map/.env` and resolves to **production**
from any directory — main checkout, worktree, anywhere on the VM. Nothing about
a `scripts/…` invocation signals that. Two rules follow, and they are separate
concerns: the gate stops an unintended write, the echo makes an intended one
attributable afterwards.

**Every script that writes gates the write behind `--execute`.** The bare
invocation is read-only and reports what would change. #398 fixed
`apply-schema.sh`, the one script that wrote unconditionally; #402 found two
more (`import_cannabis_observer.py`, `seed_locales_scripts.py`) and closed
them. The convention was believed universal before that and was enforced by
nothing — #399's AST sweep is what makes it stop depending on memory.

**Every script echoes its target before connecting**, via `add_dsn_args()` +
`resolve_dsn()` from `scripts/_dsn.py`:

```
target: co_pm_db_production_user@co-pm-db-1-….ondigitalocean.com:25060/co_pm_db_production (production)
```

The label is derived by matching `(host, port, dbname)` — **not** the DSN
string. Production is reached as two different users (`DATABASE_URL` as the app
user, `MIGRATIONS_DATABASE_URL` as the migrations user); string equality would
label a migrations DSN `unknown`. Anything unmatched is
`unknown — assume production`, never `test`: the consequence of guessing wrong
runs one way.

The uniform flags (#399):

| Flag | Effect |
|---|---|
| *(none)* | `DATABASE_URL` — production |
| `--database-url DSN` | that DSN |
| `--test` | `TEST_DATABASE_URL`; **hard-errors when unset** — never falls back to `DATABASE_URL`, which would be a production write dressed as a test write |

**Resolve last.** The echo means "about to connect", so `resolve_dsn` goes
*after* any input validation that can abort the run — otherwise the journal
records a database the run never opened, which is the false attribution the
echo exists to prevent. `check_api_anomalies` extends this to its
`threshold <= 0` short-circuit: a disabled run resolves nothing.

Passing `--test` and `--database-url` together is an error, not a precedence
rule. A script whose target flags are domain-named (`audit_schema_constraint_parity`
takes `--target-url` / `--reference-url`) uses `default_dsn()` for the default
and calls `echo_target(..., role=…)` per connection, so each gets its own line.

`redact_dsn()` drops the password *and* the query string, and returns `None`
for anything that is not a parseable URL. **Callers never fall back to printing
the raw string**: `urlparse` hands back a libpq keyword/value DSN
(`host=… password=…`) with the credentials in `path`, so a "best effort" echo
would put the password in the journal. An absent database name renders `?`.

Two shapes of dry run, both legitimate:

| Shape | Used by | Note |
|---|---|---|
| Read-only preview | `seed_locales_scripts.py`, the `audit_*` scripts | Classify against current state; write nothing |
| Real work, rolled back | `import_cannabis_observer.py` | The summary printed is the summary `--execute` produces |

The rolled-back shape has one trap: **side effects outside the transaction do
not roll back.** The importer parses addresses locally on a dry run
(`ImportConfig.local_addresses_only`) rather than spending the rate-limited
external validator's quota on a run that changes nothing — standardization
fires whenever `ADDRESS_VALIDATOR_API_KEY` is set, independent of
`--validate-addresses`, so that flag is the only lever. The cost is that
address fields in a preview may differ from a committed run; the dry-run notice
says so.

Schema DDL is never implicit. `scripts/apply-schema.sh` owns applying
`schema.sql` and carries the #398 production guards; the importer's
`--apply-schema` is opt-in and requires `--execute`, because DDL inside a run
about to be rolled back would be a lie.

All three rules are enforced by `tests/scripts/test_dsn_sweep.py`, an AST sweep
over every `scripts/*.py`: it connects ⇒ goes through `_dsn.py`; nobody reads
`DATABASE_URL` directly; write SQL ⇒ declares `--execute`. **It has no
allowlist** — an exemption set is a place for a live script to hide. If a new
script genuinely cannot comply, change the sweep with a reason in the diff.

`apply-schema.sh` deliberately keeps its **own copy** of the redaction logic
rather than importing `_dsn.py` — it runs as `ExecStartPre` on the systemd
unit, where an import failure would mean a failed production restart. The two
copies are pinned in agreement by
`tests/scripts/test_dsn.py::test_redaction_matches_apply_schema_sh`.
