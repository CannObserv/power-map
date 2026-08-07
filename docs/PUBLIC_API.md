# power-map Public API

**Schema and endpoint inventory:** `/docs` (Swagger UI, both dev and prod) is the authoritative reference for request parameters, response shapes, and per-endpoint descriptions. This document covers the meta-level contracts, auth model, and implicit behaviors that the OpenAPI spec does not capture.

Meta-level contracts: auth, scopes, rate limits, pagination, conditional requests,
subscriptions, the change feed, and shared observation-write behaviour. Per-resource
endpoint detail lives in `docs/API_ENTITIES.md`.

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
| `GET /role-types`, `/link-types`, `/entity-event-types` | content hash |

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

## Versioning


Path-versioned (`/api/v1/`). Breaking changes introduce a new prefix (`/api/v2/`). Additive changes (new optional fields, new endpoints) may appear within a version without notice.

---

## Subscriptions


Before receiving events from the change feed, a key must subscribe to the entities it cares about.

### Endpoints

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `GET` | `/api/v1/subscriptions` | API key | List subscriptions for the calling key. Params: `entity_type` (filter), `limit` (max 500, default 50), `offset`. |
| `POST` | `/api/v1/subscriptions` | `subscriptions:write` scope | Bulk-register entity IDs. Idempotent — already-subscribed IDs are counted, not errored. Unknown IDs returned in `not_found`. |
| `DELETE` | `/api/v1/subscriptions/{entity_id}` | `subscriptions:write` scope | Remove one subscription. 404 if not subscribed. |
| `DELETE` | `/api/v1/subscriptions` | `subscriptions:write` scope | Bulk-remove subscriptions. Silently ignores unknown IDs. |
| `GET` | `/api/v1/subscriptions/discover` | API key | Graph-traversal discovery of entities to subscribe to. |

### POST /subscriptions — request

```json
{ "entity_ids": ["01JVBN...", "01JVBP..."] }
```

### POST /subscriptions — response

```json
{
  "registered": 2,
  "already_subscribed": 0,
  "not_found": []
}
```

`not_found` lists IDs that don't resolve to any live or deleted entity. The rest of the batch still applies.

### GET /subscriptions — response shape

```json
{
  "data": [
    { "entity_id": "01JVBN...", "entity_type": "person", "created_at": "2025-06-01T12:00:00.000000Z" }
  ],
  "meta": { "limit": 50, "offset": 0, "count": 1, "has_more": false }
}
```

Ordered by `(created_at, entity_id)` — a stable total order (`entity_id` is unique per key). Offset pagination over the full list is deterministic and complete even when many rows share a `created_at` (e.g. a bulk subscribe), so a consumer can page from `offset=0` and enumerate its entire subscription set without duplicates or gaps.

### Bulk DELETE — request

Pass the JSON body via an HTTP DELETE with `Content-Type: application/json`:

```json
{ "entity_ids": ["01JVBN...", "01JVBP..."] }
```

Returns `204 No Content`. Unrecognized IDs are silently ignored.

### GET /subscriptions/discover — graph traversal

Traverses the PM entity graph from a root jurisdiction or organization and returns candidate entities to subscribe to. The client inspects results and POSTs selected IDs to `/subscriptions`.

**Parameters:**

| Parameter | Required | Notes |
|-----------|----------|-------|
| `root_type` | yes | `jurisdiction` or `organization` |
| `root_id` | yes | ULID or slug |
| `follow` | no | Comma-separated traversal steps (see below). Default empty = root entity only. |
| `limit` | no | Max 500, default 100 |
| `offset` | no | Default 0 |

**`follow` values** (applied in the order listed; each step requires prerequisites):

| Value | Traversal | Prerequisite |
|-------|-----------|--------------|
| `lineage` | Jurisdiction → connected jurisdictions via `lineage`-category edges (recursive) | `root_type=jurisdiction` |
| `affiliated_orgs` | Jurisdictions in scope → orgs with `governing` affiliation (only; `registered` and other types are excluded) | Jurisdiction in scope |
| `org_children` | Orgs in scope → child orgs via `parent_id` (recursive) | Organization in scope |
| `roles` | Orgs in scope → their roles | Organization in scope |
| `assignments` | Roles in scope → role_assignments | `roles` must precede |
| `people` | Assignments in scope → persons | `assignments` must precede |

Prerequisite violations → `422`. Example: `affiliated_orgs` before any jurisdiction is in scope.

**Response shape:**

```json
{
  "data": [
    {
      "entity_type": "organization",
      "entity_id": "01JXXX...",
      "display_name": "WA Senate",
      "hops_from_root": 2
    }
  ],
  "meta": { "limit": 100, "offset": 0, "count": 1, "has_more": false, "truncated": false }
}
```

Root entity is always included at `hops_from_root: 0`. `hops_from_root` counts traversal steps from the root, not graph depth within a step.

`meta.truncated: true` means the traversal hit the server-side entity cap (5,000) before completing all `follow` steps. Use a narrower `follow` chain, a smaller root, or filter results before subscribing.

**USA-WA setup example:**

```
GET /api/v1/subscriptions/discover
  ?root_type=jurisdiction&root_id=usa-wa
  &follow=lineage,affiliated_orgs,org_children,roles,assignments,people
```

---

## Change Feed


`GET /api/v1/changes` returns a subscription-filtered, outbox-ordered feed of entity mutations for sibling-service cache invalidation.

**Only events for entities the calling key has explicitly subscribed to are returned.** A key with no subscriptions receives an empty feed. Use `POST /api/v1/subscriptions` to register entities before polling.

### Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `after` | integer ≥ 0 | required | Outbox cursor (exclusive). Pass `0` to start from the beginning. |
| `limit` | integer 1–1000 | 50 | Max items per page |

### Response shape

```json
{
  "data": [
    {
      "seq_id": 4217,
      "entity_type": "person",
      "entity_id": "01JVBN...",
      "changed_at": "2025-06-01T12:00:00.000000Z",
      "change_kind": "updated",
      "merged_into": null
    },
    {
      "seq_id": 4301,
      "entity_type": "organization",
      "entity_id": "01JXCC...",
      "changed_at": "2025-06-02T09:15:00.000000Z",
      "change_kind": "deleted",
      "merged_into": "01JXCD..."
    }
  ],
  "meta": {
    "limit": 50,
    "count": 2,
    "has_more": false,
    "next_after": 4301,
    "min_seq": 118
  }
}
```

`change_kind` is `"updated"` for live or archived entities and `"deleted"` for hard-deleted or merged entities.

`merged_into` is `null` for genuine deletes and for all `"updated"` events. When `change_kind` is `"deleted"` and the entity was merged rather than hard-deleted, `merged_into` contains the id of the winner entity of the same type — the subscriber should re-anchor its reference to that id rather than retiring the entity locally.

`seq_id` is a strictly increasing integer from the append-only outbox log (`BIGSERIAL`). It is **monotonic**, not gapless — the log is an offset cursor, not a contiguous counter. Do not infer "missed events" from a gap between consecutive `seq_id`s: the sequence skips values for rolled-back or failed writes, and the id space is global across all entities while your feed is subscription-filtered, so consecutive delivered ids are expected to jump. See **Delivery semantics** below for the exactly-what-is-guaranteed contract (it is *at-least-once*, not exactly-once) — read it before building a consumer that trims or removes a reconciliation backstop.

`meta.min_seq` is the **oldest outbox `seq_id` still retained** — the prune horizon (`null` when the outbox is empty). It is global, not subscription-scoped: pruning is a global `changed_at`-based delete, so `min_seq` is the single id below which *any* event, subscribed or not, may already have been pruned. Use it to detect that a persisted cursor has fallen off the retention window — if your stored `after` is below `min_seq - 1`, events may have been pruned before you read them, so full-reconcile against the read endpoints (see **Falling off the horizon** below).

### Polling pattern

Pass `meta.next_after` from the previous response as `after` on the next poll:

```python
after = 0  # start from the beginning; persist this value between runs
while True:
    resp = client.get("/api/v1/changes", params={"after": after})
    page = resp.json()
    process(page["data"])
    after = page["meta"]["next_after"]
    if not page["meta"]["has_more"]:
        break
```

`next_after` is the `seq_id` of the last item returned, or echoes `after` when the page is empty. Because the cursor is exclusive (`>`), no deduplication is needed across pages.

### Entity types

| `entity_type` | Source table | Notes |
|---|---|---|
| `person` | `people` | |
| `organization` | `organizations` | |
| `jurisdiction` | `jurisdictions` | |
| `role` | `roles` | |
| `role_assignment` | `role_assignments` | |
| `person` / `organization` / `jurisdiction` / `role` / `role_assignment` | `deleted_entities` | `change_kind` is always `"deleted"`. `role` / `role_assignment` deletions began emitting tombstones in #277 |

### Implicit behaviors

- **Exclusive cursor.** `after` uses `>` semantics — `next_after` will never appear again in the next page.
- **Subscription-filtered.** Events for entities not in the subscription set are never returned, regardless of cursor.
- **Retention window — the feed is recent-changes, not a permanent event store.** Outbox rows older than **90 days** (age-based, pruned daily by `changed_at`; size-unbounded, issue #204) are deleted. Polling from `after=0` returns every *retained* event for your subscribed entities (the subscription filter applies at query time), but only within that window — it is **not** a full-history backfill. To obtain the **current state** of a newly subscribed entity (including one unchanged for longer than the window, which therefore has no recent outbox row), fetch it directly from its read endpoint, then poll incrementally from the returned `next_after`. A consumer dark longer than the retention window may miss intervening events and must full-reconcile against the read endpoints.
- **Falling off the horizon is silent — detect it with `min_seq`.** If your persisted `after` predates the prune horizon, `GET /changes` does **not** error or reset — it simply returns the oldest *surviving* rows with `id > after`. From the data alone you cannot distinguish "nothing changed since `after`" (empty page, `next_after` echoes `after`) from "events between `after` and the oldest retained row were pruned" (non-empty page whose first `seq_id` is far above `after`). Use `meta.min_seq` (the oldest retained id) as the explicit horizon: **on every resume, if your stored `after` is below `min_seq - 1`, treat it as "possibly lost the tail" and full-reconcile** against the read endpoints before trusting incremental deltas. (`min_seq` is global and conservative — it may prompt a reconcile even when none of the pruned events matched your subscription; that is the safe direction.)
- **Deleted entities.** Hard deletes and merges write a tombstone to an internal `deleted_entities` table for all five entity types (`person`, `organization`, `jurisdiction`, `role`, `role_assignment` — the latter two since #277), pruned on the same 90-day TTL as the outbox (issue #204). After the TTL, `GET /api/v1/people/{id}` or `/orgs/{id}` returning 404 is the fallback signal that an entity was removed.
- **Order.** Results are ordered by outbox `seq_id ASC`. Monotonic within a page and across pages, but see **Delivery semantics** for the concurrent-writer caveat that makes a purely incremental cursor *not* strictly complete.
- **No total count.** `meta.count` is the page count, not a dataset total.

### Delivery semantics

The feed is a Postgres `BIGSERIAL`-backed outbox written by row-level triggers, not a broker with broker guarantees. What is and is not promised:

- **At-least-once, per row-level change — not exactly-one-per-mutation.** Every committed `INSERT`/`UPDATE` of a tracked entity row emits one outbox row; there is **no coalescing** (N updates → N rows), so the latest state is always reachable, but a single logical write can emit several rows for one entity (a direct field update plus touch-cascade bumps from its child tables — names, identifiers, links, contacts, citations; #327). Consumers must be idempotent: treat each event as "re-fetch this entity," keyed on `(entity_type, entity_id)`, not as a delta to apply once.
- **Concurrent-writer skip — the one real completeness hazard.** A `seq_id` is assigned when the writer's transaction inserts the outbox row, but only becomes *visible* on commit. If transaction A acquires a lower `seq_id` than concurrently-running B, and B commits first, a consumer can read B, advance `next_after` past A's id, and then A commits — A's row now exists but is *below* the cursor and a purely incremental poller will never see it. Under the production multi-worker deployment this window is real (bounded by the longest in-flight write, e.g. a bulk import transaction). **Mitigation:** periodically rewind — re-poll from `high_water − margin` — where `margin` comfortably exceeds your longest expected write/import transaction. Rewind is idempotent (see above) and is the intended way to close this gap; the exclusive-cursor incremental poll alone does not.
- **No-emit cases the feed does not cover** (a reconciliation backstop, however infrequent, must still account for these):
  - Trigger-suppressed bulk paths: `TRUNCATE`, `session_replication_role = 'replica'`, `ALTER TABLE ... DISABLE TRIGGER`, or `COPY` into a trigger-disabled table produce no rows. (Ordinary bulk `UPDATE`/backfills fire per row and *over*-emit, not under.)
  - Hard deletes emit only via the `deleted_entities` tombstone (the entity trigger is `INSERT OR UPDATE`, not `DELETE`); any delete path that bypasses the tombstone emits nothing.
  - Trigger-less ingestion telemetry (`field_confidence`, `import_provenance`) does not touch the parent row and so emits nothing — by design; not user-facing state.

### Rewind-and-replay reconciliation (recommended over full-cohort re-fetch)

Because the feed is at-least-once and idempotent, a consumer does **not** need to periodically re-fetch its entire produced cohort by id purely as a safety net. Instead:

1. Poll incrementally from `next_after` for the steady-state deltas.
2. On a periodic cadence, **rewind**: re-poll from `seq = high_water − margin`. This re-covers the concurrent-writer skip window and any near-horizon churn at O(events-in-window) instead of O(cohort).
3. Choose `margin` so it exceeds your longest expected in-flight write/import transaction, and keep your rewind cadence comfortably under `90 days − margin` so you never rewind into pruned range.
4. Keep a **low-frequency** full reconcile (or a 404-driven check against read endpoints) only for the residual the outbox cannot signal — the no-emit cases above. Right-size it against that boundary; the outbox lets you shrink it, not necessarily remove it.

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

**`reason` is a diagnostic aid, not a stable API contract.** Its format (e.g. `"unknown_identifier_type: 'org_wa_legislature_chamber'"`) may change across releases. Do not pattern-match on specific reason strings in production code; use it for logging and debugging only.

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
