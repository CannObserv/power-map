# power-map — API, DB & Ingestion Conventions

Reference for public API, database, and ingestion patterns. For admin dashboard and UI patterns, see `docs/STYLE.md §32`.

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
- Conditional GET (#292/#392): everything a route needs lives in `src/api/public/etag.py` — `make_etag` (strong detail tag `"<id>-<updated_at_ms>"`), `cache_headers` (ETag + `Cache-Control: no-cache` + `Vary: X-API-Key`, `Last-Modified` only when there is one) via `http_date` (`email.utils.format_datetime(…, usegmt=True)` — **never** `strftime("%a, %d %b …")`: `%a`/`%b` are locale-dependent, so one `setlocale(LC_TIME, …)` in-process would emit an invalid HTTP-date from every conditional GET at once; naive input is stamped UTC, offset-aware input converted rather than relabelled `GMT`), `NOT_MODIFIED` (the `responses=` 304 declaration), and `conditional_response(request, response, etag, last_modified)` — the one call a route makes: returns a 304 `Response` to return immediately, or None having stamped the headers. **A route must never read the `if-none-match` header itself.** Raw equality against the whole header (the pre-#392 shape at all seven sites) fails a comma-separated list, a `W/`-prefixed weak tag, and `*`; `If-None-Match` on GET is compared **weakly**, so `W/"x"` matches `"x"`, and a comma inside a quoted tag is not a separator. Partial adoption is worse than none — a client that learns list syntax works on `/people/{id}` but not elsewhere is a nastier bug than uniform strictness — so `tests/api/public/test_conditional_get.py` sweeps `src/api/public/*.py` for any module other than `etag.py` mentioning the header. Two validator shapes, no third: a **watermark** aggregate (`count(*)` + `max(updated_at)` over the *visible* set, filter params baked into the tag — `events_collection_validator`) for collections whose table carries `updated_at`, and a **content hash** over the fetched rows for a small fully-materialized resource whose table has none. New conditional GETs also declare `responses=NOT_MODIFIED` and join `_CONDITIONAL_GETS` in `tests/api/public/test_openapi_responses.py`. A 304 still consumes a rate-limit token (the limiter runs in the auth dep, ahead of the handler)
- Health probes (#343): `GET /health` (liveness — `{"status", "build"}`, no external calls) and `GET /ready` (readiness — bounded pool acquire + `SELECT 1`, 503 with generic reason slug `no_pool` / `pool_timeout` / `db_error` on failure) live at root level in `src/api/health.py`, **outside** `/api/v1`: unauthenticated, exempt from `RequestLogMiddleware` and rate limiting (both `/api/v1`-scoped). `/ready` is the sole sanctioned exception to the `Depends(get_db)`-only rule — it calls `db.check_ready()` directly because a failing `Depends` surfaces as 500, not a catchable 503, and the probe must exercise the real pool. The bounded acquire **and probe query** (`READY_ACQUIRE_TIMEOUT_S`, 2s each — worst-case probe latency ~2×) are load-bearing: a bare acquire on an exhausted pool hangs forever, and an idle pooled connection acquires instantly then an unbounded `SELECT` hangs on a wedged DB; `pool_timeout` covers either timing out — never remove the `fetchval` timeout as "unspecified". Never leak DB error text on these unauthenticated routes — detail goes to the log. App version is derived once in `health.py` (`importlib.metadata.version("power-map")`) and feeds both `FastAPI(version=...)` and `/health.build`; never hardcode it (the `check-version-sync` hook only guards `pyproject.toml` ↔ `package.json`). Per-worker caveat: with `--workers 2`, each worker has its own pool — a probe samples whichever worker accepted.

---

## DB

- PKs: ULIDs via `generate_id()` from `src.core.db`
- `apply_schema(conn)` is idempotent (`IF NOT EXISTS` / `ON CONFLICT DO NOTHING`); wraps in a transaction
- `updated_at`: maintained by DB triggers — never set manually in application code
- Phone: normalize to E.164 via `PhoneNormalizer` from `src.core.normalizers.phone`
- Email: validate via `EmailNormalizer` from `src.core.normalizers.email`
- Integration tests (marked `integration`) require `TEST_DATABASE_URL` env var; `tests/conftest.py` redirects `DATABASE_URL` → `TEST_DATABASE_URL` and skips when absent — never runs against the production DB
- Integration test fixtures share a session-scoped `db_pool` (`asyncpg.create_pool`) from `tests/conftest.py`; `apply_schema` runs once at session start. Fixtures and tests acquire via `async with db_pool.acquire() as conn:` — never `await asyncpg.connect(...)` per call. Reference recipe: `tests/api/admin/test_people_names.py`.
  - Required markers in every consumer module: `pytestmark = pytest.mark.integration` at module level, and `@pytest_asyncio.fixture(loop_scope="session")` on every async fixture.
  - Fixture `loop_scope="session"` is load-bearing: `db_pool` is bound to the session event loop. The test-function asyncio mark is no longer needed — `asyncio_default_test_loop_scope = "session"` in `pyproject.toml` sets the default globally.
  - Sole exception: `tests/core/test_db.py` tests `src.core.db.get_pool()` / `create_pool()` lifecycle itself and intentionally owns its own connection.
  - Teardown for entities referenced by a polymorphic side table (e.g. `addresses` via `entity_addresses`): fetch the side-table's foreign-key ids before dropping the join rows, then delete the entity rows guarded by `NOT EXISTS` so a shared row can't FK-fail. Wrap the read+writes in a single `async with conn.transaction():` so a mid-teardown failure rolls back cleanly. Pair with a module-scoped autouse fixture that snapshots the entity table's rowcount before/after and asserts equality — catches leaks if someone later "simplifies" the teardown. Reference: `tests/api/admin/test_orgs_addresses.py` (#150).
- **Endpoint-test client — prefer the lifespan-less rollback client (#288).** A per-test `with TestClient(app)` enters the app lifespan, which calls `asyncpg.create_pool` (~170 ms TCP handshake + type introspection) *per test* — ~30% of integration wall-clock. Instead, drive the app with an `AsyncClient` over `ASGITransport` and override `get_db` to yield a single BEGIN/ROLLBACK-wrapped `db_pool` connection: no lifespan → no per-test pool, and app writes + fixture setup share one transaction that rolls back automatically (no manual `DELETE` teardown). Because the app and the connection run on the same session event loop, this only works with async `AsyncClient`, not sync `TestClient`. The shared `db` connection means read-back assertions see the app's uncommitted writes. Routes that open their own `async with db.transaction()` become savepoints under the outer test transaction — the outer rollback still discards them. Reference recipe: `tests/api/admin/test_orgs.py` / `tests/api/admin/test_orgs_detail_inline.py`.
    - **Pure-unit route tests** (no real DB — `get_db` fully mocked) must construct `TestClient(app)` **without** `with`: no lifespan → no pool → no live-DB dependency. Restore overrides with targeted `app.dependency_overrides.pop(dep, None)`, never `.clear()`. Reference: `tests/api/admin/test_router_ordering.py`.
    - **When rollback is *wrong* — the committing client (#288).** A single wrapping transaction can't serve two kinds of test; these use the lifespan-less **committing** fixtures (`committing_db` / `committing_client` in `tests/api/public/conftest.py`) — autocommit (no wrapping transaction), so still no per-test pool, but writes **commit**. You must restore explicit teardown (or rely on unique ULIDs + the session-start truncation) since nothing rolls back.
        - **Timestamps / etags that must *advance*.** Postgres `now()` / `CURRENT_TIMESTAMP` is fixed at transaction start, so `updated_at` (set by triggers) is identical for every write inside one transaction — the rollback client freezes it. A test asserting an entity's etag *changes* after a mutation needs the two writes in *separate* transactions → committing client. Reference: the etag tests in `tests/api/public/test_orgs.py` (they shadow `db`/`client` with the committing variants at file scope).
        - **A *separate* connection must see the row.** `RequestLogMiddleware` writes its `api_request_log` row on a background task via the module-global pool (`db.get_pool()`), never the request-scoped connection — so it can't see rows created in the rollback client's uncommitted transaction. Such tests need committed data + the real global pool. The public conftest's session-autouse fixture creates the global pool (mirroring the lifespan) precisely for this. Reference: `tests/api/public/test_request_log_middleware.py`.
        - **Assertions must not lean on `created_at` row order.** Same frozen-`now()` root as above, seen from the read side: rows written in one test transaction share an identical `created_at`, so `ORDER BY created_at` returns them in a nondeterministic order and `rows[0]`/`rows[1]` positional asserts flip across runs (flaky in the full suite, passing in isolation — #317, the #297 class from the read side). A ULID `, id` tiebreak only recovers *insertion* order across DB round-trips; names minted back-to-back in one write loop share the same millisecond, so `, id` there is a random coin flip. Key assertions by a stable value (`{r["name"]: r["is_canonical"] for r in rows}`), never by row order. Reference: `tests/core/test_observation_writers.py`.
    - **Pool headroom:** each rollback client holds one `db_pool` connection for the whole test (`DB_POOL_MAX_SIZE=2` in tests). Revisit per-worker sizing before any `pytest-xdist` parallelization (#288).
    - **Read-only sweep variant — seed once per module.** When a suite only *reads* (e.g. renders every admin GET route and asserts on the HTML), scope the `db`/`client`/`seed` fixtures to `module` instead of function: one BEGIN/ROLLBACK connection and one seed serve all parametrized cases, so a 148-route sweep pays the seed cost once. Same rollback isolation as the per-test client — just amortized. Reference: `tests/api/admin/test_a11y_render.py` (the #246 rendered-DOM a11y sweep; checker in `tests/api/admin/a11y.py`). Note `follow_redirects=False` there is deliberate: asserting a direct `200` keeps a route that 3xx's from silently passing by following the redirect elsewhere.

### Display names

- Org: use `v_org_display_names` for all queries displaying an org name — formats as "Name (Acronym)" when a canonical acronym exists, otherwise just "Name". Never join `organization_names` or `organization_acronyms` directly for display
- Person: use `v_person_display_names` — returns the canonical `person_names` row filtered to `visibility='public'`. The view exposes `display_name` (visible string) and `sort_key` (`COALESCE(sort_as, name)`, Phase 2b #123). For person ORDER BY, use `sort_key COLLATE "und-x-icu" NULLS LAST` so diacritics order locale-aware (Å near A) and any `sort_as` override is honored. See "Person names — i18n & cultural awareness" below. Never join `person_names` directly for display.
- Acronyms in `organization_acronyms` (separate table); `organization_names` holds legal/dba/former names only. Each table has exactly one canonical row per org via a partial unique index

### Org name effective dates (#239)

`organization_names` carries `effective_start` / `effective_end` (nullable `DATE`, `CHECK (start <= end)` = `chk_org_name_effective_date_order`) — the name's real-world validity timeline. PM is the system of record for "which name was in effect when"; consumers filter the dated name list rather than calling an as-of endpoint.

- **Identity model:** a rename is **one durable Org**, never a fork. An external identifier (e.g. `org_wa_legislature_committee_id`) anchors exactly one Org for its whole life — "one WSL Id = one committee" is a deliberate invariant. Resolves CannObserv/usa-wa#40.
- **Orthogonal axes:** effective dates are independent of `is_canonical` (the display pointer) and `name_type` (the kind of name). NULL `effective_start` = unknown lower bound (−∞); NULL `effective_end` = still in effect (+∞).
- **Ingestion is append-only:** `write_names` stores dates only on a newly inserted row; dates sent for an already-present name are a no-op. Rename transitions (close the old interval, promote the new canonical) are **curated in admin**, never feed-driven.
- **Broadcast:** any name-row INSERT/UPDATE/DELETE fires `trg_touch_org_on_name_change` → bumps `organizations.updated_at` → emits an `entity_changes` `'updated'` row, so change-feed subscribers re-fetch and pick up the new dates.

### Address validity windows (#181)

`entity_addresses` carries `valid_from` / `valid_until` (nullable `DATE`, `CHECK (from <= until)` = `chk_ea_validity`) — the link's real-world validity window. NULL = open-ended on that side. Overlapping windows are legitimate (mailing + physical simultaneously, two offices of the same type) — do **not** add an overlap-exclusion constraint.

- **Identity model:** the window is part of the link's identity. `entity_addresses_entity_addr_uniq` is `UNIQUE NULLS NOT DISTINCT (entity_type, entity_id, address_type, address_id, valid_from, valid_until)` — the same entity at the same address across two windows is history, not a duplicate. Any dedup logic (merge anti-joins, schema-migration dedup DELETEs) must compare windows with `IS NOT DISTINCT FROM`.
- **No `is_current` flag:** a generated column is impossible (`CURRENT_DATE` is not immutable); call sites filter with `valid_until IS NULL OR valid_until >= CURRENT_DATE`, matching the jurisdictions pattern. Admin detail queries sort current-first, then `valid_from DESC NULLS LAST`.
- **Ingestion:** `write_addresses` dedups on the normalized form plus the window, mirroring the unique key (#256). Dateless claims (both bounds NULL) stay window-agnostic and dedup against any existing row; dated claims (`ObservationAddress.valid_from` / `.valid_until`, ISO `YYYY-MM-DD`, `from <= until`) dedup on the exact window via `IS NOT DISTINCT FROM` and record a fresh link for a new window, reusing the existing `addresses` row rather than minting a per-window duplicate. Supply dates only when an upstream source carries them; validity dates are otherwise curated in admin.
- **Historical-window semantics — admin end-dating is authoritative over feeds (#256 decision, from #181 CR finding 4):** a dateless claim keeps matching *any* existing row, including an expired historical row (`valid_until < CURRENT_DATE`). So once an admin end-dates an entity's address, a later dateless re-observation records **nothing** — it does not resurrect a current, open-ended row. Rationale: curation is deliberate and human; silently reopening a closed window on the next ingest run would be whack-a-mole. A dateless re-observation of an expired address therefore leaves no trace (observations aren't logged as per-sighting events) — intentional. A source that genuinely needs to assert a *current* window supplies explicit `valid_from`/`valid_until` (the dated-claim escape hatch, #256 item 1); dated claims dedup with strict `IS NOT DISTINCT FROM` window equality, while dateless claims deliberately ignore the window.
- **Broadcast:** any `entity_addresses` INSERT/UPDATE/DELETE fires `trg_touch_entity_on_address_change` → bumps the parent entity's `updated_at` → emits an `entity_changes` `'updated'` row (all five entity types), so change-feed subscribers re-fetch and pick up the new window.

### Org lifespan bounds on assignments (#307)

An org's lifespan end is **derived, not a column**: `v_org_lifespan(organization_id, ended_on)` takes the earliest non-archived `dissolved` / `merged_with` entity event, resolved to the *latest* date within the event's known precision (year-only 2023 → `2023-12-31`; month-only → last day of month) so closing an assignment at `ended_on` never claims an earlier end than the source supports. `renamed` / `split_from` imply continuity, not an end; an end event without a year (`merged_with` doesn't require one) derives no bound. `organizations.active` is dateless state and `archived_at` is admin bookkeeping — neither is a lifespan; an org marked inactive **should** also get an end event when the date is known.

**Invariant:** an assignment's window falls within its org's lifespan.

- Org ended → no `is_current=TRUE` assignment on its roles (hard).
- `start_date` / `end_date` ≤ `ended_on` when both known (contradiction otherwise).
- `is_current=FALSE, end_date NULL` = **unknown end**, not "ongoing" — allowed on an ended org; never invent an end date for it. Exclude these rows from "current members" displays (`is_current`, not `end_date IS NULL`, is the currency signal).

**Enforcement (deliberately app-layer, no DB trigger):** `src.core.org_lifecycle.check_assignment_lifespan(conn, role_id, *, is_current, start_date, end_date)` raises `AssignmentOutsideOrgLifespan` (codes mirror the audit categories); all three admin write surfaces call it (role-assignments section, role-detail inline rows, person-detail inline rows) and render `lifespan_error_message(exc)` inline. The public observation path is *not* gated — server-to-server writes record what the source asserts and `scripts/audit_org_lifecycle_assignments.py` reconciles (report mode lists violations; `--execute` closes `current_on_ended` rows at `ended_on` with a provenance note; contradictions and unknown-end rows are report-only). A cross-table temporal trigger would misfire on messy, out-of-order ingested history — revisit only after the audit runs clean. Complements the *role*-level `established_on`/`abolished_on` bounds (`_check_assignment_within_bounds` in `roles_shared.py`), which remain a pure-date check against the role row.

**UX:** org detail shows a warning banner when an archived/inactive/ended org still carries open assignments; marking an org inactive flashes the open count. Close/re-home flows ride on #266 / #305 tooling.

### Assignment observations — update semantics & provenance (#311)

The `(person, role, start_date)` match key is **identity, not payload**: a producer correcting a start date used to miss the key and mint a duplicate, and an auto-attach used to silently discard `end_date`/`is_current` deltas. #311 splits update authority across the two resolution modes:

- **PM-native (`pm_assignment_id`) = the authoritative update channel.** The producer proves it means exactly this row, so `update_assignment_fields` *replaces* stored values (supersedes the #289 NULL→dated-only backfill): `start_date` moves (never clears), an **explicit** `end_date: null` clears (reopen) while an omitted field leaves the bound alone (the handler passes `"end_date" in req.model_fields_set` — JSON null ≠ omitted), `is_current` is tri-state (`bool | None`, None = omitted). A resulting dated end with `is_current` omitted implies `FALSE`. Merged-state guards reject before touching the row: `is_current_end_date_conflict`, `start_after_end_date`, `start_date_conflict` (sibling unique-index collision), `source_key_mismatch`.
- **Natural-key auto-attach = safe enrichment + honest signaling.** Exactly one mutation is allowed: closing an open tenure (stored end NULL → supplied dated end, `is_current → FALSE` in the same UPDATE so `chk_current_no_end_date` holds). Every other delta is withheld and echoed back in `ObservationResponse.unapplied` (additive field; `None` on clean attaches) so the producer can stop retrying and escalate to a PM-native update. Never add fuzzy "same person+role, overlapping window" re-matching — non-consecutive terms of the same role are legitimate, and guessing which tenure a mismatched key means corrupts the other term.
- **Provenance:** `role_assignments.source_key_id` (the #162 pattern) is stamped on observation-created rows (both `resolve_assignment` NEW and `write_role_assignments`) and claimed via `COALESCE` on first authoritative update of a pre-#311 NULL row. Updates require `source_key_id IS NULL OR = caller` — admin surfaces are not gated (they operate on any row); only the observation API enforces source authority.
- **Duplicate cleanup:** `scripts/audit_assignment_duplicates.py` finds overlapping same-`(person, role)` dated pairs (`deepened_start` / `subsumed` auto-merge with `--execute`; `overlapping_review` is report-only). Merge = move side data to the survivor, concatenate notes, **archive** the orphan (never delete) — the archive UPDATE hits the outbox so subscribed producers drop stale anchors.

#### Retraction — `op="retract"` (#391)

Closing is not retracting. `end_date` + `is_current=false` asserts the tenure **ended**; a produced **artifact** (a tenure that never happened — e.g. the usa-wa WSL sponsor archive's spurious "John Wynne → LD39 State Senator, 2001–02") needs the assertion that it **never existed**. The two pre-#391 levers were both wrong: closing leaves the false claim standing, and simply ceasing to produce the row orphans the anchored PM assignment (the exact backlog `scripts/audit_assignment_duplicates.py` mops up). Reaching into `POST /admin/role-assignments/{id}/archive/` is out-of-band — admin-scoped auth, outside the producer/LWW contract.

`op: "observe" | "retract"` on `AssignmentObservationRequest` (`observe` default) closes that loop. Spelled as a **verb**, not an `archived: bool` payload field, for parity with events (#322) / citations (#319) / relationships (#301) — all four carry the same guard set, and a boolean would read as LWW-writable state and inherit the `end_date` omitted-vs-explicit-null ambiguity.

- **Always id-addressed.** `identifier_type=pm_assignment_id` required; a natural-key retract → `rejected` / `invalid` (the model validator defers to the handler here rather than 422-ing, so the reject shape matches the other retract surfaces).
- **Guards, in order:** already-archived → **no-op** (`auto-attached`, no UPDATE, no clock bump) — checked **before** provenance so a foreign re-emit stays quiet; unknown id → `assignment_not_found`; a supplied `person_id`/`role_id` differing from the stored row → `identity_immutable` (guards a copy-paste `pm_assignment_id`); live row with a foreign non-NULL `source_key_id` → `source_key_mismatch` (the slug this surface already speaks — not the events' `provenance_conflict`). The refine payload and all ancillary (`links`/`contact_methods`/`addresses`) are ignored on a retract.
- **Not routed through `resolve_entity`.** Its pm-native lookup filters `archived_at IS NULL`, which would turn a re-emit into `pm_id_not_found` instead of the quiet no-op a stateful producer needs; `retract_assignment` does its own unfiltered lookup (mirrors `_retract_event`).
- **Anti-resurrection — the load-bearing half.** `resolve_assignment` now attaches to an **archived twin** (`auto-attached`, that row's id, nothing `unapplied`) instead of minting a fresh active row. **Both** doors onto the identity are closed: `write_role_assignments` (the `role_assignments: [...]` embedded people-observation path) dedups on the *open* tenure, which an archived row no longer matches, so it carries its own archived-twin skip — otherwise a producer emitting the same tenure embedded would resurrect what it retracted through the assignment endpoint. On that attach **nothing is written at all**: bound deltas are withheld *and* ancillary is skipped (`resolve_assignment` returns `attached_archived=True`; the handler branches on it). Attaching links/contacts/addresses to a retracted row would put evidence on a soft-deleted entity and fire the #327 touch triggers, emitting an `entity_changes` row for something subscribers have already dropped. Everything withheld — bound names *and* ancillary names (`notes` stays exempt, as it is on the active path: create-only, never reported) — comes back in `unapplied`, so a producer that keeps sending a retracted tenure is told rather than silently no-op'd (the #311 honest-signaling rule applies here too). One deliberate divergence from the active path: a supplied value is reported **even when it equals what the archived row stores**. On an active row "equals stored" means the claim is already true in PM; on a retracted row PM asserts the tenure never existed, so the identical claim is contradicted, not satisfied — and the commonest payload (a producer re-emitting a currently-held tenure as `is_current: true`) is exactly the one that matches what the row stored when it was retracted. `uq_role_assignment_person_role_start` is partial on active rows so the DB *permits* the re-create — the app declines. Without this a producer that retracts by id but keeps the tuple in its sync set loops forever (retract → next cycle re-creates → orphan again), and admin suppression of any assignment would be defeated on the next sync. A retract is **authoritative**; un-retract is a deliberate admin unarchive only (`POST /admin/role-assignments/{id}/unarchive/`) — there is deliberately no `archived:false` producer verb, the same conclusion #322 CR round 2 reached for events.
- **Cascades come free.** The archiving UPDATE fires `trg_entity_changes_role_assignments` → outbox row (subscribers already mirror `archived_at`, usa-wa #41/#42) and `trg_cascade_assignment_relationships` → dependent `staff_of` edges archive with the seat (#301). Ancillary stays attached to the soft-deleted row, so the daily ancillary-orphan audit is unaffected. No schema change — `role_assignments.archived_at` already shipped.
- **New disposition** `retracted` on `Disposition` / `ObservationResponse.disposition`. Emitted only by this surface; the other single-object observation endpoints never return it.

### Org parent — authoritative reparent & provenance (#334)

`organizations.parent_id` on the org observation path is the org analog of the #311 assignment split — same "identity vs. authoritative field-update" shape, resolved on the **identifier mode**, not the parent-specifier (`organization_parent_id` / `_name` / `_acronym` all resolve to one `parent_id` first, then share the write):

- **PM-native (`pm_org_id`) = the authoritative reparent channel.** The producer proves it means exactly this org, so `write_org_parent(..., authoritative=True)` *replaces* the stored parent — the fix for the reported bug where re-observing an already-anchored subcommittee with the correct parent was a silent no-op. Guards reject before touching the row: `parent_not_found` (unknown/archived parent), `parent_cycle` (self-parent or an ancestor loop caught from `trg_no_org_cycle` — the app pre-checks self + existence, then maps the trigger's `RaiseError`), `source_key_mismatch`.
- **Natural-key / external-identifier auto-attach = write-if-null.** Fills the parent only when currently NULL (and claims source on that fill); an org that already has a parent is left untouched — a natural-key match never reparents. There is no `unapplied` echo (unlike assignments) because the only parent delta a producer can assert authoritatively is via `pm_org_id`; a silent no-op on the natural path is the expected monotonic behavior. Filling a NULL parent with a descendant would close a loop → rejected `parent_cycle` (the write-if-null UPDATE fires `trg_no_org_cycle` too; both paths map the `RaiseError` via `_set_org_parent` rather than bubbling a 500).
- **Provenance:** `organizations.source_key_id` (the #162/#311 pattern) is claimed via `COALESCE` on the first write (either mode), never stamped at org creation (`_create_entity` leaves it NULL — the claim is lazy, on first parent write). An authoritative reparent requires `source_key_id IS NULL OR = caller`; a NULL source (admin-set via `inline/parent/`, or pre-#334) is claimable-once. Curator-precedence (admin parent always wins) is **not** modelled — a NULL-source curator parent is claimable, symmetric with #311; revisit only on real curator/producer hierarchy contention.
- **Idempotence:** re-asserting the same parent (authoritative) is a quiet no-op *before* the provenance gate, so a mirroring producer re-emitting current state never sees `source_key_mismatch` and never bumps `updated_at` (no producer↔PM ping-pong). Same ordering as #311.

### Event observations — refine-in-place, partial-success, `succeeded_by` & retract (#321/#322)

Entity events are producer-writable two ways: **embedded** in the org/person observation payload (`events: [...]`, all-or-nothing — a rejected event raises `ObservationRejected` and rolls the whole observation back) and via the **event-native** `POST /api/v1/orgs/{org_id}/events/observations` (partial-success). Both go through the same per-event core (`_apply_one_event`); neither decouples events from the org LWW clock — an event INSERT/UPDATE still fires `trg_touch_entity_on_event_change` → `organizations.updated_at`, which is load-bearing for the usa-wa `sync_entity_events` reconcile.

- **Identity vs. mutable — the #311 analog.** `(event_type, linked_entity)` is **identity**; `date`/`notes`/`place`/`visibility` is the mutable set. A `pm_event_id` update refines only the mutable set (the date as a unit — a year-only sharpening clears finer precision). Changing `event_type` or a supplied, differing `linked_entity` is a *different* event → rejected `identity_immutable`, never a silent reclassify (mirrors `resolve_role`'s never-reclassify rule).
- **Diff-before-write no-op gate.** An unchanged `pm_event_id` re-emit skips the UPDATE (so `updated_at` doesn't bump and re-arm the producer↔PM ping-pong) and returns `auto-attached`. The no-op check precedes the provenance gate, so an identical redelivery by a foreign key stays quiet (same as #311).
- **Provenance:** `entity_events.source_key_id` stamped on create; a refine requires `source_key_id IS NULL OR = caller` (claimed via `COALESCE`), else rejected `provenance_conflict`. Admin surfaces are not gated.
- **Partial-success + reason slugs.** The event-native endpoint runs each event in its own savepoint: a rejection rolls back only that event and is reported alongside the ones that landed. This gives **ordering-tolerance for free** — a `succeeded_by` ahead of its (unanchored) successor comes back `linked_entity_unresolved` (the one **transient** reason — self-heals next cycle) while its siblings commit. Terminal reasons: `identity_immutable`, `event_not_found`, `provenance_conflict`, `applies_to_mismatch`, `missing_required_field`, `unknown_event_type`, `invalid`. Per-event dispositions (`new｜auto-attached｜updated｜retracted｜rejected`) surface in `ObservationResponse.events` (embedded) and `EventObservationsResponse.results` (event-native).
- **`succeeded_by` slug.** The renamed-continuity link (WA committee re-keys): `applies_to=organization`, `requires_linked_entity`, no year. Direction (not derivable from the row): the event lives on the **predecessor**; `linked_entity_id` → successor. `founded`/`dissolved` are the lifespan window; `split_from`/`merged_with` are branches; each event carries exactly one linked entity, so multi-way re-orgs are expressed pairwise.
- **Retract / void (#322).** `op="retract"` on an event item archives the `pm_event_id`-addressed event (`archived_at = now()`, never hard-delete) — the only correction for a mis-linked **dateless** linked event (`succeeded_by`/`split_from`/`merged_with`), which has no mutable field to refine, so a re-link is **create-new + retract-old** (both land in one partial-success batch). Retract is always id-addressed (no `pm_event_id` → `invalid`); the supplied `event_type` — **and** a supplied `linked_entity` — must match the stored row (else `identity_immutable`, guards a copy-paste `pm_event_id`, symmetric with the refine guard); provenance is the same-or-NULL gate (foreign non-NULL source → `provenance_conflict`), and any refine payload is ignored. Two lookups deliberately see archived rows (each for its own reason): (1) the **create-path** content-dedup — a retract is **authoritative**, so re-observing content identical to a retracted event does **not** resurrect it; the dedup matches the archived row and returns `auto-attached` (no fresh row, event stays retracted), mirroring the address dateless-reobservation anti-resurrection rule (§"Address validity windows"). Un-retracting is a deliberate act (admin unarchive), not a side effect of re-observation. (2) the **retract-path** lookup — unlike refine (which filters `archived_at IS NULL`), it is unfiltered so an already-archived retract is a **diff-gated no-op** (`auto-attached`, no UPDATE, no clock bump) — checked before the provenance gate — so a producer that re-emits the retract every cycle doesn't re-arm the ping-pong. The archiving UPDATE fires the same org-touch trigger → outbox row, so subscribers drop the stale anchor (public event reads already filter `archived_at IS NULL`). Works on both transports (embedded all-or-nothing, event-native partial-success). Not gated to dateless links — the op is general; datable events keep refine as the primary correction, retract is the escape hatch when the event shouldn't exist at all.

### Jurisdiction graph broadcast (#275)

`jurisdiction_relationships` and `organization_jurisdiction_affiliations` are curated from the admin (Phase 3); both propagate to the change feed so a jurisdiction subscriber sees graph edits (the public API exposes them: `GET /api/v1/jurisdictions/{id}/relationships` and the org read model's `jurisdiction_affiliations`).

- **Relationship edges:** any `jurisdiction_relationships` INSERT/UPDATE/DELETE fires `trg_touch_jurisdiction_on_relationship_change` → `touch_parent_jurisdiction()` bumps **both** endpoints' (`from_id` and `to_id`) `updated_at` → emits an `entity_changes` `'updated'` row per endpoint.
- **Org affiliations:** any `organization_jurisdiction_affiliations` INSERT/UPDATE/DELETE fires **two** touch triggers — `trg_touch_org_on_affiliation_change` (org) and `trg_touch_jurisdiction_on_affiliation_change` (jurisdiction) — so a subscriber on either side re-fetches.

### Auto-promote invariant

Every **delete** route on `organization_names` must call `_maybe_promote_sole_name(org_id, db)` inside its transaction (from `src.api.admin.orgs_names`) — promotes the sole remaining non-canonical name to canonical, keeping `v_org_display_names.display_name` non-NULL. Edit routes do not need this — the canonical edit guard prevents completing when it would leave zero canonical names.

Equivalent for acronyms: `_maybe_promote_sole_acronym(org_id, db)` (from `src.api.admin.orgs_acronyms`) — call inside the transaction of every **delete** route on `organization_acronyms`.

### Last-identity guard

`name_delete` blocks when the org has exactly one name and no canonical acronym; `acronym_delete` blocks symmetrically.

- HTMX: return HTTP 200 with `flash_trigger("error", ...)` and empty body
- Non-HTMX: raise `HTTPException(409)`

### Links schema

`link_types` table holds (slug, display_name, is_social). `links` table holds (entity_type, entity_id, url, link_type_id, is_active). Social links: `JOIN link_types WHERE is_social = TRUE`.

**Natural-key uniqueness** (`uq_links_entity_url`, issue #142): an entity must not carry the same URL twice for the same `link_type_id`. `is_active` is intentionally excluded from the index — keeping both an active and an archived copy of the same URL is not a supported state. Ingestion pipeline writes use `ON CONFLICT (entity_type, entity_id, url, link_type_id) DO NOTHING` so re-runs are idempotent. Admin CRUD (create + update) catches `asyncpg.UniqueViolationError` and returns 409 with a `warning` flash; never bubbles as a 500.

### Merge dedup — role_assignment ancillary re-homing (#324)

`links` / `contact_methods` / `field_confidence` / `identifiers` / `import_provenance` attach to a `role_assignment` via `(entity_type='role_assignment', entity_id=<id>)` with **no FK** (identifiers scope through `entity_identifier_types`, no `entity_type` column). A merge that hard-deletes a duplicate assignment must **re-home its ancillary onto the surviving assignment first**, else those rows dangle off a dead id — invisible to every UI and the change feed, and never pruned (the same migrate-before-delete hazard #265's archiver was built for, reached via the merge path).

All three conflict-delete sites — `people_merge.py`, `orgs_roles.py::role_merge`, `orgs_merge.py` role-pair merge — fetch the `(loser_assignment, winner_assignment)` pairs and call `rehome_conflicting_assignment_ancillary` (from `src.core.ancillary_migrate`) **before** the DELETE (and `people_merge` derives its DELETE set from those same pairs, so re-homed and deleted rows are provably identical). `uq_role_assignment_person_role_start` guarantees a 1:1 loser→survivor target. Each row is re-pointed, or deleted when the survivor already carries an identical one (identity: `links (url, link_type_id)`, `contact_methods (contact_type, value)`, `field_confidence (field_name, value_hash)`, `identifiers (entity_identifier_type_id, value)`). `import_provenance` is **append-only** (no unique key, `key_fields=None`) — every row re-points wholesale, never dedups. A survivor whose ancillary actually changed gets an `entity_changes` 'updated' signal: for `links` / `contact_methods` / `identifiers` the re-point self-emits via that table's touch trigger (#327, one signal per moved row); for the trigger-less `field_confidence` / `import_provenance` a manual emit is added, gated on a move of one of those (`TRIGGERLESS_ANCILLARY_TABLES`). See "Ancillary `entity_changes` emit" below. The org-merge path is the subtle one: it re-creates each loser assignment on the winner role under a **new id** before deleting (carrying over `source_key_id` provenance), so the re-home target is that new id (or the existing winner row for a dropped dup).

**Guard:** `count_orphaned_role_assignment_ancillary` (anti-join, no matching `role_assignments` row) backs both a unit test and the daily `power-map-ancillary-orphans.timer` (`scripts/audit_ancillary_orphans.py`, exit 3 on any orphan). **Existing-orphan cleanup:** `scripts/cleanup_role_assignment_ancillary_orphans.py` — merges leave no assignment tombstone, so recovery is heuristic per dead id (PDC filer→person→current seat; `first.last@` email→unique person→current seat; redundant-link purge; everything else reported for manual triage). Dry-run by default; `--execute` is supervised.

**Role-level ancillary (#326).** A role *definition* carries the same hazard for its own `links` / `contact_methods` (`entity_type='role'`, no FK — `role` is excluded from `identifiers`/`field_confidence`/`import_provenance`), made routine by the admin contacts/links editors. `src.core.ancillary_migrate` mirrors the assignment machinery via a shared `_migrate_specs`: `rehome_role_ancillary` (merge: re-point + dedup onto the surviving role — the survivor 'role' 'updated' signal comes from the `links`/`contact_methods` touch triggers, #327, so no manual emit) is called by all three role-deleting paths (`roles.py` hard-delete uses `delete_role_ancillary` instead — a hard delete removes the rows; `orgs_roles.py::role_merge` and both `orgs_merge.py` role-pair deletes re-home). The same daily `audit_ancillary_orphans.py` guard counts role orphans too (`count_orphaned_role_ancillary`), namespaced `role.*` in the breakdown. No dedicated cleanup script — the write paths are all covered, so a role orphan is an anomaly for manual triage.

### Ancillary `entity_changes` emit — DB touch triggers (#327)

**The signal that a polymorphic ancillary row changed the parent belongs in a DB touch-cascade trigger, not in application code** — so *every* write path (admin CRUD, public observation, merge re-homing, a direct INSERT from a script) notifies change-feed subscribers uniformly. A change made in the admin dashboard must reach subscribers exactly like one made via the public API. The line is by ancillary kind:

- **User-facing state → trigger (single source, no manual emit anywhere):** `entity_addresses` (`trg_touch_entity_on_address_change`), `contact_methods` (`trg_touch_entity_on_contact_change`, #327), `links` (`trg_touch_entity_on_link_change`, #327), `identifiers` (`trg_touch_entity_on_identifier_change`), `person_names` / `org_acronyms` / affiliations / jurisdiction relationships. Each is polymorphic and dispatches on `entity_type` to bump the parent's `updated_at`, which fires the parent's `fn_record_entity_change`. The contact/link triggers cover all five entity types; the identifier trigger covers `organization`/`person`/`jurisdiction`/`role_assignment` (the last added in #327). Triggers emit **per row** — a bulk op that touches N rows emits N signals (idempotent for subscribers; matches how `entity_addresses` always behaved).
- **Ingestion telemetry → deliberately trigger-less:** `field_confidence` and `import_provenance` are written per-observation by `src/core/ingestion/pipeline.py`, so a trigger would emit an 'updated' on every audit/confidence row. They stay trigger-less; the only path that must signal their movement is a merge/cleanup re-home, which emits manually gated on `TRIGGERLESS_ANCILLARY_TABLES` (see the #324 section).

**Consequences to preserve:**

- **Never** emit `entity_changes` for `contact_methods`/`links`/`identifiers` from application code — the trigger already fires; a manual emit **double-signals**. This is why #327 *removed* the old manual emits in `observation.py` (`write_contact_methods`/`write_links`; the contact `display_label` null-fill passes `record_change=False`, mirroring `entity_addresses`), narrowed the merge `rehome_*` emits to the trigger-less tables, and dropped `rehome_role_ancillary`'s manual emit entirely (both its tables are triggered). The admin shared factories (`_contacts_shared`/`_links_shared`/`_identifiers_shared`) do raw INSERT/UPDATE/DELETE and rely on the trigger — no app-layer emit.
- History note: `contact_methods`/`links` shipped **without** a trigger and every write path emitted manually (public + merge) while admin CRUD emitted nothing (#324/#326 accommodated the absence rather than choosing it). #327 added the triggers to converge them onto the `entity_addresses` model and deleted the now-redundant manual emits.

### Citations — source provenance (#319)

A **citation** is human-checkable evidence (`url` / `title` / `excerpt` / `accessed_at`) for a fact, attached to an entity or one of its fields. It is a fifth provenance axis, distinct from `source_key_id` (actor), `import_provenance` (ingestion batch), and `field_confidence` (automated reliability) — curated, observable, and retractable. It supersedes the ad-hoc `role_assignments.notes` capture (#314/#318). Design doc: `docs/plans/2026-07-29-citations-pattern-design.md`.

- **Table:** `citations` — polymorphic no-FK ancillary over **seven** citable types (`organization`, `person`, `role`, `role_assignment`, `jurisdiction`, `person_name`, `entity_event`). `chk_citation_url_or_title` (a URL-less citation must carry a title). Soft-delete via `archived_at` (never hard-delete).
- **Identity** = `(entity_type, entity_id, field_name, url)`, `uq_citation_identity` with **`NULLS NOT DISTINCT`** over active rows: a NULL `url` (and NULL `field_name` = whole-entity) is one distinct slot, so at most one URL-less citation per `(entity, field)`. `title`/`excerpt`/`accessed_at` are mutable payload, never identity.
- **Observation semantics** (mirror events #321/#322, in `src/core/citations.py`): natural-key observe → refine the matched active row or create; `pm_citation_id` → id-addressed refine (identity immutable → `identity_immutable`); `op="retract"` archives the id-addressed row and is anti-resurrection-safe (re-observing retracted content auto-attaches to the archived row). Diff-before-write no-op precedes the `source_key_id` same-or-NULL provenance gate (`provenance_conflict`). `field_name` (non-NULL) validated against the per-entity `CITABLE_FIELDS` allowlist (`citable_field_unknown`); reason slugs incl. one transient `entity_unresolved`.
- **Transports:** `write_citations` (embedded on org/person observation payloads — all-or-nothing) and `apply_citation_observations` (the citation-native endpoint — partial-success). See `docs/PUBLIC_API.md`.
- **`entity_changes` emit:** DB touch trigger `trg_touch_entity_on_citation_change` (per the #327 model — no app-layer emit). Sub-entity citations indirect: a `person_name` citation touches the owning person, an `entity_event` citation touches the event's owning entity.
- **Merge re-homing:** every merge/delete that collapses a citable entity re-homes its citations onto the survivor **before** the delete (`migrate_citations`/`rehome_citations`, NULL-safe active-scoped dedup) — wired into `rehome_conflicting_assignment_ancillary` (role_assignment), `rehome_role_ancillary` (role), `delete_role_ancillary` (drop), and the primary person/org DELETE in `people_merge`/`orgs_merge`. Citations self-emit the survivor signal via the touch trigger. **Sub-entities** (`person_name`/`entity_event`) are handled proactively too: `people_merge` re-homes a deduped loser name's citations onto the surviving winner name (same LATERAL match as the #309 reading re-point) and drops citations for curated/purged names; `entity_event` rows aren't re-pointed by any merge (they dangle when the parent is deleted), so `delete_event_citations_for_owner` drops their citations before the person/org DELETE, and the admin event/name hard-delete paths call `delete_citations`. The daily orphan audit (`count_orphaned_citations`, `citation.<type>` scope) remains the backstop.

### Role-assignment relationships — RA→RA edges (#301)

A **role-assignment relationship** is a directional, temporal edge between two `role_assignments`: the staffer's assignment (`from`) serves a principal legislator's seat assignment (`to`). It models a person→person staff relationship the flat role model can't hold, while preserving the object assignment context (org, role, window) on both sides. Anchoring on assignments (not people) is deliberate: biennium turnover = new assignments on both sides = a new edge. Design doc: `docs/plans/2026-08-01-assignment-relationships-design.md`.

- **Table:** `role_assignment_relationships` — FK-backed (both endpoints `REFERENCES role_assignments(id) ON DELETE CASCADE`), soft-delete via `archived_at`. Typed via the `role_assignment_relationship_types` catalog (seed `staff_of`, `is_symmetric=FALSE`, from=staffer→to=principal). `chk_no_self_rel_assignment` + `chk_edge_valid_range`. **Identity** = `(from_assignment_id, to_assignment_id, rel_type_id)` — `uq_assignment_relationship_identity` over active rows; `valid_from`/`valid_until`/`notes` are mutable payload.
- **Temporal invariant:** an active edge's window ⊆ the intersection of both endpoint assignment windows, and the edge dies when either endpoint ends. Enforced **app-layer only on admin/direct writes** (`check_edge_within_assignments` → `EdgeOutsideAssignmentWindow`); the observation path **records freely** (mirrors #307). No blanket DB invariant trigger (it would block the observation record-freely contract). Steady-state drift is reconciled by `scripts/audit_assignment_relationship_windows.py`.
- **Cascade (DB trigger `cascade_assignment_relationships` on `role_assignments`):** when an endpoint's window shrinks or it is archived, dependent active edges auto-**clamp / archive** — `valid_from` clamps up only a *defined* start (an unknown start is never invented, #307); `valid_until` clamps down a defined end **and** closes a NULL/ongoing edge at a defined endpoint end; a clamp that inverts the window archives the edge; an archived endpoint archives the edge. WHEN-gated on `start_date`/`end_date`/`archived_at` so the touch-trigger `updated_at` bump doesn't recurse. Every mutation is an edge `UPDATE`, so it self-emits (observable auto-clamp). The audit shares this exact clamp rule so trigger and audit never diverge.
- **Change feed:** the edge has its **own** `entity_type` `role_assignment_relationship` (`trg_entity_changes_role_assignment_relationships`) — independently addressable/retractable — **and** touches both endpoint assignments (`trg_touch_assignments_on_relationship_change`), so it surfaces on each assignment's feed too. `entity_type` CHECKs extended on `entity_changes`/`deleted_entities`/`api_key_entity_subscriptions` (+ subscription resolve UNION) so an edge id is subscribable. This goes further than the admin-only, touch-only `jurisdiction_relationships` precedent.
- **Observation semantics** (`src/core/assignment_relationships.py`, mirrors events/citations): pm-native only — each claim references its endpoints by `pm_assignment_id`. Natural-key observe (`from`+`to`+`rel_type`) → refine matched active row / anti-resurrect archived twin / create; `pm_relationship_id` → id-addressed refine (identity immutable → `identity_immutable`); `op="retract"` archives (already-archived no-op). Diff-before-write precedes the `source_key_id` same-or-NULL gate (`provenance_conflict`). Reason slugs incl. transient `assignment_unresolved`, plus `rel_type_unknown`/`self_relationship`/`relationship_not_found`.
- **Transports:** native `POST /api/v1/assignment-relationships/observations` (partial-success) + read `GET /api/v1/assignments/{pm_assignment_id}/relationships` (both directions); scopes `assignment_relationships:read`/`:write`. No embedded transport (the edge references assignments, not a parent entity). Admin panel on the role-assignment detail (`src/api/admin/role_assignments_relationships.py`).
- **Merge re-homing:** the edge is FK-backed so it can't *orphan*, but a merge's hard-delete of a losing assignment would silently **CASCADE-delete** its active edges. `rehome_assignment_relationships` re-points them onto the survivor (deleting self-edges + winner collisions) **before** the DELETE — wired into `people_merge`, `orgs_roles::role_merge`, and both `orgs_merge` role-pair sites. Re-point/delete self-emit via the edge's touch + change triggers (no manual signal). No orphan-audit scope (FK makes orphans impossible).
- **Backfill:** `scripts/backfill_assignment_relationships.py` resolves the 3 concrete #266-descoped rows (heuristic, supervised; misses reported, never guessed).

### Person names — i18n & cultural awareness

Hybrid model (issue #121): `person_names.name` is the canonical UTF-8 display string; per-name-row metadata (`locale`, `script`, `sort_as`, `visibility`, `reading_of_id`) lives on `person_names`; structured parts live in the `person_name_parts` sidecar (1:0..1, keyed on `person_names.id`).

#### Storage rules

- Store user input verbatim. **Never** lowercase, title-case, ASCII-fold, or strip diacritics on input — names like "McNamara", "van der Waals", or "ffrench" rely on specific casing; Vietnamese names rely on diacritics.
- `name` is the authoritative free string. Structured parts in `person_name_parts` are **never auto-decomposed** — populated only when an upstream source supplies pre-parsed structure (e.g., via the observation endpoint's `names[].parts` field) or when a human confirms a suggestion — the "David Lloyd George" ambiguity is unresolvable without cultural context. **Never auto-write parts to the database** without human confirmation or upstream pre-parsed data. Assisted *suggestion* of parts is allowed via `src.core.normalizers.person_name.suggest_parts(...)` — used by triage/backfill scripts (CSV-mediated review) and, optionally, the admin name editor (pre-fill the form for review). The decomposer never persists; only the existing `upsert_or_delete_parts` path does.
- Sort with Postgres ICU collations (e.g. `ORDER BY name COLLATE "und-x-icu"`), or by `sort_as` when present. Do not use `LOWER(name)` for sorting.
- New rows default to `visibility='public'`. The `trg_deadname_visibility` trigger downgrades any `name_type='deadname'` row from `'public'` to `'legal_only'` automatically; an explicit `'hidden'` is preserved.

#### Visibility rule (single, project-wide)

A `person_names` row with `visibility ∈ {'legal_only', 'hidden'}` is excluded from:

- `v_person_display_names`
- All public API responses
- All admin search results, list pages, autocomplete, typeahead
- All duplicate-detection candidate sets and ingestion auto-match queries
- All flash messages and activity logs

It surfaces **only** on the person-detail admin page, behind an explicit "Show legal/historical names" disclosure toggle (default collapsed).

Enforcement layers:

- `v_person_display_names` filters by `visibility='public'` — use the view for all display.
- For raw `FROM person_names` / `JOIN person_names` queries, AND-append `visible_names_filter()` from `src.core.db` (or the literal `visibility = 'public'`).
- `tests/core/test_visible_names_filter.py::test_no_unguarded_person_names_queries` greps for direct access outside `ALLOWED_DIRECT_ACCESS`. New direct-access call sites must either filter visibility inline or be added to the allow-list with a `# visibility-allowlist (issue #121): <reason>` comment.

#### `name_type` values

| Value | Meaning |
|---|---|
| `legal` | Government-recognized legal name |
| `preferred` | What the person asks to be called publicly |
| `alias` | Alternate identifier (pen name, handle) |
| `former` | Previous name (marriage change, divorce, voluntary change) |
| `initials` | Initialism (`JFK`, `MLK`) |
| `maiden` | Birth surname |
| `religious` | Religious / monastic name |
| `stage` | Performer / artist name |
| `deadname` | Pre-transition or pre-disclosure name; auto-`legal_only` |
| `reading` | Phonetic reading of another row (e.g. furigana) — link via `reading_of_id` |
| `romanization` | Latin-script rendering of another row (pinyin, romaji) — link via `reading_of_id` |
| `mrz` | ICAO 9303 Machine-Readable Zone form — link via `reading_of_id` |
| `variant` | Alt-spelling / nickname of an existing name on the same person (e.g. `Jodi`/`Jody`, `Kip`/`Kristopher`) — see below |

#### `variant` vs neighbouring types

| | `variant` | `alias` | `preferred` | `reading`/`romanization`/`mrz` |
|---|---|---|---|---|
| Same identity? | yes | usually no (pen name, handle) | yes | yes |
| Linked via `reading_of_id`? | no | no | no | yes |
| Typical case | `Jody`/`Jodi` (uncertain spelling), `Kip`/`Kristopher` (short form) | "Mark Twain" for Samuel Clemens | What they go by | Phonetic / latin-script / passport form |

A `variant` row sits next to its `legal` row on the same person; both share `person_id`. `is_canonical=FALSE` on the variant; the legal row stays canonical. Use `variant` (not `alias`) when collation against the canonical name matters — e.g. to surface `Jody`-typed search input alongside `Jodi`-keyed records without conflating with truly separate identities.

#### MRZ derivation

When generating an MRZ row from a Latin-script visual `legal` row:

| Transformation | Example |
|---|---|
| Uppercase all letters | `José` → `JOSE` |
| Strip diacritics (NFKD + ASCII-only) | `García` → `GARCIA` |
| Replace hyphens with single space | `García-López` → `GARCIA LOPEZ` |
| Drop apostrophes | `O'Brien` → `OBRIEN` |
| Use `<` as filler / separator | `GARCIA<LOPEZ<<JOSE` |

No automatic generation pipeline exists in Phase 1 — populate manually or via a future ingestion integration.

#### Structured parts (`person_name_parts` sidecar)

Parts live in `person_name_parts`, keyed on `person_name_id` (1:0..1 with `person_names`). Each `person_names` row optionally has its *own* parts row — the Hant `legal` and Latn `romanization` of one person each carry distinct decompositions, not a shared set. ON DELETE CASCADE — when a `person_names` row is deleted its parts row is removed too.

Columns: `given_names TEXT[]`, `family_names TEXT[]`, `additional_names TEXT[]`, `honorific_prefix`, `honorific_suffix`, `primary_identifier`. Arrays are ordered. `primary_identifier` indicates which array drives formal address and primary sort:

- `'family'` — Western, Sinitic, Hungarian (last-name address); sort by `family_names[1]`
- `'given'` — Icelandic, mononymous fallback; sort by `given_names[1]`
- `'patronymic'` — Arabic chain, Russian; address by `given_names[1]`
- `'mononym'` — single-name people (Cher, Prince); the single token is in `name`

A `person_names` row with no corresponding `person_name_parts` row is fully valid — the free `name` string remains authoritative.

The admin UI surfaces this section as **Details** (issue #127); the DB / route names retain `parts` / `person_name_parts`.

#### Canonical name = the display pointer (issue #308)

`person_names.is_canonical` marks **the one name PM displays for a person**. Two constraints carry that meaning:

| Constraint | Guarantees |
|---|---|
| `uq_person_canonical_name` — `UNIQUE (person_id) WHERE is_canonical` | at most one canonical row per person |
| `chk_person_canonical_is_public` — `CHECK (NOT is_canonical OR visibility = 'public')` | the canonical row is always displayable |

This mirrors `uq_org_canonical_name`, which was itself narrowed from `(organization_id, name_type)` to `(organization_id)`. Person and org names now use the same model.

**Why it was re-keyed.** The previous key was `(person_id, name_type, COALESCE(locale,''), COALESCE(script,''))`, so one person could hold several canonical rows at once — a `legal` and a `preferred`, or a Latn and a Jpan `legal`. Consequences, all now gone:

- `v_person_display_names` had to disambiguate with `DISTINCT ON` plus a 13-entry `name_type` priority ladder, and returned duplicate rows per person whenever it didn't.
- A canonical row in one slot could block promotion in another. In particular a curated `legal_only` name (invisible to the view) could occupy a slot, leaving the person rendering blank with no way for the observation path to repair it.
- `is_canonical=TRUE` did not imply "this person displays" — a `deadname` row came back canonical but `legal_only`, because `trg_deadname_visibility` rewrites visibility *after* `is_canonical` is computed.

Nothing consumed the per-family meaning: every read is `ORDER BY is_canonical DESC` (show the main name first) or a wholesale demote on merge, and the admin promote path has always demoted person-wide. At the time of the change production held zero people with more than one canonical row and zero non-public canonical rows.

**Consequences for writers.** A `deadname` can never be canonical — `NEVER_CANONICAL_NAME_TYPES` in `src.core.observation` filters client hints for it, so an observation asserting one is ignored rather than failing the whole request on a `CheckViolationError`. Setting a new canonical in admin must demote the current one in the same transaction (`_names_shared` already does).

**Validate against the visibility that will land, not the one submitted.** `trg_deadname_visibility` rewrites a `deadname` row to `legal_only` *before* the write, so `name_type='deadname'` + `visibility='public'` + `is_canonical` passes any check that only inspects the submitted value and then violates `chk_person_canonical_is_public`. Admin's `_validate_canonical_visibility` therefore checks **`name_type` as well as `visibility`**, and callers pass the *effective* visibility — the stored value when a form omits the field, since `_update_name` leaves the column untouched rather than resetting it. Both name routes also map the constraint to a flash as a backstop.

**Every path that can strand a person without a display pointer must repair it.** Three call the same helper, `heal_person_canonical`: the observation path (including observations carrying no names), name deletion (`_maybe_promote_sole_name`, now a thin delegation), and merge (`merge_person_into`, which demotes the loser's canonical and so must heal the winner). The `#308c` backfill is the fourth repair path but runs its own set-based SQL — it does **not** call the helper; it shares the choice via `name_type_priority_sql()`, so all four still pick the same replacement row. The org name-delete path has its own equivalent heal (`orgs_names.py` — orgs have no visibility or eligibility exclusions, so it is just the ladder plus the guard).

Do not reintroduce a "promote only when exactly one name remains" shortcut — on either the person or the org side. That was the old delete-path rule, and it left a multi-name person (or org) blank whenever their canonical was deleted — the remaining names were perfectly displayable, nothing repaired them, and only a later observation happened to fix it.

The heal is best-effort and never aborts its caller: it runs in a savepoint and swallows `PostgresError`, because failing an observation over a cosmetic display-name repair would discard its links, addresses, role assignments and events. `UniqueViolationError` (a lost race) logs at debug; **everything else logs at WARNING** — `configure_logging` defaults to INFO, so a debug-only line would hide a typo'd statement or a revoked grant while callers still report success.

**Dedup person names on identity, not on the string.** A `legal` row and an `mrz` rendering can carry identical text while being different claims, as can a Latn and a Jpan row; matching on `name` alone silently destroys the second. `write_names` uses the full `(name, name_type, locale, script)` key.

`merge_person_into` uses a deliberately looser variant: text + **visibility** + locale + script must match, and then **either** the `name_type`s are equal **or** both are ordinary display types. Consolidating two records that were each split into `legal` + `variant` would otherwise leave the winner holding the same string as both — redundant rather than two claims. `NO_AUTO_CANONICAL_NAME_TYPES` (`mrz`, `reading`, `romanization`, `deadname`) are never interchangeable: identical text in one of those is a machine-readable rendering, a distinct claim from a display name.

`visibility` is compared on **both** branches, and that is load-bearing twice over. Without it a `hidden` winner row absorbed a `public` loser row carrying the same text; since the loser's canonical is demoted immediately beforehand, that deleted the only promotable name and left the merged person blank — defeating the heal that runs a few lines later. It also silently destroyed `legal_only` claims, breaking the #121 guarantee that the winner inherits the loser's restricted names.

#### Name families are edges, not shared slots (`reading_of_id`)

Re-keying the canonical index costs nothing, because **PM does not model "the same name written differently" by grouping rows that share `(name_type, locale, script)`** — it models it with an explicit FK.

`person_names.reading_of_id` points a `reading` (furigana) or `romanization` (pinyin, romaji) row at the name row it renders, `ON DELETE CASCADE` — a reading cannot outlive its source. So a Japanese legal name and its romaji rendering are:

- **two rows**, of **two different `name_type`s** (`legal` and `romanization`),
- **joined by an FK edge**, not by an implied grouping,
- of which **exactly one is the display pointer**.

That was already true before #308 and is unchanged by it. The old per-family canonical key was not what expressed the relationship; `reading_of_id` was, and still is. What the old key actually permitted was a *second* `legal` row differing only by locale/script also being canonical — which is the same content in two scripts, i.e. precisely the case `reading_of_id` + `name_type='romanization'` is designed for. Model it that way.

Admin support for the edge: `people_reading_target_search.py` powers the "reading of" picker; `_name_row.html` / `_name_form_row.html` surface the parent name.

**Merge preserves families through dedup (#309).** Because the edge is `ON DELETE CASCADE`, a naive merge that deletes the loser's parent row as a duplicate of a winner row would take the reading with it — even though the reading is not a duplicate of anything the winner holds. `merge_person_into` therefore re-points the loser's `reading_of_id` children at the winner's surviving equivalent **before** the dedup DELETE, keyed on the same name-identity match the DELETE uses (both share `_NAME_IDENTITY_MATCH_SQL` so they can't drift). Scope of #309: the automatic dedup DELETE only.

**Curated drops keep parents of kept children (#323).** The curated `keep_name_ids` drop (the preview-modal keep/drop selection, #255) shares the same `ON DELETE CASCADE` exposure, but only asymmetrically. Dropping a reading whose parent is kept, or dropping both, are deliberate admin choices and stay as-is. The one case that is *not* an informed choice is a **kept** reading whose parent is left unchecked — dropping the parent would silently destroy the explicitly-kept child. So the curated DELETE extends its keep-set to the parents of any kept `reading_of_id` child — keyed on the FK's presence, not `name_type`, so it covers `reading`, `romanization` **and** `mrz` alike; the dedup DELETE below may still collapse that kept parent into a winner equivalent, at which point the #309 re-point moves the reading. The preview modal surfaces the linkage on both actionable rows — a `(reading of "…")` note on each child and a `(a reading points at this)` note on each parent — both relational, so they stay accurate regardless of which boxes the admin toggles — so the dependency is visible before submission.

#### Canonical auto-promotion on observation (#308)

`write_names` guarantees that a person with an eligible name ends up displayable, symmetric with the long-standing org behaviour:

- **Client hint present** (`is_canonical=true` on some name) — that name claims the slot, guarded by `NOT EXISTS (… person_id AND is_canonical)`; never displaces an existing canonical. A hint on a `deadname` is ignored (`NEVER_CANONICAL_NAME_TYPES`) rather than raising `CheckViolationError` and failing the whole observation.
- **No hint** — PM auto-promotes exactly one name per write, picked by `_PERSON_NAME_TYPE_PRIORITY` (`preferred` > `legal` > `alias` > …). `NO_AUTO_CANONICAL_NAME_TYPES` (`deadname`, `mrz`, `romanization`, `reading`) is never auto-promoted.

Eligibility is by **identity, not name string**: the promotion target is an index into the payload, and the append-dedup key is `(name, name_type, locale, script)`. Matching on the bare string let an `mrz` row claim the display slot ahead of a `legal` one purely by list order, and silently discarded the second claim.

Clients are not required to assert `is_canonical` — omitting it is the correct conservative default when the client can't tell whether it is creating a new person or matching an existing one. The `NOT EXISTS` guard makes displacement impossible either way.

**Heal on re-observation.** Auto-promotion fires only on *newly inserted* rows, and `write_names` skips names that already exist — so a person already canonical-less would never recover, since the steady-state client re-sends the same names every sync. `write_names` therefore ends the person branch with `_heal_person_canonical`, promoting the highest-priority eligible existing name whenever the person has no canonical. It also runs for observations carrying **no names at all**, so any observation touching a blank person repairs it, and is skipped when an insert in the same call already claimed the slot.

The heal is a read-only probe plus a guarded `UPDATE`. The probe cannot violate a constraint, so the steady-state case (already-displaying person, unchanged names) stays at one round trip with no savepoint. The `UPDATE` takes a savepoint: a concurrent commit between the two statements can still collide on `uq_person_canonical_name`, and without recovery that error propagates out of `write_names` and aborts the whole observation, which the public route reports as `db_constraint_violation` — discarding links, addresses, role assignments and events over a cosmetic display-name repair.

`scripts/backfill_person_canonical_names.py` repairs people who predate this, selecting with the **same ladder** so both repair paths choose the same row (`test_backfill_matches_heal_choice`).

#### BCP 47 / ISO 15924 lookup tables (issue #123, Phase 2-prep)

`person_names.locale` and `person_names.script` are FK-constrained to `bcp47_locales(code)` and `iso15924_scripts(code)` respectively. The lookup tables are seeded by `scripts/seed_locales_scripts.py` from the `langcodes` and `pycountry` libraries, which live in the `seed` dependency group only — request-path code never imports them.

Validation layering:

| Layer | What it does | Source of truth |
|---|---|---|
| Admin form (Pydantic) | Strips whitespace, rejects empty strings | UI ergonomics |
| Database FK | Rejects unregistered codes (`'xx-XX'`, `'Xxxx'`) | Authoritative |
| Seed script (`langcodes` + `pycountry`) | Populates the lookup tables; runs once per env | Registry mirror |

No curated default-set is maintained — the typeahead's empty state shows a placeholder and narrows the full table by user keystrokes. The human-readable column differs by table: locales use `display_name`, scripts use `name`. So:

- locales: `code ILIKE '%q%' OR display_name ILIKE '%q%'`
- scripts: `code ILIKE '%q%' OR name ILIKE '%q%'`

pg_trgm GIN indexes are present on both columns of both tables (Postgres' planner may still pick Seq Scan at current row counts; the index is load-bearing as the data grows). Re-seed at any time to pick up registry updates: `uv run --group seed scripts/seed_locales_scripts.py`.

ON UPDATE CASCADE is set on both FKs, so a registry-driven `code` rename propagates to existing person_names rows. ON DELETE NO ACTION (default) blocks lookup-row deletion when referenced — the registry doesn't shrink, so this is correct.

A symmetric FK on `bcp47_locales.script → iso15924_scripts(code)` (same `ON UPDATE CASCADE`) keeps locale rows consistent with the script registry. Phase 2b code may join `bcp47_locales.script → iso15924_scripts.code` to enrich locales with their script's `name`/`numeric_code` without defensive existence checks.

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

## Unique Indexes (PostgreSQL 15+)

- Role uniqueness is **split by whether the role has a jurisdiction** (#261):
  - `uq_role_structural` — `roles(organization_id, role_type_id, jurisdiction_id, qualifier) NULLS NOT DISTINCT WHERE jurisdiction_id IS NOT NULL AND archived_at IS NULL` — the identity of a role with a jurisdiction is chamber + role type + district + position; `NULLS NOT DISTINCT` makes a NULL `qualifier` unique per district (one senator).
  - `uq_role_org_title` — `roles(organization_id, lower(title)) WHERE jurisdiction_id IS NULL AND archived_at IS NULL` — a role without a jurisdiction keeps title-based identity.
  - A role with a jurisdiction must name its role type: `chk_role_jurisdiction_needs_role_type` (`jurisdiction_id IS NULL OR role_type_id IS NOT NULL`).
- `uq_role_assignment_person_role_start` — `role_assignments(person_id, role_id, start_date) NULLS NOT DISTINCT WHERE archived_at IS NULL`
- All created inside `DO … EXCEPTION WHEN unique_violation` blocks so `apply_schema` is safe on DBs with existing duplicates (logs WARNING instead of raising)
- Schema tests for `uq_role_org_title` are guarded by a `require_uq_role_org_title` fixture that skips when the index is absent
- `deduplicate_roles.py` collapses in two passes matching the split: roles without a jurisdiction by `(organization_id, lower(title))`, roles with a jurisdiction by `(organization_id, role_type_id, jurisdiction_id, qualifier)` — it never merges distinct roles that share a title
- **Titles of roles with a jurisdiction are PM-curated (#267):** `title` is not part of such a role's identity (matching keys on the tuple), so the observation may omit it — on create `resolve_role` synthesizes the canonical title from the tuple via `src.core.role_title` (the single formatter, shared with `scripts/generate_wa_roles.py`) and **prefers it over any supplied title**, so upstream observers can't drift PM's curated form. A supplied title is used only as a fallback when it can't be synthesized; a titleless role that can't be synthesized (unknown `role_type` / non-`usa-wa-ld` district) is rejected `role_title_unavailable`.
- **`role_types` catalog is publicly observable (#268):** `GET /api/v1/role-types` returns the classifier vocabulary (`id, slug, display_name, expects_jurisdiction, requires_qualifier`) so producers discover the `role_type` slugs instead of hardcoding them. `expects_jurisdiction` is an **advisory** hint that the office is normally attached with a jurisdiction — `resolve_role` does not enforce it. An unknown `role_type` slug is already rejected (`role_type_not_found`), so the endpoint's role is to prevent a *valid-but-wrong* slug (which would mint a duplicate role).
- **`requires_qualifier` is enforced (#273):** a per-position office (e.g. `state_representative` — House seats are Position 1/2) sets `requires_qualifier=TRUE`; `resolve_role` **rejects** a jurisdictional observation of it with a missing/blank `qualifier` (`qualifier_required`) rather than minting a positionless seat (the #267 spurious-mint). `state_senator` is `FALSE` (one per district — `NULLS NOT DISTINCT` keeps the NULL-qualifier senator unique). Unlike `expects_jurisdiction`, this is a hard guard, not a hint. **Defense in depth:** a DB trigger (`trg_role_requires_qualifier` → `enforce_role_requires_qualifier()`) enforces the same rule at the data layer, so admin/direct-`INSERT` paths that bypass `resolve_role` also can't mint one (raises `check_violation`). Can't be a `CHECK` — it references `role_types`.

**Bootstrap sequence for a dirty DB:** (1) run `scripts/deduplicate_roles.py --execute` to collapse duplicates, (2) re-run `apply_schema` (or restart the service) to create the indexes, (3) verify with `\d roles` / `\d role_assignments` in psql.

**Inline constraint additions need a companion DO block (#307 CR round 2).** `CREATE TABLE IF NOT EXISTS` no-ops on an existing table, so a `CHECK`/`CONSTRAINT` added inline to the CREATE reaches only fresh DBs — prod silently lacked `entity_events_event_year_check` and `chk_at_requires_year` for exactly this reason. Any inline constraint change must ship with an idempotent `DO $$ … $$` migration that ADDs the constraint when absent (guard on `pg_constraint` by `conrelid`/`conname`) and replaces it when the clause is stale, wrapped in `EXCEPTION WHEN check_violation` → `RAISE WARNING` so `apply_schema` survives dirty data. Verify against prod (`pg_get_constraintdef`), not just the test DB — the test DB's table may be young enough to have gotten the inline form. Reference: the `entity_events` reconciliation block in `schema.sql`; regression harness: `tests/core/test_schema_constraint_migrations.py` (drop constraint → `apply_schema` → assert restored).

**ADD-when-absent, not just replace-if-stale (#312).** A replace-if-stale guard (`IF EXISTS (constraint AND check_clause NOT LIKE '…') THEN DROP+re-add`, the #168 idiom) no-ops when the constraint is *entirely absent* — so it never heals a table that predates the constraint. A #312 prod-vs-test sweep caught five more in that state (`field_confidence_entity_type_check`, `import_provenance_entity_type_check`, and the three `import_batches_*_count_check`s): the entity_type pair had only the #168 replace-if-stale guard, the count checks had no reconciliation at all. Their #312 blocks ADD the current full shape when absent (guard on `pg_constraint` presence). Note the contrast: #176's unconditional `DROP CONSTRAINT IF EXISTS + ADD` heals absent *and* stale in one statement (that's why `contact_methods`/`entity_addresses` never drifted), but it hard-aborts `apply_schema` on a violating row — prefer the `EXCEPTION WHEN check_violation → RAISE WARNING` DO-block form on any constraint that could meet dirty data at deploy time.

**The drift class covers *modifiers*, not just presence (#315).** The same `IF NOT EXISTS` no-op masks an inline modifier added after the table shipped — an FK's `ON DELETE` action, a CHECK clause body — even when the constraint itself is present. Prod's `entity_events_event_place_address_id_fkey` sat at plain NO ACTION (`confdeltype='a'`) while the code's inline `REFERENCES addresses(id) ON DELETE SET NULL` never applied, so hard-deleting an address referenced by an event *errored* in prod but nulled the ref on fresh DBs. Reconciliation for this variant keys on the wrong *action*, not absence — `SELECT conname WHERE contype='f' AND confdeltype <> 'n'` then DROP+re-add with the intended action (idempotent: a correct FK is untouched). Because the constraint is present, an absence-only guard (and any presence-only diff) no-ops here — the drift is only visible by comparing the **full `pg_get_constraintdef`**. Reference: the `entity_events_event_place_address_id_fkey` block in `schema.sql`; harness: `tests/core/test_schema_constraint_migrations.py::test_apply_schema_repairs_fk_on_delete_action` (reproduces the wrong-action shape, not mere absence).

**Continuous guard: prod-vs-reference parity audit (#315).** A companion DO block only heals prod on deploy *if someone wrote it* — the drift above was caught twice by manual CR sweeps, after it had already sat in prod. `scripts/audit_schema_constraint_parity.py` (daily `power-map-schema-parity.timer`, exit 3 on drift) snapshots every constraint's full `pg_get_constraintdef` on a reference DB (`PARITY_REFERENCE_URL`, default `TEST_DATABASE_URL`) and on prod, and fails when prod is missing or disagrees on any reference constraint — catching drift from *any* source (manual DDL, partial migration, a deploy whose `apply_schema` no-op'd a new inline constraint), FK actions included. Logic + the reference-fidelity caveat: `src/core/schema_parity.py`. Note why a fresh-DB-only unit guard can't replace it: most inline constraints (measured: 73) have **no** reconciliation block — they shipped correctly with their `CREATE TABLE` and never needed one — so "apply_schema must restore every dropped constraint" would flag all 73; the fresh DB structurally cannot tell "inline-only but fine" from "inline-only and drifted in prod." Only a comparison against real prod state can. The drop-reapply harness covers the *reconciled* subset; the parity audit covers everything else.

**The parity audit also covers functions & triggers (#331).** The same silent-drift window applies to the `CREATE OR REPLACE` function/trigger surface — the change-feed `touch_parent_*` functions + `trg_touch_entity_*` triggers that emit `entity_changes` (#327). They self-heal on the next `apply_schema` (every `systemctl restart power-map`), but a partial apply or hand-applied hotfix can leave a stale body running undetected. So the audit snapshots functions (`pg_get_functiondef`, keyed on signature so overloads stay distinct; **extension-owned** and non-plain aggregate/window/procedure functions excluded via a `pg_depend deptype='e'` anti-join — `pg_trgm`/`vector`/`unaccent` install hundreds into `public` that aren't ours to guard) and triggers (`pg_get_triggerdef`, `NOT tgisinternal`, same extension anti-join) alongside constraints, with the per-kind report namespaced `constraint.*`/`function.*`/`trigger.*`. **PG-version caveat:** `pg_get_functiondef`/`pg_get_triggerdef` formatting can legitimately differ across PG majors, so those two kinds are **skipped on a reference-vs-target major mismatch** (loud WARNING) rather than misreported as body drift — keep `PARITY_REFERENCE_URL` on prod's major. Constraints are version-stable and always diff.

### Role-type vocabulary — governance (#266)

`role_types` is a **flat, global aggregation key** — its stated purpose is to let "all X across orgs/jurisdictions" aggregate without matching free-text titles. It is *not* a label for every distinct office. New slugs are gated by four rules so the catalog doesn't proliferate into a WA-flavored, domain-ambiguous grab-bag:

1. **Aggregation test — a slug exists only if you would query "all of them" across orgs/jurisdictions.** If the answer is no, it stays a free-text `roles.title` and gets no type. A one-off, non-recurring position doesn't earn a type until it recurs; a coarse *category* whose members you'd query together (`chamber_officer` covers a chamber's institutional officers — Secretary of the Senate, a future Chief Clerk) does earn one even at one row today, because the category — not the single title — is the aggregation. This is the primary guardrail against proliferation.
2. **Domain-prefix convention.** Every non-jurisdictional slug is prefixed by the org-kind it attaches to: `committee_`, `chamber_`, `legislature_`, `party_` (corporate/advocacy `org_*` is reserved for #303). The prefix disambiguates a bare noun that means different things per domain (a `member` on a committee vs a party) and yields coarse rollups for free (`WHERE slug LIKE 'legislature_%'`) — so you rarely need a separate coarse type. The two jurisdictional seat types (`state_representative`, `state_senator`) predate this convention and are **grandfathered unprefixed** — renaming a public-API-visible slug (`GET /api/v1/role-types`) is breaking and buys nothing.
3. **Concept in the type, jurisdiction label in the renderer.** Types stay jurisdiction-neutral; WA-specific titles live in `src/core/role_title.py`. Don't mint `wa_speaker` — the type is the concept, the label is rendered (or, for coarse types, carried by the free-text title).
4. **Coarse where a long tail exists.** When distinct titles share a domain but few would ever be aggregated individually (the committee-staff tail: `Counsel`, `Research Analyst`, `Fiscal Coordinator`, …), use one coarse type (`legislature_staff`) + the specific free-text title, not a slug per title. Coarse types set `expects_jurisdiction=FALSE`; the specific office is not structurally recoverable (accepted tradeoff — e.g. `chamber_leader` aggregates "all chamber leaders" but not "all Speakers cross-state").

**Reserved, not seeded.** Obvious near-term peers of a seeded concept (`chamber_majority_leader`, `chamber_minority_leader`, `chamber_president_pro_tempore`, `chamber_speaker_pro_tempore`) are documented here but **seeded only on first observation** — don't front-load the vocabulary ahead of data (rule 1).

**Current catalog (post-#266):** `state_representative`, `state_senator` (jurisdictional seats, grandfathered); `chamber_leader`, `chamber_officer` (coarse); `committee_chair`, `committee_vice_chair`, `committee_ranking_member`, `committee_assistant_ranking_member`, `committee_member`; `legislature_staff` (coarse); `party_member` (coarse). The pre-#266 coarse `member` was split into `committee_member` + `party_member` and dropped.

### Entity search — last-token prefix FTS (#316)

Every `search_tsv @@ …` predicate (orgs, people, roles, role-assignments — both the public `search` endpoints and the admin list `*_queries.py` filters) goes through **`pm_prefix_tsquery(cfg, q)`**, never bare `plainto_tsquery`. It is the single source of truth for query-side FTS: it reuses `plainto_tsquery` for normalization (unaccent, punctuation split, stopwords) and appends `:*` to the last lexeme so the trailing token matches as a **prefix** — `"Ollie Gar"` matches `"Ollie Garrett"`, and single-token queries prefix too (`"Ja"` → `"Jane …"`). Prefix-only, not infix. It is `IMMUTABLE STRICT` (NULL in → NULL out, so nullable-`q` call sites like orgs-by-jurisdiction keep working) and injection-safe (plainto strips all tsquery operators before the `::text` round-trip). Known limitation: a partial *hyphenated* final token doesn't prefix-match (the `<->`-linked compound is required exact) — see `docs/PUBLIC_API.md`. When adding a new searchable entity, use `pm_prefix_tsquery` for the `@@` predicate; do not re-inline `plainto_tsquery`. Jurisdictions are the deliberate exception (ILIKE on name/slug, no `search_tsv`).

### `pg_trgm` extension

Enabled via `CREATE EXTENSION IF NOT EXISTS pg_trgm` in `apply_schema`. Required for org duplicate detection (`similarity()` function). Re-run `apply_schema` (or restart) to install on existing databases. Gracefully degrades to `org_dup_count = 0` if not installed.

---

## Ingestion

- EVTL pattern: Extract (CSV read) → Validate (Pydantic) → Transform (normalize fields) → Load (DB insert)
- `RowResult` envelope: `errors` = fatal (entity skipped), `warnings` = non-fatal (field skipped)
- `field_confidence` is append-only; query latest with `ORDER BY assessed_at DESC LIMIT 1`
- `import_batches.file_hash` is unique; re-running with same files reuses the existing batch
- Address standardization uses the external address-validator service when `ADDRESS_VALIDATOR_API_KEY` is set; falls back to local `usaddress` parsing otherwise
- `addresses.precision` indicates the specificity tier of the geocoded result (`street`, `postal`, `city`, `region`, `country`; NULL = unset or pre-geocoding historical record). Event place linkage (`entity_events.event_place_address_id`) requires city-level or finer (`city`, `postal`, `street`) — or NULL; `country`/`region` precision is rejected. See `EVENT_PLACE_PRECISIONS` in `src/core/types.py`.
- `VALIDATE_ADDRESSES=true` (or `--validate-addresses` CLI flag) enables the `/validate` endpoint
- `role_index` is pre-populated from the DB at pipeline startup (Pass 3) so re-runs are idempotent across batches
