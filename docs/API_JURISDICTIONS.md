# power-map Public API — Jurisdictions

Per-resource endpoint behaviour for **Jurisdictions**: filters, response shapes, and the
implicit rules this collection follows. Auth, pagination and conditional requests are in
`docs/PUBLIC_API.md`, the change feed in `docs/CHANGE_FEED.md`; write semantics in
`docs/OBSERVATIONS.md`; the other resources are indexed in `docs/API_ENTITIES.md`.

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
- **Bitemporal fields.** `valid_from` / `valid_until` are the validity-axis dates (when the jurisdiction or relationship was legally in effect). `recorded_at` / `superseded_at` are the transaction-axis timestamps (when the record was created/replaced in this system). All four may be null. A relationship additionally carries `updated_at`, which is **neither axis**: it is the row's last-modification clock, maintained by a DB trigger and never null. It advances on any edit — including ones that supersede nothing — so use it for change detection, not for reasoning about when a relationship was in effect or superseded. It is the watermark behind this endpoint's ETag (#392).
- **`include_archived` default.** Archived jurisdictions (`archived_at` non-null) are excluded from the list endpoint by default. Pass `include_archived=true` to include them. Detail and resolve endpoints always return archived jurisdictions regardless of this flag.
