# power-map Public API

**Schema and endpoint inventory:** `/docs` (Swagger UI, both dev and prod) is the authoritative reference for request parameters, response shapes, and per-endpoint descriptions. This document covers the meta-level contracts, auth model, and implicit behaviors that the OpenAPI spec does not capture.

Meta-level contracts: auth, scopes, rate limits, pagination, conditional requests and
shared observation-write behaviour. The change feed and subscriptions are in
`docs/CHANGE_FEED.md`; per-resource endpoint detail in `docs/API_ENTITIES.md`.

---

## Authentication


Every request requires `X-API-Key: <token>`. Missing header → 403; invalid key → 401.

Keys are stored as SHA-256 hashes — the raw token is never persisted after issuance. Valid requests refresh `last_used_at` on the key row (debounced to at most once per minute, #292); the maintainer can review per-key usage in the admin dashboard to identify inactive keys.

---

## Scope


Read endpoints are accessible with any valid key. Write endpoints require an additional per-key scope grant (e.g. `observations:write`). A key without the required scope receives a 403. Scope grants are managed by the maintainer via the admin dashboard.

| Scope | Used by |
|-------|---------|
| `observations:write` | `POST /*/observations` endpoints |
| `subscriptions:write` | `POST /api/v1/subscriptions`, `DELETE /api/v1/subscriptions`, `DELETE /api/v1/subscriptions/{entity_id}` |
| `voice_embeddings:write` | `POST /api/v1/people/{id}/embeddings`, `PATCH /api/v1/people/{id}/embeddings/{eid}`, `DELETE /api/v1/people/{id}/embeddings/{eid}`, `DELETE /api/v1/people/{id}/embeddings`, `POST /api/v1/people/{id}/embeddings/{eid}/restore` |
| `voice_embeddings:read` | `POST /api/v1/people/identify`, `POST /api/v1/people/verify`, `POST /api/v1/people/verify-batch`, `POST /api/v1/people/embeddings/presence`, `GET /api/v1/people/{id}/embeddings` — required for all biometric data reads |

---

## Rate Limits


Per-key token-bucket rate limiting is enforced at the application layer (#292). Each API key has two independent buckets:

| Bucket | Applies to | Sustained rate | Burst capacity |
|--------|-----------|----------------|----------------|
| read | `GET` / `HEAD`, plus the read-semantic POST endpoints: `POST /people/identify`, `POST /people/verify`, `POST /people/verify-batch`, `POST /people/embeddings/presence` (#310 — they carry a body for size reasons but only read) | 2 req/s | 120 |
| write | everything else | 1 req/s | 60 |

An exhausted bucket returns **`429 Too Many Requests`** with:

| Header | Meaning |
|--------|---------|
| `Retry-After` | Seconds until the next request will be accepted |
| `X-RateLimit-Limit` | Bucket burst capacity |
| `X-RateLimit-Remaining` | Tokens remaining (0 on a 429) |

On 429, back off for at least `Retry-After` seconds — hammering a drained bucket earns only more 429s. The limits are a backstop against runaway clients, not a precise quota: enforcement is per server worker, so short bursts may admit slightly more than the configured rate. Design your client for the documented numbers.

To reduce request volume in the first place: poll `GET /api/v1/changes` for deltas instead of re-fetching entities, and use `If-None-Match` conditional requests (see the caching sections) so unchanged resources cost a 304 instead of a full transfer.

---

## Request logging


For operational observability and debugging, the service records each `/api/v1/*` request: timestamp, key, method/path, status, and latency. For the observation-write and change-feed endpoints it additionally stores the **raw request and response bodies** (so submissions and their dispositions can be inspected). Records are retained for ~90 days, then pruned on the same window as the change feed. Your `X-API-Key` token is never stored — requests are attributed by the key's internal id.

---

## Key Lifecycle


No self-serve key management. To request, rotate, or revoke a key, open an issue or contact the maintainer. Include the `key_prefix` (first 8 characters of your raw token) so the correct row can be identified without the raw secret.

---

## Pagination — implicit behaviors


The `/docs` spec documents the `q`, `limit`, `offset`, `include_archived`, `identifier_type`, and `identifier_value` parameters. The following behavioral details are not captured there:

- **`count` is the page count, not the total.** `meta.count` is the number of items returned in this response. No total-dataset-size field exists.
- **`limit` is server-clamped to 50.** Values above 50 are silently reduced; the cap is enforced in code, not in schema validation, so the OpenAPI spec shows no upper bound.
- **Empty `q` short-circuits (q-only path).** When `q` is absent or whitespace-only and `jurisdiction` is not provided, the endpoint returns an empty result set immediately — no DB query is issued. A non-empty `q` is required for meaningful results on this path.
- **`jurisdiction` returns results with an empty `q`.** When `jurisdiction` is provided, omitting or blanking `q` returns the full jurisdiction-scoped cohort. `q` acts as an additional name filter on top of the jurisdiction scope, not as a precondition for the query to execute.
- **`identifier_type` + `identifier_value` take precedence over `q`.** When both are supplied, they perform an exact identifier lookup and return at most one result with `has_more: false`; `q`, `limit`, and `offset` are accepted but have no effect.
- **`include_archived: false` is a silent filter.** Archived entities are excluded by default with no signal in the response that a matching archived record exists. Pass `include_archived=true` to include them. This is the deliberate exception to the #306 rule that status filters must not silently hide search matches: for a machine caller the filter is an explicit, documented opt-in, unlike the admin lists' implicit default status tab (see `docs/ADMIN.md` "List status filters & search discoverability").
- **`q` uses full-text search with last-token prefix matching, not substring matching.** The `q` parameter is tokenized at word boundaries; all but the final token must match a whole word, and the **final token matches as a prefix** (`:*`, #316) — so `"approp"` matches `"Appropriations"` and `"Ollie Gar"` matches `"Ollie Garrett"`. This treats the trailing token as "still being typed," which is what a typeahead consumer wants; a single-token query is therefore also a prefix (`"Ja"` → `"Jane …"`). It is prefix-only, not infix: `"llie"` does not match `"Ollie"`. One caveat: a partial **hyphenated** final token matches only once fully typed — `"Anne-Mar"` does not match `"Anne-Marie"`, but `"Anne-Marie"` does (the hyphenated compound must match as a whole word; only its last segment carries the prefix). Multi-word queries are AND. Punctuation is stripped so `"Jr."` and `"Jr"` match identically; person name search is accent-insensitive (`"Hernandez"` matches `"Hernández"`). Results are ordered by relevance rank, then name, then a stable `id` tiebreaker — so offset pagination is complete and duplicate-free even when many results share a rank and name.
- **`q` searches all name variants and notes, not just canonical names.** Organizations: all name variants (legal, dba, former), all acronyms, and notes. People: all public name variants and notes (hidden and legal-only names are excluded from the search index).

Iteration pattern:

```python
offset, limit = 0, 50
while True:
    resp = client.get("/api/v1/orgs/search", params={"q": "<term>", "limit": limit, "offset": offset})
    page = resp.json()
    process(page["data"])
    if not page["meta"]["has_more"]:
        break
    offset += limit
```

---

## Caching — detail endpoints


Detail endpoints (`GET /api/v1/orgs/{id}`, `GET /api/v1/people/{id}`, `GET /api/v1/jurisdictions/{id}`, `GET /api/v1/roles/{id}`, `GET /api/v1/assignments/{id}`) return caching headers on every `200` response:

| Header | Value |
|--------|-------|
| `ETag` | `"<id>-<updated_at_ms>"` — strong ETag based on last-update timestamp |
| `Last-Modified` | RFC 9110 §5.6.7 IMF-fixdate — e.g. `Wed, 05 Aug 2026 12:30:45 GMT`, always UTC |
| `Cache-Control` | `no-cache` — revalidation required before serving from cache |
| `Vary` | `X-API-Key` |

Send `If-None-Match: <etag>` to receive `304 Not Modified` when the record is unchanged. `Vary: X-API-Key` means shared proxy caches store a separate entry per key — if multiple services share one key they share a cache entry.

---

## Conditional requests


**This section applies API-wide, not just to the detail endpoints above** (#392). Every endpoint that advertises an `ETag` shares one parser, so the forms below behave identically on all of them.

| Endpoint | Validator |
|---|---|
| `GET /orgs/{id}`, `/people/{id}`, `/roles/{id}`, `/assignments/{id}`, `/jurisdictions/{id}` | `"<id>-<updated_at_ms>"` |
| `GET /people/{id}/events`, `/orgs/{id}/events` | watermark |
| `GET /citations/{entity_type}/{entity_id}` | watermark |
| `GET /assignments/{pm_assignment_id}/relationships` | watermark |
| `GET /jurisdictions/{id}/relationships` | watermark |
| `GET /jurisdictions/{id}/lineage` | content hash |
| `GET /role-types`, `/link-types`, `/entity-event-types`, `/entity-identifier-types` | content hash |

**Watermark** = `count(*)` + `max(updated_at)` over the *visible* set, with every filter and the `limit`/`offset` window baked into the tag. Count catches a row entering or leaving the filtered set — a retract archives rather than deletes, so the row's own `updated_at` bump is invisible once the default (active-only) filter excludes it — and `max` catches an in-place edit. A tag from one filter/window never revalidates against another's.

**Content hash** = a digest of the returned rows, used where no single table's `updated_at` covers the response — a catalog whose table has none, or `/lineage`, whose result is a recursive traversal over jurisdictions *and* their lineage edges. Note `/lineage` deliberately does **not** bake `depth` into the tag: the hash tracks what the traversal actually returned, so two depths reaching the same set share a tag and a deeper reach gets its own. Exact by construction: an in-place rename of a catalog entry invalidates, which a `count(*)` + `max(created_at)` tag would not. It saves serialization and transfer, not the query.

Search and list endpoints (`/people/search`, `/orgs/search`, `/roles`, `/assignments`, `/jurisdictions`) deliberately have **no** validator — a collection validator over a filtered, paginated, ranked result set costs about as much as serving the page. `/changes` is a cursor feed and needs none.

### `If-None-Match` forms accepted

The header is parsed per RFC 9110 §13.1.2, so an interposed proxy's rewriting doesn't defeat revalidation. All of these revalidate:

| Form | Example |
|------|---------|
| A single tag | `If-None-Match: "01J…-1754400000000"` |
| A comma-separated list — any member matching wins | `If-None-Match: "stale", "01J…-1754400000000"` |
| A weak tag — `If-None-Match` on GET uses **weak comparison**, so `W/"x"` and `"x"` match | `If-None-Match: W/"01J…-1754400000000"` |
| `*` — matches any current representation of an existing resource | `If-None-Match: *` |

A comma inside a quoted tag is part of the tag, not a separator.

**`If-Modified-Since` is not honored.** Revalidation is ETag-only: a request carrying `If-Modified-Since` (and no matching `If-None-Match`) always receives a full `200`, despite the `Last-Modified` header we send. `Last-Modified` is informational — always revalidate with `If-None-Match`.

**A 304 still costs a rate-limit token.** The limiter runs in the auth dependency, ahead of the handler, so conditional requests reduce transfer and serialization — not your effective poll ceiling. Use `GET /api/v1/changes` to lower request *count*.

---

## Timestamps


Every timestamp in a response body is ISO 8601 UTC with a `Z` suffix and microsecond precision — `2026-08-14T12:00:00.123456Z`, never `+00:00` and never a local offset. The spec types them `string` / `format: date-time`, and a field that is nullable in the spec is the only one that can arrive as `null`. Calendar dates (`established_on`, `effective_start`, and the like) are plain `YYYY-MM-DD` with no time part. `Last-Modified` is the one exception to the format, being an HTTP-date by RFC 9110 (see **Caching**).

Uniformity is enforced in CI, not by convention (#440), so it is safe to parse on: a service reading `Z` from one endpoint will read it from all of them.

---

## Versioning


Path-versioned (`/api/v1/`). Breaking changes introduce a new prefix (`/api/v2/`). Additive changes (new optional fields, new endpoints) may appear within a version without notice.

---

## Observation writes — shared behavior


All `POST /*/observations` endpoints return an `ObservationResponse` with the following fields:

| Field | Type | Notes |
|-------|------|-------|
| `disposition` | `string` | `"new"`, `"auto-attached"`, or `"rejected"` |
| `entity_id` | `string \| null` | ULID of the matched or created entity; `null` on `rejected` |
| `entity_type` | `string \| null` | Entity type string; `null` on `rejected` |
| `reason` | `string \| null` | Human-readable rejection cause; `null` on non-rejected responses |
| `unapplied` | `list[string] \| null` | #311: fields supplied but not applied on a natural-key auto-attach (see the assignments section). Currently populated only by assignment observations; `null` elsewhere and on clean attaches |
| `events` | `list[EventObservationResult] \| null` | #321: per-event dispositions when the payload carried `events`. `null` when none submitted. On this all-or-nothing embedded path a rejected event raises → whole observation `rejected`, so `events` is present only on full success. Each item: `{disposition, event_id?, reason?}` |
| `citations` | `list[CitationObservationResult] \| null` | #319: per-citation dispositions when the payload carried `citations[]`; `null` when none were submitted, and present only on full success (the embedded path is all-or-nothing) |
| `attached_archived` | `bool \| null` | #477: `true` when an `auto-attached` addressed an **archived** row rather than a live one — the #391 anti-resurrection attach, or a re-emitted `op="retract"`. Absent/`null` otherwise, never `false`. Treat `true` as "PM asserts this never happened — stop re-emitting it"; `unapplied` never carried that meaning. Currently populated only by assignment observations; the per-item results for events / citations / relationships carry their own. See [OBSERVATIONS.md](OBSERVATIONS.md) §"Anti-resurrection is labelled" |

**`reason` is a diagnostic aid, not a stable API contract.** Its format (e.g. `"unknown_identifier_type: 'org_wa_legislature_chamber'"`) may change across releases. Do not pattern-match on specific reason strings in production code; use it for logging and debugging only. To avoid `unknown_identifier_type` in the first place, discover the registered slugs from `GET /api/v1/entity-identifier-types` instead of hardcoding them (#459) — value conventions per slug are in [OBSERVATIONS.md](OBSERVATIONS.md).

**Event `reason` slugs are a contract, though.** Unlike the top-level `reason`, per-event `EventObservationResult.reason` (#321) is a stable machine-readable slug: one transient — `linked_entity_unresolved` (self-heals once the linked entity anchors) — and the rest terminal: `identity_immutable`, `event_not_found`, `provenance_conflict`, `applies_to_mismatch`, `missing_required_field`, `unknown_event_type`, `invalid`.

### Event observations — refine-in-place & partial-success (#321)

Events reach PM two ways. **Embedded** (`events: [...]` in a `POST /*/observations` payload) is all-or-nothing. The **event-native** surface `POST /api/v1/orgs/{org_id}/events/observations` (`observations:write`; body `{ "events": [...] }`) is **partial-success** — each event lands under its own savepoint and returns its own disposition in `EventObservationsResponse.results` (`{disposition, event_id?, reason?}`), so one rejected event never rolls back its siblings. 404 if the org id doesn't resolve.

Each event item is the shape listed under people/org observations, plus:

| Field | Notes |
|-------|-------|
| `pm_event_id` | optional. When set, **refines an existing event in place**: the mutable set (`date`/`notes`/`place`/`visibility`) is replaced; identity (`event_type`, `linked_entity`) is immutable — a change there → `identity_immutable`. An unchanged re-emit is a no-op (`auto-attached`, no clock bump). Gated on `source_key_id` provenance (`provenance_conflict` on a foreign source). Absent → natural create with content dedup. |
| `op` | `observe` (default) or `retract` (#322). `retract` **archives** the `pm_event_id` event (`archived_at`, never hard-delete) → `retracted`; the outbox emits so subscribers drop the anchor. The only correction for a mis-linked **dateless** linked event (`succeeded_by`/`split_from`/`merged_with`), so a re-link = create-new + retract-old. Requires `pm_event_id` (else `invalid`); `event_type` must match the stored row (else `identity_immutable`); same-or-NULL provenance (`provenance_conflict`); refine payload ignored. Re-retracting an already-archived event is a no-op (`auto-attached`, no clock bump). |

Per-event `disposition` ∈ `new｜auto-attached｜updated｜retracted｜rejected`. The `succeeded_by` event type (org renamed-continuity link) lives on the **predecessor**, `linked_entity_id` → successor; a `succeeded_by` whose successor isn't anchored yet returns `linked_entity_unresolved` and self-heals on a later cycle.

---

## Citations — source provenance (#319)


A **citation** attaches human-checkable evidence (`url` / `title` / `excerpt` / `accessed_at`) to an entity or one of its fields — "where did this fact come from". Citable entity types: `organization`, `person`, `role`, `role_assignment`, `jurisdiction`, `person_name`, `entity_event`.

**Scopes:** `citations:write` (observe/retract), `citations:read` (read).

### Write — two transports

- **Embedded** — a `citations: [...]` array on a `POST /orgs/observations` or `POST /people/observations` payload, attached to the resolved entity. All-or-nothing with the parent (a rejected citation → whole observation `rejected`); per-citation dispositions echo in `ObservationResponse.citations`.
- **Citation-native** — `POST /api/v1/citations/{entity_type}/{entity_id}/observations` (`citations:write`; body `{ "citations": [...] }`), **partial-success**: each claim lands under its own savepoint and returns its own `{disposition, citation_id?, reason?}` in `results`. 422 if `entity_type` is not citable.

Each citation item:

| Field | Notes |
|-------|-------|
| `field_name` | optional. NULL = whole-entity citation; non-NULL must be in the entity's citable-field allowlist (else `citable_field_unknown`). |
| `url` | optional (offline sources). One of `url`/`title` is required. |
| `title` / `excerpt` / `accessed_at` | mutable payload, **full-replace** — see below. `accessed_at` is ISO-8601 (`Z`). |
| `pm_citation_id` | optional. Set → id-addressed refine of the mutable payload; identity (`entity`, `field_name`, `url`) is immutable (`identity_immutable`). Absent → natural-key observe: identity `(entity, field_name, url)` → refine the matched active row or create. |
| `op` | `observe` (default) or `retract`. `retract` **archives** the `pm_citation_id` row → `retracted` (requires `pm_citation_id` else `invalid`; supplied `url`/`field_name` must match else `identity_immutable`; already-archived → `auto-attached` no-op). Re-observing retracted content stays retracted (anti-resurrection). |

**Full-replace payload.** A refine (id-addressed *or* natural-key) writes the whole mutable set (`title`, `excerpt`, `accessed_at`) — a field omitted from the claim is **cleared to NULL**, same model as event refine. Always send the complete payload on every observe; do not send a partial refine expecting the other fields to persist. `url` and `title` are only required on a genuine **create** (`missing_required_field` — a refine of an existing row is exempt, its identity is already pinned).

Identity uses `NULLS NOT DISTINCT`: at most one URL-less citation per `(entity, field)`. Writes are gated on `source_key_id` (same-or-NULL, else `provenance_conflict`). Per-citation `disposition` ∈ `new｜auto-attached｜updated｜retracted｜rejected`. Reason slugs: transient `entity_unresolved`; terminal `identity_immutable`, `citation_not_found`, `provenance_conflict`, `citable_field_unknown`, `missing_required_field`, `invalid`.

### Read — `GET /api/v1/citations/{entity_type}/{entity_id}`

`citations:read`. Query: `field_name` (narrow to one field), `include_archived` (default false), `limit`/`offset`. Standard `{data, meta}`, newest first (`created_at DESC, id DESC` — unique tail).

---

## Health check


`GET /api/v1/` returns `{"status": "ok", "version": "v1"}` when authenticated. Use it to confirm key validity before hitting data endpoints.

Unauthenticated probes live at root level, outside `/api/v1` (#343) — not part of the keyed API surface, exempt from rate limits and request logging:

- `GET /health` — liveness: `{"status": "ok", "build": "<version>"}`; no external calls.
- `GET /ready` — readiness: bounded DB pool check; `200 {"status": "ok"}` or `503 {"status": "unavailable", "reason": "no_pool" | "pool_timeout" | "db_error"}`.

---

## Change feed & subscriptions

The polled change feed and webhook subscriptions live in `docs/CHANGE_FEED.md` —
one subject, and the half of the API a consumer integrates against rather than
queries.
