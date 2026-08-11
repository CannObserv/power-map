# power-map Public API — Change Feed & Subscriptions

The consumer surface for staying in sync: the polled change feed and the webhook
subscriptions that ride on it. Auth, scopes, rate limits, pagination and conditional
requests are in `docs/PUBLIC_API.md`; per-resource endpoints in
`docs/API_ENTITIES.md`; write semantics in `docs/OBSERVATIONS.md`.

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
