# power-map Public API

**Schema and endpoint inventory:** `/docs` (Swagger UI, both dev and prod) is the authoritative reference for request parameters, response shapes, and per-endpoint descriptions. This document covers the meta-level contracts, auth model, and implicit behaviors that the OpenAPI spec does not capture.

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
- **`include_archived: false` is a silent filter.** Archived entities are excluded by default with no signal in the response that a matching archived record exists. Pass `include_archived=true` to include them. This is the deliberate exception to the #306 rule that status filters must not silently hide search matches: for a machine caller the filter is an explicit, documented opt-in, unlike the admin lists' implicit default status tab (see `docs/STYLE.md` §32 "List status filters & search discoverability").
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

## Jurisdictions

### Endpoints

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `GET` | `/api/v1/jurisdictions` | API key | Paginated list. Params: `type` (slug filter), `include_archived` (bool, default `false`), `limit` (max 100), `offset`. |
| `GET` | `/api/v1/jurisdictions/resolve` | API key | Lookup by slug or external identifier. Params: `slug` xor (`scheme` + `value`). Returns a single record or 404. |
| `GET` | `/api/v1/jurisdictions/{id}` | API key | Detail by ULID or slug. ETag caching — see caching section above. |
| `GET` | `/api/v1/jurisdictions/{id}/relationships` | API key | Edges involving this jurisdiction. Params: `direction` (`from`/`to`/`both`, default `both`), `category` (`spatial`/`governance`/`functional`/`lineage`), `rel_type` (slug filter), `limit`, `offset`. ETag caching (watermark) — see [Conditional requests](#conditional-requests). |
| `GET` | `/api/v1/jurisdictions/{id}/lineage` | API key | Walk `lineage`-category edges recursively. Returns ordered list of jurisdictions (depth-first). Params: `depth` (default 10, max 50). ETag caching (content hash) — see [Conditional requests](#conditional-requests). |
| `POST` | `/api/v1/jurisdictions/observations` | `observations:write` scope | Submit a jurisdiction identity observation. |

### Observation write — `POST /jurisdictions/observations`

Upserts a jurisdiction by identifier using the same match-or-create semantics as the other observation write endpoints.

**Request fields:**

| Field | Required | Notes |
|-------|----------|-------|
| `identifier_type` | always | Must be a registered jurisdiction identifier type slug (`jur_ocd`, `jur_fips`, `jur_iso3166_2`, `jur_slug`) |
| `identifier_value` | always | Value for the identifier |
| `jurisdiction_slug` | NEW only | Unique slug (e.g. `usa-wa`, `usa-wa-ld-21`). Required when creating a new jurisdiction; ignored on AUTO_ATTACHED. |
| `jurisdiction_name` | NEW only | Human-readable name. Required for NEW; ignored on AUTO_ATTACHED. |
| `jurisdiction_type_slug` | NEW only | Must match a seeded `jurisdiction_types` slug (e.g. `state`, `county`, `legislative_district_upper`). Required for NEW. |
| `jurisdiction_valid_from` | NEW only | ISO 8601 date — validity-axis start. Ignored on AUTO_ATTACHED — core entity fields are not overwritten after creation. |
| `jurisdiction_valid_until` | NEW only | ISO 8601 date — validity-axis end; must be ≥ `valid_from` if both supplied. Ignored on AUTO_ATTACHED. |
| `jurisdiction_notes` | NEW only | Free-text notes. Ignored on AUTO_ATTACHED. |
| `links` | optional | List of `{url, link_type_id XOR link_type_slug}` |
| `contact_methods` | optional | List of `{contact_type, value, display_label?}` — `contact_type` must be `email` or `phone`; `display_label` is an optional short human-readable label (e.g. `"Main Office"`, `"Committee Hotline"`) |
| `addresses` | optional | List of `{raw_input, address_type, valid_from?, valid_until?}` — `address_type` must be `mailing`, `physical`, or `other` (default `other`). `valid_from`/`valid_until` (`YYYY-MM-DD`, optional; `valid_from` ≤ `valid_until`, 422 otherwise) bound the address validity window; NULL/omitted = open-ended on that side. A **dateless** claim dedups against any existing window (never resurrects an admin-ended address); a **dated** claim dedups on the exact window and records a fresh row for a new one (#256). |
| `additional_identifiers` | optional | List of `{identifier_type_slug, identifier_value}` — for attaching secondary identifier schemes |

**Disposition semantics:**

| Disposition | Condition |
|-------------|-----------|
| `new` | Identifier not seen before; jurisdiction created |
| `auto-attached` | Identifier already known; existing entity returned |
| `rejected` | Unknown identifier type; identifier belongs to a non-jurisdiction entity; required NEW fields missing; invalid `jurisdiction_type_slug`; slug collision with a different entity. A human-readable `reason` string is always present on rejected responses. |

### Implicit behaviors

- **`{id}` accepts ULID or slug.** All `{id}` path parameters on jurisdiction routes resolve by ULID first, then by slug. A URL of `/jurisdictions/usa-wa` is equivalent to `/jurisdictions/01KT...` when the slug matches.
- **`type` is free-text but registry-backed.** The `type.slug` field comes from the `jurisdiction_types` lookup table seeded at install time (~16 values: `country`, `state`, `county`, `city`, `legislative_district_upper`, etc.). Unknown type slugs in the `type` filter return an empty result set, not a 422.
- **Relationship directionality.** Edges are stored once in the DB (from→to). Symmetric relationship types (`is_symmetric: true` on `rel_type`) imply both directions at the application layer — `direction=both` queries both `from_id` and `to_id` regardless of symmetry flag. Pass `direction=from` or `direction=to` to see only one side.
- **Lineage cycle safety.** The lineage endpoint uses a recursive CTE with a visited-array guard. The `depth` cap prevents runaway traversal even on a cyclic graph.
- **Bitemporal fields.** `valid_from` / `valid_until` are the validity-axis dates (when the jurisdiction or relationship was legally in effect). `recorded_at` / `superseded_at` are the transaction-axis timestamps (when the record was created/replaced in this system). All four may be null.
- **`include_archived` default.** Archived jurisdictions (`archived_at` non-null) are excluded from the list endpoint by default. Pass `include_archived=true` to include them. Detail and resolve endpoints always return archived jurisdictions regardless of this flag.

---

## People

### Endpoints

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `GET` | `/api/v1/people/search` | API key | Search by display name or identifier. Params: `q`, `identifier_type` + `identifier_value` (takes precedence over `q`), `include_archived`, `limit`, `offset`. |
| `GET` | `/api/v1/people/{id}` | API key | Detail by ULID. Returns public name variants, identifiers, `voice_embeddings_count`, `created_at`, and `updated_at`. ETag caching — see caching section above. |
| `GET` | `/api/v1/people/{id}/events` | API key | Paginated lifecycle events for a person. Params: `limit` (default 20, max 100), `offset`. Public-visibility and active events only. ETag caching — see the events response-shape section. |
| `POST` | `/api/v1/people/observations` | `observations:write` scope | Submit a person identity observation. |
| `POST` | `/api/v1/people/identify` | `voice_embeddings:read` scope | Identify a person by voice embedding similarity (open-set, global top-k). Returns top-k matches ordered by cosine similarity. Body: `{model_id, embedding, top_k?}`. Unknown model → empty matches; dimension mismatch or invalid embedding (zero vector, non-finite values) → 422. |
| `POST` | `/api/v1/people/verify` | `voice_embeddings:read` scope | Closed-set verification (#299): score the embedding against a declared candidate set instead of the global top-k. Body: `{model_id, embedding, person_ids}` (1–500 ids — legislature-scale rosters fit in one call, #310; duplicates deduped, first occurrence wins). Returns `{results: [{person_id, similarity, embedding_id, n_embeddings}]}` — one result per requested id, always, in request order. `similarity` = best (max cosine) across the person's active embeddings; `embedding_id` = the winning enrollment; a person with no active embeddings under the model (or an unknown/archived id) returns `similarity: null, embedding_id: null, n_embeddings: 0`, so absence is distinguishable from a low score. Exact scoring (no ANN index involved — a candidate can never be dropped by approximate recall). No server-side thresholding. Unlike identify, an unknown/non-queryable model → 422; also 422 on dimension mismatch or invalid embedding. |
| `POST` | `/api/v1/people/verify-batch` | `voice_embeddings:read` scope | Multi-embedding closed-set verification (#310): score N query embeddings against one declared candidate set in a single call — collapses the per-centroid verify loop for archival-scale jobs. Body: `{model_id, embeddings, person_ids}` (1–50 embeddings × 1–500 ids, bounding the exact scoring product at 25k pairs; duplicates deduped, first occurrence wins). Returns `{results: [{embedding_index, results: [...]}]}` — one group per query embedding, in `embeddings` request order, each carrying the full per-candidate result list with `/verify` semantics (request order, null = no enrollment, best enrollment wins, deterministic tiebreak, exact scoring, no thresholding). One SQL round-trip regardless of N. Note the response shape cost: a maximal call (50 × 500) returns 25k result rows ≈ 2–3 MB of JSON — size chunks accordingly. 422 on unknown/non-queryable model, or dimension mismatch / invalid embedding at any index (detail names the failing index). |
| `POST` | `/api/v1/people/embeddings/presence` | `voice_embeddings:read` scope | Bulk enrollment-presence query (#310): which of these person_ids have ≥1 active embedding under `model_id`. Body: `{model_id, person_ids}` (1–1000 ids; duplicates deduped, first occurrence wins). Returns `{results: [{person_id, n_embeddings}]}` in request order; unknown/archived ids or people with no active enrollments return `n_embeddings: 0` (no 404). Intended for pre-filtering verify candidate sets (once per launch + periodic refresh — enrollments can be pushed mid-job). Non-queryable model → 422, mirroring verify. |
| `POST` | `/api/v1/people/{id}/embeddings` | `voice_embeddings:write` scope | Write a voice embedding observation for a person. Idempotent on `(source_service, source_job_id, source_segment, person_id)` — duplicate against an *active* row returns 200 with the original row's ID. 404 if person is unknown or archived; 409 if the conflicting row is archived (restore or change provenance key first); 422 on dimension mismatch, unknown/write-disabled model, or invalid embedding (zero vector, non-finite values — #299; rejected at write time so stored vectors can never produce NaN similarity on reads). |
| `PATCH` | `/api/v1/people/{id}/embeddings/{eid}?model_id=` | `voice_embeddings:write` scope | Update mutable metadata on an active embedding. Patchable fields: `activity_ms`, `audio_sample_rate_hz`, `recorded_at`. The embedding vector, `model_id`, and provenance key fields are identity — not patchable. Returns all patchable fields after update. 404 if not found; 409 if archived (restore first); 422 for unknown model or empty body. |
| `DELETE` | `/api/v1/people/{id}/embeddings/{eid}?model_id=` | `voice_embeddings:write` scope | Soft-delete a single embedding (sets `archived_at`). Idempotent — re-deleting returns 200 with existing timestamp. 404 if not found; 422 for unknown model. |
| `DELETE` | `/api/v1/people/{id}/embeddings?model_id=&source_job_id=` | `voice_embeddings:write` scope | Batch soft-delete all active embeddings for a person matching `source_job_id`. Returns `{archived_count}`. 404 if person unknown or archived; 422 for unknown model. |
| `POST` | `/api/v1/people/{id}/embeddings/{eid}/restore?model_id=` | `voice_embeddings:write` scope | Restore a soft-deleted embedding (clears `archived_at`). 404 if not found; 409 if already active; 422 for unknown model. |
| `GET` | `/api/v1/people/{id}/embeddings?model_id=&include_archived=&source_job_id=&source_segment=&limit=&offset=` | `voice_embeddings:read` scope | Paginated listing of voice embeddings, newest first — ordered by `(created_at, id)` descending, a stable total order so offset pagination is complete even when rows share a `created_at`. `include_archived=true` includes archived rows. Optional `source_job_id` restricts to a single provenance job (index-backed; omit to enumerate the full set); optional `source_segment` (#299) narrows further to one provenance row — with `include_archived=true` this finds the archived row behind a write 409 in a single call. 404 if person unknown or archived; 422 for unknown model. |

### Observation write — `POST /people/observations`

Upserts a person by identifier using the same match-or-create semantics as the other observation write endpoints.

**Request fields:**

| Field | Required | Notes |
|-------|----------|-------|
| `identifier_type` | always | Must be a registered person identifier type slug (e.g. `person_wa_pdc`; `observo_speaker` for Observo operator-labeled voice speakers, value = an opaque Observo ULID) |
| `identifier_value` | always | Value for the identifier |
| `names` | optional | List of `{name, name_type, is_canonical?}` — `name_type` must be a valid name type (e.g. `legal`, `preferred`); `is_canonical` defaults to `false`. Exact-match dedup: re-submitting the same name is a no-op. Canonical is scoped per `(person, name_type)` slot — a person may have one canonical `legal` and a separate canonical `preferred`. Unlike org names, person names do **not** auto-promote — a name is canonical only if explicitly submitted with `is_canonical: true`. The hint is ignored if a canonical already exists for that name's slot (never displaces). At most one entry per request may carry `is_canonical: true` (422 otherwise). |
| `personal_pronouns` | optional | Free-text pronouns string (e.g. `they/them`). Written only if the field is currently null; ignored if already set. |
| `role_assignments` | optional | List of `{role_id, start_date?, end_date?}`. Exact-match dedup on `(person_id, role_id, start_date, end_date)`. |
| `links` | optional | List of `{url, link_type_id XOR link_type_slug}` |
| `contact_methods` | optional | List of `{contact_type, value, display_label?}` — `contact_type` must be `email` or `phone`; `display_label` is an optional short human-readable label (e.g. `"Main Office"`, `"Committee Hotline"`) |
| `addresses` | optional | List of `{raw_input, address_type, valid_from?, valid_until?}` — `address_type` must be `mailing`, `physical`, or `other` (default `other`). `valid_from`/`valid_until` (`YYYY-MM-DD`, optional; `valid_from` ≤ `valid_until`, 422 otherwise) bound the address validity window; NULL/omitted = open-ended on that side. A **dateless** claim dedups against any existing window (never resurrects an admin-ended address); a **dated** claim dedups on the exact window and records a fresh row for a new one (#256). |
| `additional_identifiers` | optional | List of `{identifier_type_slug, identifier_value}` — for attaching secondary identifier schemes |
| `events` | optional | List of `{event_type_id XOR event_type_slug, pm_event_id?, op?, event_year?, event_month?, event_day?, event_hour?, event_minute?, event_second?, event_place_text?, event_place_address_id?, linked_entity_type?, linked_entity_id?, notes?, visibility?}`. `pm_event_id` refines in place; `op="retract"` archives it (#322) — both work on this embedded path too. See entity events section below for `pm_event_id`/`op` semantics. |

**Disposition semantics:**

| Disposition | Condition |
|-------------|-----------|
| `new` | Identifier not seen before; person created |
| `auto-attached` | Identifier already known; existing entity returned |
| `rejected` | Unknown identifier type; identifier belongs to a non-person entity; DB constraint violation. A human-readable `reason` string is always present on rejected responses. |

**When to include `display_label`:** Add a label when the contact method serves a specific named function — e.g. `"Scheduler"`, `"Committee Office"`, `"Main Switchboard"`. Omit it for generic personal numbers where the value alone is self-explanatory.

---

## Organizations

### Endpoints

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `GET` | `/api/v1/orgs/search` | API key | Search by display name or identifier. Params: `q`, `identifier_type` + `identifier_value` (takes precedence over `q`), `jurisdiction` (slug or ULID — filters to orgs with a `governing` affiliation for that jurisdiction), `include_archived`, `limit`, `offset`. |
| `GET` | `/api/v1/orgs/{id}` | API key | Detail by ULID. Returns names, acronyms, identifiers, jurisdiction affiliations, `active`, `created_at`, and `updated_at`. ETag caching — see caching section above. |
| `GET` | `/api/v1/orgs/{id}/events` | API key | Paginated lifecycle events for an organization. Params: `limit` (default 20, max 100), `offset`. Public-visibility and active events only. ETag caching — see the events response-shape section. |
| `POST` | `/api/v1/orgs/observations` | `observations:write` scope | Submit an organization identity observation. |

### Detail — `GET /api/v1/orgs/{id}`

Returns the base search-result fields plus:

| Field | Description |
|-------|-------------|
| `names` | Array of `{id, name, name_type, is_canonical, effective_start, effective_end}`. `effective_start`/`effective_end` are `YYYY-MM-DD` or `null` — the name's real-world validity (null start = unknown lower bound, null end = still in effect). Filter this array by date to resolve which name was in effect at a given time. |
| `acronyms` | Array of `{id, acronym, is_canonical}` |
| `identifiers` | Array of `{id, type_id, type_slug, value}` |
| `jurisdiction_affiliations` | Array of `{jurisdiction_id, affiliation_type: {id, slug, display_name}}`. Empty array when no affiliations exist. |
| `active` | Boolean. Orgs-only domain axis: operationally live (`true`) vs. dissolved/defunct (`false`). **Orthogonal to `archived_at`** — an org can be `active` and archived, or inactive and not archived. Detail-only; not surfaced in search results. |
| `created_at` | ISO 8601 UTC timestamp |
| `updated_at` | ISO 8601 UTC timestamp |

Supports conditional requests. Every 200 response includes `ETag`, `Last-Modified`, `Cache-Control: no-cache`, and `Vary: X-API-Key` headers. Pass the ETag back as `If-None-Match` to receive 304 on cache hit. `updated_at` advances whenever any child table (names, acronyms, identifiers, affiliations) changes.

### Renames and the name timeline

A rename is modeled as **one durable organization**, never a fork. The organization's external identifier (e.g. `org_wa_legislature_committee_id`) stays anchored to the same record for the entity's entire life — **one WSL Id = one committee** is a stable invariant; consumers should treat the identifier as the durable anchor and the name as following it. On rename, the prior name is retained as a `former` name and a new canonical name is added; both carry `effective_start`/`effective_end`, so a consumer resolves "which name was in effect when" by filtering the `names` array by date — no separate as-of endpoint. Name-timeline transitions (closing the old interval, promoting the new canonical) are curated in Power-Map; the observation feed is append-only and never displaces a canonical name. Any name or date change advances the org's `updated_at` and emits an `updated` change-feed event, so subscribers re-fetch and pick up the new dates.

### Observation write — `POST /orgs/observations`

Upserts an organization by identifier using the same match-or-create semantics as the other observation write endpoints.

**Request fields:**

| Field | Required | Notes |
|-------|----------|-------|
| `identifier_type` | always | Must be a registered organization identifier type slug (e.g. `org_ubi`, `org_wa_legislature`, `org_wa_legislature_chamber`, `org_wa_pdc` — PDC lobbyist-firm filer_id, `org_wa_pdc_committee` — PDC campaign-finance committee filer_id) |
| `identifier_value` | always | Value for the identifier |
| `names` | optional | List of `{name, name_type, is_canonical?, effective_start?, effective_end?}` — `name_type` must be `legal`, `dba`, or `former` (default `legal`); `is_canonical` defaults to `false`. Exact-match dedup. Set `is_canonical: true` on at most one entry to designate it as the canonical name (422 otherwise); when no entry carries the hint the first name written for an org with no existing canonical is auto-promoted. The hint is ignored if a canonical already exists (never displaces). `effective_start`/`effective_end` (`YYYY-MM-DD`; `effective_start` must be ≤ `effective_end` if both supplied — 422 otherwise) are stored **only on a newly written name row**; dates sent for an already-present name are a no-op — name-timeline transitions are curated in Power-Map, not driven by the feed. |
| `org_acronyms` | optional | List of `{acronym, is_canonical?}` — `is_canonical` defaults to `false`. Exact-match dedup. Set `is_canonical: true` on at most one entry to designate it as canonical (422 otherwise); when no entry carries the hint the first acronym written for an org with no existing canonical is auto-promoted. The hint is ignored if a canonical already exists (never displaces). |
| `organization_parent_id` | optional | ULID of the parent org. Mutually exclusive with `organization_parent_name` and `organization_parent_acronym` — supply at most one. **Apply semantics depend on `identifier_type` (#334):** when the org is id-addressed by `pm_org_id`, the parent is **authoritative** — it *replaces* the org's current parent (reparent), gated on producer provenance (`organizations.source_key_id`, claimed on first parent write; a parent owned by another key → `rejected: source_key_mismatch`; unknown/archived parent → `parent_not_found`; a self-parent or ancestor loop → `parent_cycle`). When the org is matched by any **other** identifier, the parent is **write-if-null** — set only if the org has no parent yet, never overwriting an existing one (a fill that would close an ancestor loop is still rejected `parent_cycle`). |
| `organization_parent_name` | optional | Canonical name of the parent org. Resolves to a single active org; rejected if zero or multiple matches. Same apply semantics as `organization_parent_id` once resolved. |
| `organization_parent_acronym` | optional | Canonical acronym of the parent org. Resolves to a single active org; rejected if zero or multiple matches. Same apply semantics as `organization_parent_id` once resolved. |
| `links` | optional | List of `{url, link_type_id XOR link_type_slug}` |
| `contact_methods` | optional | List of `{contact_type, value, display_label?}` — `contact_type` must be `email` or `phone`; `display_label` is an optional short human-readable label (e.g. `"Main Office"`, `"Committee Hotline"`) |
| `addresses` | optional | List of `{raw_input, address_type, valid_from?, valid_until?}` — `address_type` must be `mailing`, `physical`, or `other` (default `other`). `valid_from`/`valid_until` (`YYYY-MM-DD`, optional; `valid_from` ≤ `valid_until`, 422 otherwise) bound the address validity window; NULL/omitted = open-ended on that side. A **dateless** claim dedups against any existing window (never resurrects an admin-ended address); a **dated** claim dedups on the exact window and records a fresh row for a new one (#256). |
| `additional_identifiers` | optional | List of `{identifier_type_slug, identifier_value}` — for attaching secondary identifier schemes |
| `jurisdiction_affiliations` | optional | List of `{jurisdiction_id, affiliation_type_slug}` — typed org-to-jurisdiction associations. `affiliation_type_slug` must match a value in `organization_jurisdiction_affiliation_types` (seeded values: `governing`, `registered`). Idempotent (duplicate rows silently ignored). Invalid `jurisdiction_id` or unknown `affiliation_type_slug` → `rejected`. |
| `events` | optional | List of event claims — same shape as for `POST /people/observations`. See entity events section below. |
| `active` | optional | Boolean. Sets the orgs-only `active` axis (operationally live vs. dissolved/defunct). **Omitted or `null` ⇒ the flag is left unchanged**; an explicit bool asserts it. Setting `active` on an **archived** org is rejected (`reason: active_on_archived_org`) — archiving is an admin lifecycle gate, so an archived row is not a valid observation target. A redundant assertion (value already matches) is a true no-op and emits no change-feed event. |

**Disposition semantics:**

| Disposition | Condition |
|-------------|-----------|
| `new` | Identifier not seen before; organization created |
| `auto-attached` | Identifier already known; existing entity returned |
| `rejected` | Unknown identifier type; identifier belongs to a non-organization entity; ambiguous parent lookup (0 or 2+ matches); `active` asserted on an archived org (`reason: active_on_archived_org`); `active` asserted on an org hard-deleted concurrently with the request (`reason: org_not_found`); DB constraint violation. A human-readable `reason` string is always present on rejected responses. |

**When to include `display_label`:** Add a label when the contact method serves a specific named function — e.g. `"Main Office"`, `"Committee Hotline"`, `"Press Inquiries"`. Omit it when the value alone is self-explanatory.

---

## Roles

### Endpoints

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `GET` | `/api/v1/roles` | API key | Paginated list of roles, optionally filtered by org. |
| `GET` | `/api/v1/roles/{id}` | API key | Full role record (links, contact methods, addresses) with ETag caching. |
| `GET` | `/api/v1/role-types` | API key | Full (unpaginated) catalog of role-type classifiers — the structural-match vocabulary. ETag caching (content hash) — see [Conditional requests](#conditional-requests). |
| `POST` | `/api/v1/roles/observations` | `observations:write` scope | Submit a role observation (match-or-create). |

### List — `GET /api/v1/roles`

Ordered by `(organization_id, title, id)` — the `id` tiebreaker gives a stable total order, so offset pagination is complete even when rows share an org and title.

Query parameters:

| Parameter | Default | Notes |
|-----------|---------|-------|
| `organization_id` | (none) | Filter to roles owned by this org ULID. Highly recommended; without it the list spans all orgs. |
| `include_archived` | `false` | Include archived roles (`archived_at` non-null). |
| `limit` | 20 | 1–100 |
| `offset` | 0 | |

Response item fields: `id`, `organization_id`, `title`, `notes`, `established_on`, `abolished_on`, `role_type_id`, `role_type_slug`, `jurisdiction_id`, `qualifier`, `archived_at`, `created_at`, `updated_at`.

**Structural fields (#261):** a role with a jurisdiction carries `role_type_id` + `role_type_slug` (the office, e.g. `state_representative`), `jurisdiction_id` (the district), and `qualifier` (position label, e.g. `"Position 1"`; NULL for single-position offices like a state senator). All four are `null` on a plain role (no jurisdiction). Aggregate by these: all Representatives → filter `role_type_slug`; all roles in a district → filter `jurisdiction_id`.

### Role types — `GET /api/v1/role-types`

The machine-readable catalog of role-type classifiers (#268), so producers of structured roles discover the `role_type` match-key vocabulary instead of hardcoding it. Unpaginated `{"data": [...]}` (small, stable set); no query parameters.

Item fields: `id`, `slug`, `display_name`, `expects_jurisdiction`, `requires_qualifier`.

- `slug` — the stable value sent as `RoleObservationRequest.role_type` and returned as `RoleDetail.role_type_slug`.
- `expects_jurisdiction` — advisory hint that this office is normally attached with a `jurisdiction_id` (structural-tuple match). It is a producer hint, **not** enforced: `resolve_role` will let a jurisdiction-expecting type be used in title mode. Sending an **unknown** `role_type` is already rejected (`role_type_not_found`), so an unrecognized slug can never mint a role — this endpoint is what keeps a producer from sending a *valid-but-wrong* slug.
- `requires_qualifier` — **enforced** (unlike `expects_jurisdiction`): the office is per-position, so a jurisdictional observation that omits `qualifier` is rejected (`qualifier_required`, #273) instead of minting a positionless seat. True for `state_representative` (per-position House seats), false for `state_senator` (one per district — NULL qualifier is valid).

`member` (#269) is the coarse, jurisdiction-less membership classifier (`expects_jurisdiction: false`) — "person is a member of this body/committee" beneath any precise seat. It is a classifier only: a role tagged `member` still matches by `(organization_id, lower(title))` (send a `title` like `"Member"`), and the type is stored on the role so memberships aggregate without relying on the free-text title.

```jsonc
{
  "data": [
    { "id": "01KX…03", "slug": "member",               "display_name": "Member",              "expects_jurisdiction": false, "requires_qualifier": false },
    { "id": "01KX…01", "slug": "state_representative", "display_name": "State Representative", "expects_jurisdiction": true,  "requires_qualifier": true  },
    { "id": "01KX…02", "slug": "state_senator",        "display_name": "State Senator",        "expects_jurisdiction": true,  "requires_qualifier": false }
  ]
}
```

### Detail — `GET /api/v1/roles/{id}`

Returns all list item fields plus:

| Field | Description |
|-------|-------------|
| `links` | Array of `{id, url, link_type_id, link_type_slug, link_type_name, is_active}` |
| `contact_methods` | Array of `{id, contact_type, value, display_label (nullable)}` |
| `addresses` | Array of `{id, address_id, address_type, raw_input (nullable), standardized (nullable), valid_from (nullable date), valid_until (nullable date)}` — `valid_from`/`valid_until` bound the validity window (`YYYY-MM-DD`); NULL = open-ended on that side |

Supports ETag / `If-None-Match` conditional requests; 304 on cache hit.

### Observation write — `POST /roles/observations`

Two mutually exclusive resolution modes:

- **Standard** — match or create. A **plain role** (no jurisdiction) matches by `(organization_id, lower(title))`. A **role with a jurisdiction** (supply `jurisdiction_id`) matches by `(organization_id, role_type, jurisdiction_id, qualifier)`, so distinct roles sharing a title never collapse and a title-only submission never attaches to a role with a jurisdiction. No external identifier type needed. For a role with a jurisdiction, `title` is **optional and PM-curated** — on create PM synthesizes the canonical title from the tuple and **prefers it over any supplied title** (#267), so an observer never drifts PM's form; a supplied title is used only as a fallback when it can't be synthesized.
- **PM-native** — attach to a known role by its PM ULID. Supply `identifier_type="pm_role_id"` + `identifier_value=<role ULID>`. Never creates; returns `rejected` if the ULID is unknown or archived.

**Request fields:**

| Field | Required | Notes |
|-------|----------|-------|
| `identifier_type` | PM-native mode | Must be `"pm_role_id"` when supplied. Mutually exclusive with `organization_id`/`title`. |
| `identifier_value` | PM-native mode | Role ULID. Required when `identifier_type` is present. |
| `organization_id` | standard mode | ULID of the owning organization. Must exist and be active; unknown/archived org → `rejected`. |
| `title` | plain role only | Role title. Case-insensitive match against existing roles without a jurisdiction in the same org. **Required for a plain role**; **optional when `jurisdiction_id` is present** — PM synthesizes the canonical title and prefers it, so a supplied title is ignored except as a fallback when it can't be synthesized. Omit it for a role with a jurisdiction. |
| `role_type` | with jurisdiction | `role_types` slug (e.g. `state_representative`, `state_senator`). Required when `jurisdiction_id` is supplied; unknown slug → `rejected`. |
| `jurisdiction_id` | with jurisdiction | PM jurisdiction ULID. When present, matching/uniqueness switches to structural identity. A superseded/redistricted (historical) district is valid — only a soft-deleted (archived) district is rejected — so roles can be created against the district that was in effect. |
| `qualifier` | with jurisdiction; required for per-position offices | Position label disambiguating roles in one district (e.g. `"Position 1"`). Requires `jurisdiction_id` (422 otherwise). NULL/omitted for single-position offices; **required** when the `role_type` has `requires_qualifier` (e.g. `state_representative`) — omitting it → `qualifier_required` reject (#273). |
| `notes` | optional | Free text. Only written on NEW. |
| `established_on` | optional | ISO 8601 date. Only written on NEW. |
| `abolished_on` | optional | ISO 8601 date. Only written on NEW. Must be >= `established_on` if both supplied. |
| `links` | optional | List of `{url, link_type_id XOR link_type_slug}`. Written on both NEW and AUTO_ATTACHED (append-only). |
| `contact_methods` | optional | List of `{contact_type, value, display_label?}` — `contact_type` must be `email` or `phone`; `display_label` is an optional short human-readable label. Written on both NEW and AUTO_ATTACHED (append-only). |
| `addresses` | optional | List of `{raw_input, address_type, valid_from?, valid_until?}`. Written on both NEW and AUTO_ATTACHED (append-only). `valid_from`/`valid_until` (`YYYY-MM-DD`, optional; `valid_from` ≤ `valid_until`, 422 otherwise) bound the address validity window; NULL/omitted = open-ended on that side. A **dateless** claim dedups against any existing window (never resurrects an admin-ended address); a **dated** claim dedups on the exact window and records a fresh row for a new one (#256). |

**Disposition semantics:**

| Disposition | Condition |
|-------------|-----------|
| `new` | No active matching role found (plain: `(org_id, lower(title))`; with jurisdiction: `(org_id, role_type, jurisdiction_id, qualifier)`); role created (standard mode only) |
| `auto-attached` | Active matching role already exists (standard) or known ULID supplied (PM-native); attribute writes still applied |
| `rejected` | Organization unknown or archived; unknown/archived ULID (PM-native); unknown `role_type` slug; a role with a jurisdiction missing `role_type`; a jurisdictional observation of a `requires_qualifier` office missing `qualifier` (`qualifier_required` — #273); unknown or archived `jurisdiction_id`; a titleless role with a jurisdiction whose title can't be synthesized (`role_title_unavailable` — unknown role_type / non-`usa-wa-ld` district); DB constraint violation. A human-readable `reason` string is always present on rejected responses. |

**Note:** `notes`, `established_on`, and `abolished_on` are only written on NEW disposition. They are intentionally not updated on AUTO_ATTACHED to preserve first-submitter authority over these core role fields.

---

## Assignments

### Endpoints

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `GET` | `/api/v1/assignments` | API key | Paginated list of role assignments, optionally filtered. |
| `GET` | `/api/v1/assignments/{id}` | API key | Full assignment record (links, contact methods, addresses) with ETag caching. |
| `POST` | `/api/v1/assignments/observations` | `observations:write` scope | Submit an assignment observation (match-or-create, id-addressed update, or `op="retract"`). |

### List — `GET /api/v1/assignments`

Ordered by `(person_id, role_id, start_date, id)` — the `id` tiebreaker gives a stable total order, so offset pagination is complete even when rows share those keys (e.g. archived duplicates).

Query parameters:

| Parameter | Default | Notes |
|-----------|---------|-------|
| `person_id` | (none) | Filter to assignments for this person ULID. |
| `role_id` | (none) | Filter to assignments for this role ULID. |
| `include_archived` | `false` | Include archived assignments (`archived_at` non-null). |
| `limit` | 20 | 1–100 |
| `offset` | 0 | |

Response item fields: `id`, `person_id`, `role_id`, `is_current`, `start_date`, `end_date`, `notes`, `archived_at`, `created_at`, `updated_at`.

### Detail — `GET /api/v1/assignments/{id}`

Returns all list item fields plus:

| Field | Description |
|-------|-------------|
| `links` | Array of `{id, url, link_type_id, link_type_slug, link_type_name, is_active}` |
| `contact_methods` | Array of `{id, contact_type, value, display_label (nullable)}` |
| `addresses` | Array of `{id, address_id, address_type, raw_input (nullable), standardized (nullable), valid_from (nullable date), valid_until (nullable date)}` — `valid_from`/`valid_until` bound the validity window (`YYYY-MM-DD`); NULL = open-ended on that side |

Supports ETag / `If-None-Match` conditional requests; 304 on cache hit.

### Observation write — `POST /assignments/observations`

Two mutually exclusive resolution modes:

- **Standard** — match or create by `(person_id, role_id, start_date)`. `NULL` start_date is a distinct known value (NULLS NOT DISTINCT), meaning "unknown start" is itself a unique slot. On auto-attach, one enrichment applies (#311): a dated `end_date` **closes an open tenure in place** (stored end `NULL` → supplied value, `is_current` → `false` — a dated end implies ended), gated on provenance (below). Any other delta — a differing non-`NULL` `end_date`, an `is_current` flip — is never applied; the field names come back in the response's `unapplied` array so the producer can stop retrying and escalate to a PM-native update.
- **PM-native** — address a known assignment by its PM ULID. Supply `identifier_type="pm_assignment_id"` + `identifier_value=<assignment ULID>`. Never creates; in `observe` mode returns `rejected` if the ULID is unknown or archived (in `op="retract"` mode an archived ULID is a deliberate no-op — see Retraction below). Supplied fields **update the tenure in place** (#311, supersedes the #289 `NULL`→dated-only backfill): a `start_date` *moves* the bound (it cannot be cleared); an **explicit** `end_date: null` *clears* the bound (reopen) while an *omitted* `end_date` leaves it alone (JSON null ≠ omitted); `is_current` sets/clears currency. A resulting dated end with `is_current` omitted implies `is_current=false`. Rejections (whole observation rolls back untouched): `source_key_mismatch` (provenance, below), `is_current_end_date_conflict` (`is_current=true` while a stored end would remain — send `end_date: null` to reopen), `start_after_end_date` (merged bounds invert), `start_date_conflict` (new start collides with a sibling tenure sharing `(person, role, start_date)`).

**Retraction — `op="retract"` (#391).** Closing is not retracting: `end_date` + `is_current=false` asserts the tenure *ended*, but a produced **artifact** — a tenure that never happened — needs the assertion that it *never existed*, and simply ceasing to produce the row orphans the anchored PM assignment. `op="retract"` **archives** the assignment (`archived_at`, never hard-delete) → disposition `retracted`; the outbox emits so subscribers mirror `archived_at` and drop the anchor, and the #301 cascade archives dependent `staff_of` edges with the seat.

Always id-addressed — requires `identifier_type="pm_assignment_id"` (a natural-key retract → `rejected` / `invalid`). A supplied `person_id` / `role_id` must match the stored row (else `identity_immutable` — this guards a copy-paste ULID); provenance is the same-or-`NULL` gate (`source_key_mismatch`); the refine payload and all ancillary (`links` / `contact_methods` / `addresses`) are ignored. Re-retracting an already-archived assignment is a **no-op** (`auto-attached`, no clock bump) so a producer can safely re-emit it every cycle.

A retract is **authoritative**: a later standard-mode re-observation of `(person_id, role_id, start_date)` attaches to the archived row (`auto-attached`, that row's id) rather than minting a fresh active one — re-observing never resurrects, and the same holds for the `role_assignments: [...]` array embedded in a people observation. Un-retract is a deliberate admin unarchive; there is no `archived: false` producer verb.

```json
POST /api/v1/assignments/observations
{"identifier_type": "pm_assignment_id", "identifier_value": "<assignment ULID>", "op": "retract"}
```

**Provenance & update authority (#311):** every assignment created via observation records the writing key as `source_key_id`. Field updates (PM-native, and the standard-mode close-enrichment) are allowed only when the row's `source_key_id` is `NULL` (pre-#311 rows — claimed by the updating key on first update) or equal to the caller's key. Another key's rows are never mutated: PM-native → `rejected` `source_key_mismatch`; standard-mode → attach succeeds with the withheld fields in `unapplied`.

**Request fields:**

| Field | Required | Notes |
|-------|----------|-------|
| `identifier_type` | PM-native mode | Must be `"pm_assignment_id"` when supplied. Mutually exclusive with `person_id`/`role_id`. |
| `identifier_value` | PM-native mode | Assignment ULID. Required when `identifier_type` is present. |
| `op` | optional | `observe` (default) or `retract` (#391). `retract` archives the `pm_assignment_id`-addressed tenure → `retracted`; requires PM-native mode (else `invalid`), payload ignored, re-emit is a no-op. |
| `person_id` | standard mode | ULID of the person. Must exist and be active; unknown/archived person → `rejected`. |
| `role_id` | standard mode | ULID of the role. Must exist and be active; unknown/archived role → `rejected`. |
| `start_date` | optional | ISO 8601 date (nullable). `NULL` = unknown start date. Written on NEW; in PM-native mode a non-null value **moves** the bound in place (#311). |
| `end_date` | optional | ISO 8601 date. Must be >= `start_date` when both set. Written on NEW; on standard-mode auto-attach closes an open tenure (#311); in PM-native mode updates in place — explicit `null` clears (reopen), omitted leaves alone (#311). |
| `is_current` | optional | Tri-state (#311): omitted = no claim (NEW inserts `false`). Cannot be `true` when a dated `end_date` is also set. PM-native mode sets/clears currency in place. |
| `notes` | optional | Free text. Only written on NEW; never reported in `unapplied`. |
| `links` | optional | List of `{url, link_type_id XOR link_type_slug}`. Written on both NEW and AUTO_ATTACHED (append-only). |
| `contact_methods` | optional | List of `{contact_type, value, display_label?}` — `contact_type` must be `email` or `phone`; `display_label` is an optional short human-readable label. Written on both NEW and AUTO_ATTACHED (append-only). |
| `addresses` | optional | List of `{raw_input, address_type, valid_from?, valid_until?}`. Written on both NEW and AUTO_ATTACHED (append-only). `valid_from`/`valid_until` (`YYYY-MM-DD`, optional; `valid_from` ≤ `valid_until`, 422 otherwise) bound the address validity window; NULL/omitted = open-ended on that side. A **dateless** claim dedups against any existing window (never resurrects an admin-ended address); a **dated** claim dedups on the exact window and records a fresh row for a new one (#256). |

**Disposition semantics:**

| Disposition | Condition |
|-------------|-----------|
| `new` | No active assignment with this `(person_id, role_id, start_date)` found; assignment created (standard mode only) |
| `auto-attached` | Active assignment already exists (standard) or known ULID supplied (PM-native); attribute writes still applied. Standard mode may carry `unapplied: [...]` (#311) — supplied `end_date`/`is_current` values that were **not** applied (differing non-`NULL` bound, currency flip, or foreign-source row); absent/`null` on clean attaches. Also the two #391 quiet outcomes: re-retracting an already-archived assignment, and a standard-mode re-observation matching an **archived** twin (anti-resurrection — the archived row's id). On that second case **nothing at all is written**: bound deltas are withheld and ancillary (`links` / `contact_methods` / `addresses`) is skipped rather than attached to a retracted row; every withheld name is reported in `unapplied` — **even when the supplied value equals what the archived row stores**, since a retracted tenure *contradicts* the claim rather than satisfying it (this is where the rule diverges from the active-row `unapplied` semantics above, which report only a differing value). |
| `retracted` | `op="retract"` archived the `pm_assignment_id`-addressed assignment (#391). Assignments only — the other observation endpoints never return it. |
| `rejected` | Person or role unknown/archived; unknown/archived ULID (PM-native); PM-native update conflicts (#311): `source_key_mismatch`, `is_current_end_date_conflict`, `start_after_end_date`, `start_date_conflict` (sibling collision); retract conflicts (#391): `invalid` (not id-addressed), `assignment_not_found`, `identity_immutable`, `source_key_mismatch`; DB constraint violation. A human-readable `reason` string is always present on rejected responses. |

**Changes feed:** `role_assignment` entities appear in `GET /api/v1/changes` with `entity_type: "role_assignment"`.

---

## Entity Events

### Endpoints

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `GET` | `/api/v1/people/{id}/events` | API key | Paginated lifecycle events for a person. |
| `GET` | `/api/v1/orgs/{id}/events` | API key | Paginated lifecycle events for an organization. |
| `GET` | `/api/v1/entity-event-types` | API key | Unpaginated list of all event type vocabulary entries. ETag caching (content hash) — see [Conditional requests](#conditional-requests). |

### Response shape — `GET /people/{id}/events` and `GET /orgs/{id}/events`

Both events endpoints support conditional requests (#292): every 200 response carries `ETag`, `Cache-Control: no-cache`, `Vary: X-API-Key`, and (when the entity has at least one visible event) `Last-Modified`. Pass the ETag back as `If-None-Match` to receive `304 Not Modified` when the collection is unchanged — including the empty-collection case. The ETag covers the entity's whole visible-events set plus the `limit`/`offset` pair, so it changes when an event is added, edited, archived, or hidden. The accepted `If-None-Match` forms (tag lists, `W/` weak tags, `*`) are the API-wide set — see [Conditional requests](#conditional-requests).

Standard paginated envelope, newest first — ordered by event date (year, month, day) descending, then `created_at` descending, then an `id` tiebreaker for a stable total order, so offset pagination is complete even when events share a date. Each item:

| Field | Type | Notes |
|-------|------|-------|
| `id` | string | ULID |
| `event_type` | object | `{id, slug, display_name}` — inline event type |
| `date` | object | `{year, month, day, hour, minute, second, at}` — partial date; null fields = unknown precision. `at` is ISO 8601 Z (reserved — currently always null; future use for denormalized full-precision timestamps) |
| `event_place_text` | string\|null | Freeform place name |
| `event_place_address` | object\|null | Structured address linked to the event place: `{id, city, region, standardized, precision}`. Null when no address is linked. |
| `linked_entity_type` | string\|null | `person` or `organization` |
| `linked_entity_id` | string\|null | ULID of related entity |
| `notes` | string\|null | |
| `visibility` | string | Always `public` in list responses (other tiers filtered out) |
| `verified_at` | string\|null | ISO 8601 Z |
| `created_at` | string | ISO 8601 Z |

### Observation `events` surface

When submitting a `POST /people/observations` or `POST /orgs/observations`, an optional `events` list accepts lifecycle event claims:

| Field | Required | Notes |
|-------|----------|-------|
| `event_type_id` | XOR with slug | Power Map event type ULID |
| `event_type_slug` | XOR with id | e.g. `birth`, `founded`, `marriage` — use `GET /entity-event-types` to discover available slugs |
| `event_year` | depends | Required when the event type has `requires_year: true` (e.g. `birth`, `founded`) |
| `event_month` | optional | 1–12; requires `event_year` |
| `event_day` | optional | 1–31; requires `event_month` |
| `event_hour` | optional | 0–23; requires `event_day` |
| `event_minute` | optional | 0–59; requires `event_hour` |
| `event_second` | optional | 0–59; requires `event_minute` |
| `event_place_text` | optional | Freeform place string (e.g. `Berlin, Germany`) |
| `event_place_address_id` | optional | ULID of a linked `addresses` row. Must have city, postal, or street precision. Addresses with NULL precision (pre-geocoding records) are also accepted. Rejected if the ID does not exist or the precision is `country` or `region`. |
| `linked_entity_type` | depends | Required when `requires_linked_entity: true` (e.g. `marriage`, `merged_with`). `person` or `organization` |
| `linked_entity_id` | depends | ULID of linked entity; required alongside `linked_entity_type` |
| `notes` | optional | Free text |
| `visibility` | optional | `public` (default), `legal_only`, or `hidden` |

**Implicit behaviors:**

- **`applies_to` enforcement.** Submitting a person-only event type (e.g. `birth`) via `POST /orgs/observations` returns `disposition: rejected`.
- **Append-only.** Conflicting observations from different sources both land. Two observations claiming `birth` with different years each create a separate event row; resolution is editorial (admin UI).
- **Dedup.** Exact match on `(event_type_id, event_year, event_month, event_day, event_hour, event_minute, event_second, linked_entity_id)` — re-submitting the identical event claim is a no-op.
- **Partial date precision chain.** `event_month` requires `event_year`; `event_day` requires `event_month`; and so on down to `event_second`. Violating this chain results in `disposition: rejected`.
- **Source attribution.** Each event row records the `source_key_id` of the API key that submitted it.

---

## Health check

`GET /api/v1/` returns `{"status": "ok", "version": "v1"}` when authenticated. Use it to confirm key validity before hitting data endpoints.

Unauthenticated probes live at root level, outside `/api/v1` (#343) — not part of the keyed API surface, exempt from rate limits and request logging:

- `GET /health` — liveness: `{"status": "ok", "build": "<version>"}`; no external calls.
- `GET /ready` — readiness: bounded DB pool check; `200 {"status": "ok"}` or `503 {"status": "unavailable", "reason": "no_pool" | "pool_timeout" | "db_error"}`.
