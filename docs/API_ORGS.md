# power-map Public API — Organizations

Per-resource endpoint behaviour for **Organizations**: filters, response shapes, and the
implicit rules this collection follows. Auth, pagination and conditional requests are in
`docs/PUBLIC_API.md`, the change feed in `docs/CHANGE_FEED.md`; write semantics in
`docs/OBSERVATIONS.md`; the other resources are indexed in `docs/API_ENTITIES.md`.

---

## Organizations


### Endpoints

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `GET` | `/api/v1/orgs/search` | API key | Search by display name or identifier. Params: `q`, `identifier_type` + `identifier_value` (takes precedence over `q`), `jurisdiction` (slug or ULID — filters to orgs with a `governing` affiliation for that jurisdiction), `include_archived`, `limit`, `offset`. |
| `GET` | `/api/v1/orgs/{id}` | API key | Detail by ULID. Returns names, acronyms, identifiers, jurisdiction affiliations, `active`, `created_at`, and `updated_at`. ETag caching — see `docs/PUBLIC_API.md` § Caching — detail endpoints. |
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
| `additional_identifiers` | optional | List of `{identifier_type_slug, identifier_value}` — for attaching secondary identifier schemes. **Internal (`pm_*`) types are refused here** (`rejected`, `Internal identifier type … cannot be assigned via observations`) — they address the entity via `identifier_type`, they are not attachable. **One value per type per entity:** re-sending the same value is a no-op, a *different* value for a type the entity already carries rejects the whole observation (`reason: identifier_conflict: '<slug>'`). |
| `jurisdiction_affiliations` | optional | List of `{jurisdiction_id, affiliation_type_slug}` — typed org-to-jurisdiction associations. `affiliation_type_slug` must match a value in `organization_jurisdiction_affiliation_types` (seeded values: `governing`, `registered`). Idempotent (duplicate rows silently ignored). Invalid `jurisdiction_id` or unknown `affiliation_type_slug` → `rejected`. |
| `events` | optional | List of event claims — same shape as for `POST /people/observations`. See `docs/API_EVENTS.md` § Observation `events` surface. |
| `active` | optional | Boolean. Sets the orgs-only `active` axis (operationally live vs. dissolved/defunct). **Omitted or `null` ⇒ the flag is left unchanged**; an explicit bool asserts it. Setting `active` on an **archived** org is rejected (`reason: active_on_archived_org`) — archiving is an admin lifecycle gate, so an archived row is not a valid observation target. A redundant assertion (value already matches) is a true no-op and emits no change-feed event. |

**Disposition semantics:**

| Disposition | Condition |
|-------------|-----------|
| `new` | Identifier not seen before; organization created |
| `auto-attached` | Identifier already known; existing entity returned |
| `rejected` | Unknown identifier type; identifier belongs to a non-organization entity; an internal type in `additional_identifiers`; a conflicting `additional_identifiers` value (`identifier_conflict: '<slug>'`); ambiguous parent lookup (0 or 2+ matches); `active` asserted on an archived org (`reason: active_on_archived_org`); `active` asserted on an org hard-deleted concurrently with the request (`reason: org_not_found`); DB constraint violation. A human-readable `reason` string is always present on rejected responses. |

**When to include `display_label`:** Add a label when the contact method serves a specific named function — e.g. `"Main Office"`, `"Committee Hotline"`, `"Press Inquiries"`. Omit it when the value alone is self-explanatory.
