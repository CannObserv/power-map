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
| `voice_embeddings:write` | `POST /api/v1/people/{id}/embeddings`, `DELETE /api/v1/people/{id}/embeddings/{eid}`, `DELETE /api/v1/people/{id}/embeddings`, `POST /api/v1/people/{id}/embeddings/{eid}/restore` |
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

### Entity types

| `entity_type` | Source table | Notes |
|---|---|---|
| `person` | `people` | |
| `organization` | `organizations` | |
| `jurisdiction` | `jurisdictions` | |
| `role` | `roles` | |
| `role_assignment` | `role_assignments` | |
| `deleted` | `deleted_entities` | `change_kind` is always `"deleted"`; `archived_at` is always `null` |

### Implicit behaviors

- **Inclusive boundary.** `since` uses `>=` semantics — the timestamp returned as `next_since` may appear again in the next page. Deduplicate by `entity_id` when processing consecutive pages.
- **Deleted entities.** Hard deletes and merges write a tombstone to an internal `deleted_entities` table (TTL ≈ 90 days). After the TTL, `GET /api/v1/people/{id}` or `/orgs/{id}` returning 404 is the fallback signal that an entity was removed.
- **Order.** Results are ordered by `changed_at ASC, entity_id ASC`. Within a single timestamp, order is deterministic but arbitrary.
- **No total count.** `meta.count` is the page count, not a dataset total.

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
| `contact_methods` | optional | List of `{contact_type, value}` — `contact_type` must be `email` or `phone` |
| `addresses` | optional | List of `{raw_input, address_type}` — `address_type` must be `mailing` or `physical` |
| `additional_identifiers` | optional | List of `{identifier_type_slug, identifier_value}` — for attaching secondary identifier schemes |

**Disposition semantics:**

| Disposition | Condition |
|-------------|-----------|
| `new` | Identifier not seen before; jurisdiction created |
| `auto-attached` | Identifier already known; existing entity returned |
| `rejected` | Unknown identifier type; identifier belongs to a non-jurisdiction entity; required NEW fields missing; invalid `jurisdiction_type_slug`; slug collision with a different entity |

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
| `POST` | `/api/v1/people/{id}/embeddings` | `voice_embeddings:write` scope | Write a voice embedding observation for a person. Idempotent on `(source_service, source_job_id, source_segment, person_id)` — duplicate returns 200 with the original row's ID. 404 if person is unknown or archived; 422 on dimension mismatch or unknown/write-disabled model. |
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
| `names` | optional | List of `{name, name_type}` — `name_type` must be a valid name type (e.g. `legal`, `preferred`). Exact-match dedup: re-submitting the same name is a no-op. |
| `personal_pronouns` | optional | Free-text pronouns string (e.g. `they/them`). Written only if the field is currently null; ignored if already set. |
| `role_assignments` | optional | List of `{role_id, start_date?, end_date?}`. Exact-match dedup on `(person_id, role_id, start_date, end_date)`. |
| `links` | optional | List of `{url, link_type_id XOR link_type_slug}` |
| `contact_methods` | optional | List of `{contact_type, value}` — `contact_type` must be `email` or `phone` |
| `addresses` | optional | List of `{raw_input, address_type}` — `address_type` must be `mailing` or `physical` |
| `additional_identifiers` | optional | List of `{identifier_type_slug, identifier_value}` — for attaching secondary identifier schemes |
| `events` | optional | List of `{event_type_id XOR event_type_slug, event_year?, event_month?, event_day?, event_hour?, event_minute?, event_second?, event_place_text?, event_place_address_id?, linked_entity_type?, linked_entity_id?, notes?, visibility?}`. See entity events section below. |

**Disposition semantics:**

| Disposition | Condition |
|-------------|-----------|
| `new` | Identifier not seen before; person created |
| `auto-attached` | Identifier already known; existing entity returned |
| `rejected` | Unknown identifier type; identifier belongs to a non-person entity; DB constraint violation |

---

## Organizations

### Endpoints

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `GET` | `/api/v1/orgs/search` | API key | Search by display name or identifier. Params: `q`, `identifier_type` + `identifier_value` (takes precedence over `q`), `jurisdiction` (slug or ULID — filters to orgs with a `governing` affiliation for that jurisdiction), `include_archived`, `limit`, `offset`. |
| `GET` | `/api/v1/orgs/{id}` | API key | Detail by ULID. Returns names, acronyms, identifiers, jurisdiction affiliations, `created_at`, and `updated_at`. ETag caching — see caching section above. |
| `GET` | `/api/v1/orgs/{id}/events` | API key | Paginated lifecycle events for an organization. Params: `limit` (default 20, max 100), `offset`. Public-visibility and active events only. |
| `POST` | `/api/v1/orgs/observations` | `observations:write` scope | Submit an organization identity observation. |

### Observation write — `POST /orgs/observations`

Upserts an organization by identifier using the same match-or-create semantics as the other observation write endpoints.

**Request fields:**

| Field | Required | Notes |
|-------|----------|-------|
| `identifier_type` | always | Must be a registered organization identifier type slug (e.g. `org_ubi`) |
| `identifier_value` | always | Value for the identifier |
| `names` | optional | List of `{name, name_type}`. Exact-match dedup. |
| `org_acronyms` | optional | List of acronym strings. Written with `is_canonical=false`; exact-match dedup. |
| `organization_parent_id` | optional | ULID of the parent org. Mutually exclusive with `organization_parent_name` and `organization_parent_acronym` — supply at most one. |
| `organization_parent_name` | optional | Canonical name of the parent org. Resolves to a single active org; rejected if zero or multiple matches. |
| `organization_parent_acronym` | optional | Canonical acronym of the parent org. Resolves to a single active org; rejected if zero or multiple matches. |
| `links` | optional | List of `{url, link_type_id XOR link_type_slug}` |
| `contact_methods` | optional | List of `{contact_type, value}` — `contact_type` must be `email` or `phone` |
| `addresses` | optional | List of `{raw_input, address_type}` — `address_type` must be `mailing` or `physical` |
| `additional_identifiers` | optional | List of `{identifier_type_slug, identifier_value}` — for attaching secondary identifier schemes |
| `jurisdiction_affiliations` | optional | List of `{jurisdiction_id, affiliation_type_slug}` — typed org-to-jurisdiction associations. `affiliation_type_slug` must match a value in `organization_jurisdiction_affiliation_types` (seeded values: `governing`, `registered`). Idempotent (duplicate rows silently ignored). Invalid `jurisdiction_id` or unknown `affiliation_type_slug` → `rejected`. |
| `events` | optional | List of event claims — same shape as for `POST /people/observations`. See entity events section below. |

**Disposition semantics:**

| Disposition | Condition |
|-------------|-----------|
| `new` | Identifier not seen before; organization created |
| `auto-attached` | Identifier already known; existing entity returned |
| `rejected` | Unknown identifier type; identifier belongs to a non-organization entity; ambiguous parent lookup (0 or 2+ matches); DB constraint violation |

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
| `contact_methods` | Array of `{id, contact_type, value}` |
| `addresses` | Array of `{id, address_id, address_type, raw_input (nullable), standardized (nullable)}` |

Supports ETag / `If-None-Match` conditional requests; 304 on cache hit.

### Observation write — `POST /roles/observations`

Match-or-create semantics. Roles are keyed by `(organization_id, lower(title))` — no external identifier type is needed.

**Request fields:**

| Field | Required | Notes |
|-------|----------|-------|
| `organization_id` | always | ULID of the owning organization. Must exist and be active; unknown/archived org → `rejected`. |
| `title` | always | Role title. Case-insensitive match against existing roles in the same org. |
| `notes` | optional | Free text. Only written on NEW. |
| `established_on` | optional | ISO 8601 date. Only written on NEW. |
| `abolished_on` | optional | ISO 8601 date. Only written on NEW. Must be >= `established_on` if both supplied. |
| `links` | optional | List of `{url, link_type_id XOR link_type_slug}`. Written on both NEW and AUTO_ATTACHED (append-only). |
| `contact_methods` | optional | List of `{contact_type, value}`. Written on both NEW and AUTO_ATTACHED (append-only). |
| `addresses` | optional | List of `{raw_input, address_type}`. Written on both NEW and AUTO_ATTACHED (append-only). |

**Disposition semantics:**

| Disposition | Condition |
|-------------|-----------|
| `new` | No active role with this `(org_id, lower(title))` found; role created |
| `auto-attached` | Active role already exists; attribute writes still applied |
| `rejected` | Organization unknown or archived; DB constraint violation |

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
| `contact_methods` | Array of `{id, contact_type, value}` |
| `addresses` | Array of `{id, address_id, address_type, raw_input (nullable), standardized (nullable)}` |

Supports ETag / `If-None-Match` conditional requests; 304 on cache hit.

### Observation write — `POST /assignments/observations`

Match-or-create semantics. Assignments are keyed by `(person_id, role_id, start_date)` — `NULL` start_date is treated as a distinct known value (NULLS NOT DISTINCT), meaning "unknown start" is itself a unique slot.

**Request fields:**

| Field | Required | Notes |
|-------|----------|-------|
| `person_id` | always | ULID of the person. Must exist and be active; unknown/archived person → `rejected`. |
| `role_id` | always | ULID of the role. Must exist and be active; unknown/archived role → `rejected`. |
| `start_date` | optional | ISO 8601 date (nullable). `NULL` = unknown start date. Only written on NEW. |
| `end_date` | optional | ISO 8601 date. Must be >= `start_date` when both set. Only written on NEW. |
| `is_current` | optional | `false` by default. Cannot be `true` when `end_date` is also set. Only written on NEW. |
| `notes` | optional | Free text. Only written on NEW. |
| `links` | optional | List of `{url, link_type_id XOR link_type_slug}`. Written on both NEW and AUTO_ATTACHED (append-only). |
| `contact_methods` | optional | List of `{contact_type, value}`. Written on both NEW and AUTO_ATTACHED (append-only). |
| `addresses` | optional | List of `{raw_input, address_type}`. Written on both NEW and AUTO_ATTACHED (append-only). |

**Disposition semantics:**

| Disposition | Condition |
|-------------|-----------|
| `new` | No active assignment with this `(person_id, role_id, start_date)` found; assignment created |
| `auto-attached` | Active assignment already exists; attribute writes still applied |
| `rejected` | Person or role unknown/archived; `is_current` + `end_date` conflict; DB constraint violation |

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
