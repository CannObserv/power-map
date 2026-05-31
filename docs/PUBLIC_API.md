# power-map Public API

**Schema and endpoint inventory:** `/docs` (Swagger UI, both dev and prod) is the authoritative reference for request parameters, response shapes, and per-endpoint descriptions. This document covers the meta-level contracts, auth model, and implicit behaviors that the OpenAPI spec does not capture.

---

## Authentication

Every request requires `X-API-Key: <token>`. Missing header → 403; invalid key → 401.

Keys are stored as SHA-256 hashes — the raw token is never persisted after issuance. Each valid request updates `last_used_at` on the key row; the maintainer can review per-key usage in the admin dashboard to identify inactive keys.

---

## Scope

Read endpoints are accessible with any valid key. Write endpoints require an additional per-key scope grant (e.g. `observations:write`). A key without the required scope receives a 403. Scope grants are managed by the maintainer via the admin dashboard.

---

## Rate Limits

None are enforced at the application layer. Implement client-side throttling to avoid saturating the DB connection pool under sustained load.

---

## Key Lifecycle

No self-serve key management. To request, rotate, or revoke a key, open an issue or contact the maintainer. Include the `key_prefix` (first 8 characters of your raw token) so the correct row can be identified without the raw secret.

---

## Pagination — implicit behaviors

The `/docs` spec documents the `q`, `limit`, `offset`, `include_archived`, `identifier_type`, and `identifier_value` parameters. The following behavioral details are not captured there:

- **`count` is the page count, not the total.** `meta.count` is the number of items returned in this response. No total-dataset-size field exists.
- **`limit` is server-clamped to 50.** Values above 50 are silently reduced; the cap is enforced in code, not in schema validation, so the OpenAPI spec shows no upper bound.
- **Empty `q` short-circuits.** When `q` is absent or whitespace-only, the endpoint returns an empty result set immediately — no DB query is issued. A non-empty `q` is required for meaningful results.
- **`identifier_type` + `identifier_value` take precedence over `q`.** When both are supplied, they perform an exact identifier lookup and return at most one result with `has_more: false`; `q`, `limit`, and `offset` are accepted but have no effect.
- **`include_archived: false` is a silent filter.** Archived entities are excluded by default with no signal in the response that a matching archived record exists. Pass `include_archived=true` to include them.

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

`GET /api/v1/orgs/{id}` and `GET /api/v1/people/{id}` return caching headers on every `200` response:

| Header | Value |
|--------|-------|
| `ETag` | `"<id>-<updated_at_ms>"` — strong ETag based on last-update timestamp |
| `Last-Modified` | RFC 7231 format |
| `Cache-Control` | `no-cache` — revalidation required before serving from cache |
| `Vary` | `X-API-Key` |

Send `If-None-Match: <etag>` to receive `304 Not Modified` when the record is unchanged. `Vary: X-API-Key` means shared proxy caches store a separate entry per key — if multiple services share one key they share a cache entry.

---

## Versioning

Path-versioned (`/api/v1/`). Breaking changes introduce a new prefix (`/api/v2/`). Additive changes (new optional fields, new endpoints) may appear within a version without notice.

---

## Change Feed

`GET /api/v1/changes` returns a time-ordered feed of entity mutations for sibling-service cache invalidation.

### Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `since` | ISO 8601 timestamp | required | Return events at or after this timestamp |
| `limit` | integer 1–1000 | 50 | Max items per page |

### Response shape

```json
{
  "data": [
    {
      "entity_type": "person",
      "entity_id": "01JVBN...",
      "changed_at": "2025-06-01T12:00:00.000000Z",
      "change_kind": "updated",
      "archived_at": null
    }
  ],
  "meta": {
    "limit": 50,
    "count": 1,
    "has_more": false,
    "next_since": "2025-06-01T12:00:00.000000Z"
  }
}
```

`change_kind` is `"updated"` for live or archived entities and `"deleted"` for hard-deleted or merged entities.

### Polling pattern

Pass `meta.next_since` from the previous response as `since` on the next poll:

```python
since = "2025-01-01T00:00:00.000000Z"
while True:
    resp = client.get("/api/v1/changes", params={"since": since})
    page = resp.json()
    process(page["data"])
    since = page["meta"]["next_since"]
    if not page["meta"]["has_more"]:
        break
```

### Implicit behaviors

- **Inclusive boundary.** `since` uses `>=` semantics — the timestamp returned as `next_since` may appear again in the next page. Deduplicate by `entity_id` when processing consecutive pages.
- **Deleted entities.** Hard deletes and merges write a tombstone to an internal `deleted_entities` table (TTL ≈ 90 days). After the TTL, `GET /api/v1/people/{id}` or `/orgs/{id}` returning 404 is the fallback signal that an entity was removed.
- **Order.** Results are ordered by `changed_at ASC, entity_id ASC`. Within a single timestamp, order is deterministic but arbitrary.
- **No total count.** `meta.count` is the page count, not a dataset total.

---

## Health check

`GET /api/v1/` returns `{"status": "ok", "version": "v1"}` when authenticated. Use it to confirm key validity before hitting data endpoints.
