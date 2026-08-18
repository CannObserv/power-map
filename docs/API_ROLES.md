# power-map Public API — Roles

Per-resource endpoint behaviour for **Roles**: filters, response shapes, and the
implicit rules this collection follows. Auth, pagination and conditional requests are in
`docs/PUBLIC_API.md`, the change feed in `docs/CHANGE_FEED.md`; write semantics in
`docs/OBSERVATIONS.md`; the other resources are indexed in `docs/API_ENTITIES.md`.

---

## Roles


### Endpoints

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `GET` | `/api/v1/roles` | API key | Paginated list of roles, optionally filtered by org. |
| `GET` | `/api/v1/roles/{id}` | API key | Full role record (links, contact methods, addresses) with ETag caching. |
| `GET` | `/api/v1/role-types` | API key | Full (unpaginated) catalog of role-type classifiers — the structural-match vocabulary. ETag caching (content hash) — see [Conditional requests](PUBLIC_API.md#conditional-requests). |
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

Item fields: `id`, `slug`, `display_name`, `expects_jurisdiction`, `requires_qualifier`, `forbids_qualifier`.

- `slug` — the stable value sent as `RoleObservationRequest.role_type` and returned as `RoleDetail.role_type_slug`.
- `expects_jurisdiction` — advisory hint that this office is normally attached with a `jurisdiction_id` (structural-tuple match). It is a producer hint, **not** enforced: `resolve_role` will let a jurisdiction-expecting type be used in title mode. Sending an **unknown** `role_type` is already rejected (`role_type_not_found`), so an unrecognized slug can never mint a role — this endpoint is what keeps a producer from sending a *valid-but-wrong* slug.
- `requires_qualifier` — **enforced** (unlike `expects_jurisdiction`): the office is per-position, so a jurisdictional observation that omits `qualifier` is rejected (`qualifier_required`, #273) instead of minting a positionless seat. True for `state_representative` (per-position House seats).
- `forbids_qualifier` — **enforced**, the mirror of the above (#302): the office is *positionless*, so a jurisdictional observation that **supplies** a `qualifier` is rejected (`qualifier_forbidden`) instead of minting a second, self-contradictory seat. True for `state_representative_at_large` (the pre-1965 WA House, whose seats were fungible and never designated).

Read the two flags as a pair — they are mutually exclusive, and the combination is what tells a producer how to treat `qualifier`:

| `requires_qualifier` | `forbids_qualifier` | Meaning | Example |
|---|---|---|---|
| `true` | `false` | Qualifier **required** | `state_representative` (Position 1/2) |
| `false` | `true` | Qualifier **rejected** — office is positionless | `state_representative_at_large` |
| `false` | `false` | Qualifier optional; one seat per district | `state_senator` |

An empty or whitespace-only `qualifier` counts as **absent** on both paths — it never satisfies `requires_qualifier`, never trips `forbids_qualifier`, and is stored as NULL.

```jsonc
{
  "data": [
    { "id": "01KX…01", "slug": "state_representative",          "display_name": "State Representative",           "expects_jurisdiction": true,  "requires_qualifier": true,  "forbids_qualifier": false },
    { "id": "01KX…0D", "slug": "state_representative_at_large", "display_name": "State Representative (At-Large)", "expects_jurisdiction": true,  "requires_qualifier": false, "forbids_qualifier": true  },
    { "id": "01KX…02", "slug": "state_senator",                 "display_name": "State Senator",                  "expects_jurisdiction": true,  "requires_qualifier": false, "forbids_qualifier": false },
    { "id": "01KX…0A", "slug": "committee_member",              "display_name": "Committee Member",               "expects_jurisdiction": false, "requires_qualifier": false, "forbids_qualifier": false }
  ]
}
```

The coarse `member` classifier (#269) was **retired** by #266 — it conflated committee with party membership, so "all members" mixed the two. It is split into `committee_member` and `party_member`, both jurisdiction-less classifiers: a role tagged with either still matches by `(organization_id, lower(title))` (send a `title` like `"Member"`), and the type is stored on the role so memberships aggregate without relying on the free-text title.

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
| `qualifier` | with jurisdiction; required for per-position offices, rejected for positionless ones | Position label disambiguating roles in one district (e.g. `"Position 1"`). Requires `jurisdiction_id` (422 otherwise). NULL/omitted for single-position offices; **required** when the `role_type` has `requires_qualifier` (e.g. `state_representative`) — omitting it → `qualifier_required` reject (#273); **rejected** when the `role_type` has `forbids_qualifier` (e.g. `state_representative_at_large`) — supplying it → `qualifier_forbidden` reject (#302). Blank/whitespace counts as omitted on both paths. |
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
| `rejected` | Organization unknown or archived; unknown/archived ULID (PM-native); unknown `role_type` slug; a role with a jurisdiction missing `role_type`; a jurisdictional observation of a `requires_qualifier` office missing `qualifier` (`qualifier_required` — #273); a jurisdictional observation of a `forbids_qualifier` office supplying one (`qualifier_forbidden` — #302); unknown or archived `jurisdiction_id`; a titleless role with a jurisdiction whose title can't be synthesized (`role_title_unavailable` — unknown role_type / non-`usa-wa-ld` district); DB constraint violation. A human-readable `reason` string is always present on rejected responses. |

**Note:** `notes`, `established_on`, and `abolished_on` are only written on NEW disposition. They are intentionally not updated on AUTO_ATTACHED to preserve first-submitter authority over these core role fields.
