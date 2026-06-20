# power-map Public API

**Schema and endpoint inventory:** `/docs` (Swagger UI, both dev and prod) is the authoritative reference for request parameters, response shapes, and per-endpoint descriptions. This document covers the meta-level contracts, auth model, and implicit behaviors that the OpenAPI spec does not capture.

---

## Authentication

Every request requires `X-API-Key: <token>`. Missing header → 403; invalid key → 401.

Keys are stored as SHA-256 hashes — the raw token is never persisted after issuance. Each valid request updates `last_used_at` on the key row; the maintainer can review per-key usage in the admin dashboard to identify inactive keys.

---

## Scope

Read endpoints are accessible with any valid key. Write endpoints require an additional per-key scope grant (e.g. `observations:write`). A key without the required scope receives a 403. Scope grants are managed by the maintainer via the admin dashboard.

| Scope | Used by |
|-------|---------|
| `observations:write` | `POST /*/observations` endpoints |
| `subscriptions:write` | `POST /api/v1/subscriptions`, `DELETE /api/v1/subscriptions`, `DELETE /api/v1/subscriptions/{entity_id}` |
| `voice_embeddings:write` | `POST /api/v1/people/{id}/embeddings`, `PATCH /api/v1/people/{id}/embeddings/{eid}`, `DELETE /api/v1/people/{id}/embeddings/{eid}`, `DELETE /api/v1/people/{id}/embeddings`, `POST /api/v1/people/{id}/embeddings/{eid}/restore` |
| `voice_embeddings:read` | `POST /api/v1/people/identify`, `GET /api/v1/people/{id}/embeddings` — required for all biometric data reads |

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
- **Empty `q` short-circuits (q-only path).** When `q` is absent or whitespace-only and `jurisdiction` is not provided, the endpoint returns an empty result set immediately — no DB query is issued. A non-empty `q` is required for meaningful results on this path.
- **`jurisdiction` returns results with an empty `q`.** When `jurisdiction` is provided, omitting or blanking `q` returns the full jurisdiction-scoped cohort. `q` acts as an additional name filter on top of the jurisdiction scope, not as a precondition for the query to execute.
- **`identifier_type` + `identifier_value` take precedence over `q`.** When both are supplied, they perform an exact identifier lookup and return at most one result with `has_more: false`; `q`, `limit`, and `offset` are accepted but have no effect.
- **`include_archived: false` is a silent filter.** Archived entities are excluded by default with no signal in the response that a matching archived record exists. Pass `include_archived=true` to include them.
- **`q` uses full-text search, not substring matching.** The `q` parameter is tokenized at word boundaries (`plainto_tsquery`); multi-word queries are AND. Consequences: partial-word queries (`"approp"`) will not match `"Appropriations"`; punctuation is stripped so `"Jr."` and `"Jr"` match identically; person name search is accent-insensitive (`"Hernandez"` matches `"Hernández"`). Results are ordered by relevance rank then name.
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
| `Last-Modified` | RFC 7231 format |
| `Cache-Control` | `no-cache` — revalidation required before serving from cache |
| `Vary` | `X-API-Key` |

Send `If-None-Match: <etag>` to receive `304 Not Modified` when the record is unchanged. `Vary: X-API-Key` means shared proxy caches store a separate entry per key — if multiple services share one key they share a cache entry.

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
      "change_kind": "updated"
    }
  ],
  "meta": {
    "limit": 50,
    "count": 1,
    "has_more": false,
    "next_after": 4217
  }
}
```

`change_kind` is `"updated"` for live or archived entities and `"deleted"` for hard-deleted or merged entities.

`seq_id` is a monotonically increasing integer from the outbox log (`BIGSERIAL`). It is stable and gapless per subscription set.

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
| `person` / `organization` / … | `deleted_entities` | `change_kind` is always `"deleted"` |

### Implicit behaviors

- **Exclusive cursor.** `after` uses `>` semantics — `next_after` will never appear again in the next page.
- **Subscription-filtered.** Events for entities not in the subscription set are never returned, regardless of cursor.
- **Backfill note.** The outbox retains all events since trigger installation. To receive historical events for a newly subscribed entity, poll from `after=0` — the subscription filter applies at query time, so all past outbox entries for that entity are returned.
- **Deleted entities.** Hard deletes and merges write a tombstone to an internal `deleted_entities` table (TTL ≈ 90 days). After the TTL, `GET /api/v1/people/{id}` or `/orgs/{id}` returning 404 is the fallback signal that an entity was removed.
- **Order.** Results are ordered by outbox `seq_id ASC` — strictly monotonic, no ties.
- **No total count.** `meta.count` is the page count, not a dataset total.

---

## Observation writes — shared behavior

All `POST /*/observations` endpoints return an `ObservationResponse` with three fields:

| Field | Type | Notes |
|-------|------|-------|
| `disposition` | `string` | `"new"`, `"auto-attached"`, or `"rejected"` |
| `entity_id` | `string \| null` | ULID of the matched or created entity; `null` on `rejected` |
| `entity_type` | `string \| null` | Entity type string; `null` on `rejected` |
| `reason` | `string \| null` | Human-readable rejection cause; `null` on non-rejected responses |

**`reason` is a diagnostic aid, not a stable API contract.** Its format (e.g. `"unknown_identifier_type: 'org_wa_legislature_chamber'"`) may change across releases. Do not pattern-match on specific reason strings in production code; use it for logging and debugging only.

---

## Jurisdictions

### Endpoints

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `GET` | `/api/v1/jurisdictions` | API key | Paginated list. Params: `type` (slug filter), `include_archived` (bool, default `false`), `limit` (max 100), `offset`. |
| `GET` | `/api/v1/jurisdictions/resolve` | API key | Lookup by slug or external identifier. Params: `slug` xor (`scheme` + `value`). Returns a single record or 404. |
| `GET` | `/api/v1/jurisdictions/{id}` | API key | Detail by ULID or slug. ETag caching — see caching section above. |
| `GET` | `/api/v1/jurisdictions/{id}/relationships` | API key | Edges involving this jurisdiction. Params: `direction` (`from`/`to`/`both`, default `both`), `category` (`spatial`/`governance`/`functional`/`lineage`), `rel_type` (slug filter), `limit`, `offset`. |
| `GET` | `/api/v1/jurisdictions/{id}/lineage` | API key | Walk `lineage`-category edges recursively. Returns ordered list of jurisdictions (depth-first). Params: `depth` (default 10, max 50). |
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
| `addresses` | optional | List of `{raw_input, address_type}` — `address_type` must be `mailing` or `physical` |
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
| `GET` | `/api/v1/people/{id}/events` | API key | Paginated lifecycle events for a person. Params: `limit` (default 20, max 100), `offset`. Public-visibility and active events only. |
| `POST` | `/api/v1/people/observations` | `observations:write` scope | Submit a person identity observation. |
| `POST` | `/api/v1/people/identify` | `voice_embeddings:read` scope | Identify a person by voice embedding similarity. Returns top-k matches ordered by cosine similarity. Body: `{model_id, embedding, top_k?}`. Unknown model → empty matches; dimension mismatch → 422. |
| `POST` | `/api/v1/people/{id}/embeddings` | `voice_embeddings:write` scope | Write a voice embedding observation for a person. Idempotent on `(source_service, source_job_id, source_segment, person_id)` — duplicate against an *active* row returns 200 with the original row's ID. 404 if person is unknown or archived; 409 if the conflicting row is archived (restore or change provenance key first); 422 on dimension mismatch or unknown/write-disabled model. |
| `PATCH` | `/api/v1/people/{id}/embeddings/{eid}?model_id=` | `voice_embeddings:write` scope | Update mutable metadata on an active embedding. Patchable fields: `activity_ms`, `audio_sample_rate_hz`, `recorded_at`. The embedding vector, `model_id`, and provenance key fields are identity — not patchable. Returns all patchable fields after update. 404 if not found; 409 if archived (restore first); 422 for unknown model or empty body. |
| `DELETE` | `/api/v1/people/{id}/embeddings/{eid}?model_id=` | `voice_embeddings:write` scope | Soft-delete a single embedding (sets `archived_at`). Idempotent — re-deleting returns 200 with existing timestamp. 404 if not found; 422 for unknown model. |
| `DELETE` | `/api/v1/people/{id}/embeddings?model_id=&source_job_id=` | `voice_embeddings:write` scope | Batch soft-delete all active embeddings for a person matching `source_job_id`. Returns `{archived_count}`. 404 if person unknown or archived; 422 for unknown model. |
| `POST` | `/api/v1/people/{id}/embeddings/{eid}/restore?model_id=` | `voice_embeddings:write` scope | Restore a soft-deleted embedding (clears `archived_at`). 404 if not found; 409 if already active; 422 for unknown model. |
| `GET` | `/api/v1/people/{id}/embeddings?model_id=&include_archived=&limit=&offset=` | `voice_embeddings:read` scope | Paginated listing of voice embeddings. `include_archived=true` includes archived rows. 404 if person unknown or archived; 422 for unknown model. |

### Observation write — `POST /people/observations`

Upserts a person by identifier using the same match-or-create semantics as the other observation write endpoints.

**Request fields:**

| Field | Required | Notes |
|-------|----------|-------|
| `identifier_type` | always | Must be a registered person identifier type slug (e.g. `person_wa_pdc`) |
| `identifier_value` | always | Value for the identifier |
| `names` | optional | List of `{name, name_type, is_canonical?}` — `name_type` must be a valid name type (e.g. `legal`, `preferred`); `is_canonical` defaults to `false`. Exact-match dedup: re-submitting the same name is a no-op. Canonical is scoped per `(person, name_type)` slot — a person may have one canonical `legal` and a separate canonical `preferred`. Unlike org names, person names do **not** auto-promote — a name is canonical only if explicitly submitted with `is_canonical: true`. The hint is ignored if a canonical already exists for that name's slot (never displaces). At most one entry per request may carry `is_canonical: true` (422 otherwise). |
| `personal_pronouns` | optional | Free-text pronouns string (e.g. `they/them`). Written only if the field is currently null; ignored if already set. |
| `role_assignments` | optional | List of `{role_id, start_date?, end_date?}`. Exact-match dedup on `(person_id, role_id, start_date, end_date)`. |
| `links` | optional | List of `{url, link_type_id XOR link_type_slug}` |
| `contact_methods` | optional | List of `{contact_type, value, display_label?}` — `contact_type` must be `email` or `phone`; `display_label` is an optional short human-readable label (e.g. `"Main Office"`, `"Committee Hotline"`) |
| `addresses` | optional | List of `{raw_input, address_type}` — `address_type` must be `mailing` or `physical` |
| `additional_identifiers` | optional | List of `{identifier_type_slug, identifier_value}` — for attaching secondary identifier schemes |
| `events` | optional | List of `{event_type_id XOR event_type_slug, event_year?, event_month?, event_day?, event_hour?, event_minute?, event_second?, event_place_text?, event_place_address_id?, linked_entity_type?, linked_entity_id?, notes?, visibility?}`. See entity events section below. |

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
| `GET` | `/api/v1/orgs/{id}` | API key | Detail by ULID. Returns names, acronyms, identifiers, jurisdiction affiliations, `created_at`, and `updated_at`. ETag caching — see caching section above. |
| `GET` | `/api/v1/orgs/{id}/events` | API key | Paginated lifecycle events for an organization. Params: `limit` (default 20, max 100), `offset`. Public-visibility and active events only. |
| `POST` | `/api/v1/orgs/observations` | `observations:write` scope | Submit an organization identity observation. |

### Detail — `GET /api/v1/orgs/{id}`

Returns the base search-result fields plus:

| Field | Description |
|-------|-------------|
| `names` | Array of `{id, name, name_type, is_canonical}` |
| `acronyms` | Array of `{id, acronym, is_canonical}` |
| `identifiers` | Array of `{id, type_id, type_slug, value}` |
| `jurisdiction_affiliations` | Array of `{jurisdiction_id, affiliation_type: {id, slug, display_name}}`. Empty array when no affiliations exist. |
| `created_at` | ISO 8601 UTC timestamp |
| `updated_at` | ISO 8601 UTC timestamp |

Supports conditional requests. Every 200 response includes `ETag`, `Last-Modified`, `Cache-Control: no-cache`, and `Vary: X-API-Key` headers. Pass the ETag back as `If-None-Match` to receive 304 on cache hit. `updated_at` advances whenever any child table (names, acronyms, identifiers, affiliations) changes.

### Observation write — `POST /orgs/observations`

Upserts an organization by identifier using the same match-or-create semantics as the other observation write endpoints.

**Request fields:**

| Field | Required | Notes |
|-------|----------|-------|
| `identifier_type` | always | Must be a registered organization identifier type slug (e.g. `org_ubi`, `org_wa_legislature`, `org_wa_legislature_chamber`) |
| `identifier_value` | always | Value for the identifier |
| `names` | optional | List of `{name, name_type, is_canonical?}` — `name_type` must be `legal`, `dba`, or `former` (default `legal`); `is_canonical` defaults to `false`. Exact-match dedup. Set `is_canonical: true` on at most one entry to designate it as the canonical name (422 otherwise); when no entry carries the hint the first name written for an org with no existing canonical is auto-promoted. The hint is ignored if a canonical already exists (never displaces). |
| `org_acronyms` | optional | List of `{acronym, is_canonical?}` — `is_canonical` defaults to `false`. Exact-match dedup. Set `is_canonical: true` on at most one entry to designate it as canonical (422 otherwise); when no entry carries the hint the first acronym written for an org with no existing canonical is auto-promoted. The hint is ignored if a canonical already exists (never displaces). |
| `organization_parent_id` | optional | ULID of the parent org. Mutually exclusive with `organization_parent_name` and `organization_parent_acronym` — supply at most one. |
| `organization_parent_name` | optional | Canonical name of the parent org. Resolves to a single active org; rejected if zero or multiple matches. |
| `organization_parent_acronym` | optional | Canonical acronym of the parent org. Resolves to a single active org; rejected if zero or multiple matches. |
| `links` | optional | List of `{url, link_type_id XOR link_type_slug}` |
| `contact_methods` | optional | List of `{contact_type, value, display_label?}` — `contact_type` must be `email` or `phone`; `display_label` is an optional short human-readable label (e.g. `"Main Office"`, `"Committee Hotline"`) |
| `addresses` | optional | List of `{raw_input, address_type}` — `address_type` must be `mailing` or `physical` |
| `additional_identifiers` | optional | List of `{identifier_type_slug, identifier_value}` — for attaching secondary identifier schemes |
| `jurisdiction_affiliations` | optional | List of `{jurisdiction_id, affiliation_type_slug}` — typed org-to-jurisdiction associations. `affiliation_type_slug` must match a value in `organization_jurisdiction_affiliation_types` (seeded values: `governing`, `registered`). Idempotent (duplicate rows silently ignored). Invalid `jurisdiction_id` or unknown `affiliation_type_slug` → `rejected`. |
| `events` | optional | List of event claims — same shape as for `POST /people/observations`. See entity events section below. |

**Disposition semantics:**

| Disposition | Condition |
|-------------|-----------|
| `new` | Identifier not seen before; organization created |
| `auto-attached` | Identifier already known; existing entity returned |
| `rejected` | Unknown identifier type; identifier belongs to a non-organization entity; ambiguous parent lookup (0 or 2+ matches); DB constraint violation. A human-readable `reason` string is always present on rejected responses. |

**When to include `display_label`:** Add a label when the contact method serves a specific named function — e.g. `"Main Office"`, `"Committee Hotline"`, `"Press Inquiries"`. Omit it when the value alone is self-explanatory.

---

## Roles

### Endpoints

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `GET` | `/api/v1/roles` | API key | Paginated list of roles, optionally filtered by org. |
| `GET` | `/api/v1/roles/{id}` | API key | Full role record (links, contact methods, addresses) with ETag caching. |
| `POST` | `/api/v1/roles/observations` | `observations:write` scope | Submit a role observation (match-or-create). |

### List — `GET /api/v1/roles`

Query parameters:

| Parameter | Default | Notes |
|-----------|---------|-------|
| `organization_id` | (none) | Filter to roles owned by this org ULID. Highly recommended; without it the list spans all orgs. |
| `include_archived` | `false` | Include archived roles (`archived_at` non-null). |
| `limit` | 20 | 1–100 |
| `offset` | 0 | |

Response item fields: `id`, `organization_id`, `title`, `notes`, `established_on`, `abolished_on`, `archived_at`, `created_at`, `updated_at`.

### Detail — `GET /api/v1/roles/{id}`

Returns all list item fields plus:

| Field | Description |
|-------|-------------|
| `links` | Array of `{id, url, link_type_id, link_type_slug, link_type_name, is_active}` |
| `contact_methods` | Array of `{id, contact_type, value, display_label (nullable)}` |
| `addresses` | Array of `{id, address_id, address_type, raw_input (nullable), standardized (nullable)}` |

Supports ETag / `If-None-Match` conditional requests; 304 on cache hit.

### Observation write — `POST /roles/observations`

Two mutually exclusive resolution modes:

- **Standard** — match or create by `(organization_id, lower(title))`. No external identifier type needed.
- **PM-native** — attach to a known role by its PM ULID. Supply `identifier_type="pm_role_id"` + `identifier_value=<role ULID>`. Never creates; returns `rejected` if the ULID is unknown or archived.

**Request fields:**

| Field | Required | Notes |
|-------|----------|-------|
| `identifier_type` | PM-native mode | Must be `"pm_role_id"` when supplied. Mutually exclusive with `organization_id`/`title`. |
| `identifier_value` | PM-native mode | Role ULID. Required when `identifier_type` is present. |
| `organization_id` | standard mode | ULID of the owning organization. Must exist and be active; unknown/archived org → `rejected`. |
| `title` | standard mode | Role title. Case-insensitive match against existing roles in the same org. |
| `notes` | optional | Free text. Only written on NEW. |
| `established_on` | optional | ISO 8601 date. Only written on NEW. |
| `abolished_on` | optional | ISO 8601 date. Only written on NEW. Must be >= `established_on` if both supplied. |
| `links` | optional | List of `{url, link_type_id XOR link_type_slug}`. Written on both NEW and AUTO_ATTACHED (append-only). |
| `contact_methods` | optional | List of `{contact_type, value, display_label?}` — `contact_type` must be `email` or `phone`; `display_label` is an optional short human-readable label. Written on both NEW and AUTO_ATTACHED (append-only). |
| `addresses` | optional | List of `{raw_input, address_type}`. Written on both NEW and AUTO_ATTACHED (append-only). |

**Disposition semantics:**

| Disposition | Condition |
|-------------|-----------|
| `new` | No active role with this `(org_id, lower(title))` found; role created (standard mode only) |
| `auto-attached` | Active role already exists (standard) or known ULID supplied (PM-native); attribute writes still applied |
| `rejected` | Organization unknown or archived; unknown/archived ULID (PM-native); DB constraint violation. A human-readable `reason` string is always present on rejected responses. |

**Note:** `notes`, `established_on`, and `abolished_on` are only written on NEW disposition. They are intentionally not updated on AUTO_ATTACHED to preserve first-submitter authority over these core role fields.

---

## Assignments

### Endpoints

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `GET` | `/api/v1/assignments` | API key | Paginated list of role assignments, optionally filtered. |
| `GET` | `/api/v1/assignments/{id}` | API key | Full assignment record (links, contact methods, addresses) with ETag caching. |
| `POST` | `/api/v1/assignments/observations` | `observations:write` scope | Submit an assignment observation (match-or-create). |

### List — `GET /api/v1/assignments`

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
| `addresses` | Array of `{id, address_id, address_type, raw_input (nullable), standardized (nullable)}` |

Supports ETag / `If-None-Match` conditional requests; 304 on cache hit.

### Observation write — `POST /assignments/observations`

Two mutually exclusive resolution modes:

- **Standard** — match or create by `(person_id, role_id, start_date)`. `NULL` start_date is a distinct known value (NULLS NOT DISTINCT), meaning "unknown start" is itself a unique slot.
- **PM-native** — attach to a known assignment by its PM ULID. Supply `identifier_type="pm_assignment_id"` + `identifier_value=<assignment ULID>`. Never creates; returns `rejected` if the ULID is unknown or archived.

**Request fields:**

| Field | Required | Notes |
|-------|----------|-------|
| `identifier_type` | PM-native mode | Must be `"pm_assignment_id"` when supplied. Mutually exclusive with `person_id`/`role_id`. |
| `identifier_value` | PM-native mode | Assignment ULID. Required when `identifier_type` is present. |
| `person_id` | standard mode | ULID of the person. Must exist and be active; unknown/archived person → `rejected`. |
| `role_id` | standard mode | ULID of the role. Must exist and be active; unknown/archived role → `rejected`. |
| `start_date` | optional | ISO 8601 date (nullable). `NULL` = unknown start date. Only written on NEW. |
| `end_date` | optional | ISO 8601 date. Must be >= `start_date` when both set. Only written on NEW. |
| `is_current` | optional | `false` by default. Cannot be `true` when `end_date` is also set. Only written on NEW. |
| `notes` | optional | Free text. Only written on NEW. |
| `links` | optional | List of `{url, link_type_id XOR link_type_slug}`. Written on both NEW and AUTO_ATTACHED (append-only). |
| `contact_methods` | optional | List of `{contact_type, value, display_label?}` — `contact_type` must be `email` or `phone`; `display_label` is an optional short human-readable label. Written on both NEW and AUTO_ATTACHED (append-only). |
| `addresses` | optional | List of `{raw_input, address_type}`. Written on both NEW and AUTO_ATTACHED (append-only). |

**Disposition semantics:**

| Disposition | Condition |
|-------------|-----------|
| `new` | No active assignment with this `(person_id, role_id, start_date)` found; assignment created (standard mode only) |
| `auto-attached` | Active assignment already exists (standard) or known ULID supplied (PM-native); attribute writes still applied |
| `rejected` | Person or role unknown/archived; unknown/archived ULID (PM-native); `is_current` + `end_date` conflict; DB constraint violation. A human-readable `reason` string is always present on rejected responses. |

**Changes feed:** `role_assignment` entities appear in `GET /api/v1/changes` with `entity_type: "role_assignment"`.

---

## Entity Events

### Endpoints

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `GET` | `/api/v1/people/{id}/events` | API key | Paginated lifecycle events for a person. |
| `GET` | `/api/v1/orgs/{id}/events` | API key | Paginated lifecycle events for an organization. |
| `GET` | `/api/v1/entity-event-types` | API key | Unpaginated list of all event type vocabulary entries. |

### Response shape — `GET /people/{id}/events` and `GET /orgs/{id}/events`

Standard paginated envelope. Each item:

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
