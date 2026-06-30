# Per-Key Change Feed Subscriptions

**Date:** 2026-06-15
**Issue:** #191
**Status:** Approved

---

## Goal

Replace the global `/changes` firehose with a per-API-key filtered feed backed by an explicit entity subscription set. Simultaneously fix the timestamp-cursor unreliability by introducing an append-only outbox log with a monotonic sequence cursor.

---

## Approved Approach

Three independently deliverable pieces, built in order:

1. **Outbox log** — `entity_changes` table + triggers; new `after` (BIGSERIAL) cursor on `/changes`
2. **Subscription table** — `api_key_entity_subscriptions`; new subscription management endpoints
3. **Discovery endpoint** — graph traversal helper for populating the subscription set

---

## Key Decisions

### Cursor: outbox log replaces timestamp polling

**Why:** `updated_at >= $1` has three structural bugs — boundary ambiguity (multiple entities share the same `changed_at`), non-deterministic ordering under concurrent load, and a misleading "deduplicate the overlap row" contract that is wrong when more than one entity sits on the page boundary. An append-only table with a `BIGSERIAL` PK eliminates all three.

**How:**
- New table `entity_changes(id BIGSERIAL PK, entity_type, entity_id, change_kind, changed_at)`
- Triggers on all five entity tables write a row on every INSERT/UPDATE
- Hard deletes and merges write `change_kind='deleted'` (replaces `deleted_entities` as tombstone; same 90-day TTL applies to outbox rows)
- `/changes` param: `since` (timestamp) → `after` (integer seq_id, `>` exclusive)
- Response meta: `next_since` → `next_after` (integer)
- Clean break — no compat shim; "always filter" already breaks existing consumers

### Filtering: always filter, empty subscription = empty feed

**Why:** Implicit behavior (filter-if-subscribed, firehose-if-not) makes the contract surprising and hard to audit. Every consumer that wants change events must opt in explicitly. This forces intentionality and keeps the contract uniform across all keys.

**No flag on `api_keys`.** Behavior is uniform: the join to `api_key_entity_subscriptions` always applies.

### Subscription: explicit per-entity opt-in, self-serve

**Why:** The downstream service knows its entity graph better than the PM maintainer does; that graph evolves (new committee added → `usa-wa` should register it without a maintainer intervention). Predicate-based (slug prefix, jurisdiction CTE at every poll) was rejected: it couples filter logic to the hot read path and doesn't generalize beyond jurisdictions.

**New scope:** `subscriptions:write` for mutation endpoints. Discovery is read-only (any valid key).

### Discovery: graph traversal populates the subscription set, not auto-enroll

**Why:** Auto-expanding the subscription set when the graph changes (new org affiliates with WA → auto-enroll) requires write-time fan-out, which is expensive and introduces a new class of correctness bugs. Discovery runs on-demand, returns candidates, and the client decides what to register. "Saved searches" (re-run discovery to find newly added entities) are explicitly deferred to v2.

---

## Data Model

### `entity_changes`

```sql
CREATE TABLE entity_changes (
    id           BIGSERIAL    PRIMARY KEY,
    entity_type  TEXT         NOT NULL
                              CHECK (entity_type IN
                                ('person','organization','jurisdiction',
                                 'role','role_assignment')),
    entity_id    TEXT         NOT NULL,
    change_kind  TEXT         NOT NULL
                              CHECK (change_kind IN ('updated','deleted')),
    changed_at   TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_entity_changes_entity ON entity_changes(entity_id, id);
```

Triggers required on: `people`, `organizations`, `jurisdictions`, `roles`, `role_assignments`.

### `api_key_entity_subscriptions`

```sql
CREATE TABLE api_key_entity_subscriptions (
    api_key_id   TEXT         NOT NULL REFERENCES api_keys(id) ON DELETE CASCADE,
    entity_id    TEXT         NOT NULL,
    entity_type  TEXT         NOT NULL
                              CHECK (entity_type IN
                                ('person','organization','jurisdiction',
                                 'role','role_assignment')),
    created_at   TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    PRIMARY KEY (api_key_id, entity_id)
);

CREATE INDEX idx_sub_entity ON api_key_entity_subscriptions(entity_id, api_key_id);
```

`entity_type` stored as an annotation for type-filtered list queries; not needed for correctness (ULIDs are globally unique). Server resolves `entity_type` on `POST` by looking up the ID across entity tables — client does not supply it.

---

## API Changes

### `GET /api/v1/changes` — updated

| Old | New |
|-----|-----|
| `?since=<ISO 8601 timestamp>` | `?after=<integer seq_id>` |
| `meta.next_since` (string) | `meta.next_after` (integer) |
| Unfiltered | Filtered by `api_key_entity_subscriptions` for calling key |

Core query:

```sql
SELECT ec.id, ec.entity_type, ec.entity_id, ec.change_kind, ec.changed_at
FROM entity_changes ec
JOIN api_key_entity_subscriptions s
    ON s.entity_id = ec.entity_id AND s.api_key_id = $key_id
WHERE ec.id > $after
ORDER BY ec.id ASC
LIMIT $limit + 1
```

### `GET /api/v1/subscriptions` — new

List own subscriptions. Params: `entity_type` (filter), `limit`, `offset`. Any valid key.

### `POST /api/v1/subscriptions` — new (requires `subscriptions:write`)

Bulk register entity IDs. Idempotent — existing rows silently ignored.

**Request:** `{"entity_ids": ["01JXXX...", ...]}`

**Response:** `{"registered": N, "already_subscribed": M, "not_found": [...]}`

`not_found` lists any IDs that don't resolve to a known entity. The rest of the batch is still applied.

### `DELETE /api/v1/subscriptions/{entity_id}` — new (requires `subscriptions:write`)

Remove single subscription. 404 if not subscribed.

### `DELETE /api/v1/subscriptions` — new (requires `subscriptions:write`)

Bulk remove. **Request:** `{"entity_ids": [...]}`

### `GET /api/v1/subscriptions/discover` — new (read-only, any valid key)

Graph traversal — returns candidate entities for subscription. Client selects from results and POSTs to `/subscriptions`.

**Parameters:**

| Param | Required | Notes |
|-------|----------|-------|
| `root_type` | yes | `jurisdiction` or `organization` |
| `root_id` | yes | ULID or slug |
| `follow` | yes | Comma-separated edge types (see below) |
| `limit` | no | Default 100, max 500 |
| `offset` | no | Default 0 |

**`follow` values** (applied in traversal order; each is opt-in):

| Value | Traversal |
|-------|-----------|
| `lineage` | Jurisdiction → descendant jurisdictions via `lineage`-category edges (recursive CTE) |
| `affiliated_orgs` | Any matched jurisdiction → orgs with `governing` affiliation |
| `org_children` | Any matched org → child orgs via `parent_id` (recursive) |
| `roles` | Any matched org → its roles |
| `assignments` | Any matched role → role_assignments |
| `people` | Any matched role_assignment → persons |

Constraint: `affiliated_orgs` and `org_children` require a jurisdiction or org in the matched set respectively; `roles`/`assignments`/`people` require their prerequisite type. Invalid combinations → 422.

**Response item:**
```json
{
  "entity_type": "organization",
  "entity_id": "01JXXX...",
  "display_name": "WA Senate",
  "hops_from_root": 2
}
```

Root entity is included at `hops_from_root: 0`.

**`usa-wa` setup example:**
```
GET /api/v1/subscriptions/discover
  ?root_type=jurisdiction&root_id=usa-wa
  &follow=lineage,affiliated_orgs,org_children,roles,assignments,people
```

---

## Out of Scope (v2)

- **Saved search templates** — storing graph traversal parameters and re-running discovery to surface newly-added entities for opt-in. Deferred.
- **Auto-enroll on graph change** — write-time fan-out when affiliations change. Deferred.
- **Webhook / push delivery** — SSE or HTTP push instead of polling. Deferred.
- **`affiliated_orgs` with `registered` affiliation type** — only `governing` in v1.

---

## Open Questions

- ~~**Outbox TTL policy**~~ — RESOLVED (#204): 90-day window on both `entity_changes` and `deleted_entities`, enforced by `scripts/prune_outbox.py` under a daily `power-map-prune.timer`. DELETE-based (no `changed_at` index, to keep the trigger-heavy insert path lean); revisit range-partitioning only if steady-state volume makes the daily scan expensive.
- ~~**Backfill on new subscription**~~ — RESOLVED (#204): the feed is a recent-changes window, not a full-history store. New subscribers fetch current state from the read endpoint, then poll incrementally; documented in `PUBLIC_API.md` § change feed.
