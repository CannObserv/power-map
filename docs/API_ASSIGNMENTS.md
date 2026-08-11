# power-map Public API — Assignments

Per-resource endpoint behaviour for **Assignments**: filters, response shapes, and the
implicit rules this collection follows. Auth, pagination and conditional requests are in
`docs/PUBLIC_API.md`, the change feed in `docs/CHANGE_FEED.md`; write semantics in
`docs/OBSERVATIONS.md`; the other resources are indexed in `docs/API_ENTITIES.md`.

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
