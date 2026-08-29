# power-map Public API — Assignments

Per-resource endpoint behaviour for **Assignments**: filters, response shapes, and the
implicit rules this collection follows. Auth, pagination and conditional requests are in
`docs/PUBLIC_API.md`, the change feed in `docs/CHANGE_FEED.md`; the other resources are
indexed in `docs/API_ENTITIES.md`. Assignment write semantics live here, in
§"Write semantics & provenance"; the cross-cutting observation rules the other
resources share are in `docs/OBSERVATIONS.md`.

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

Always id-addressed — requires `identifier_type="pm_assignment_id"` (a natural-key retract → `rejected` / `invalid`). A supplied `person_id` / `role_id` must match the stored row (else `identity_immutable` — this guards a copy-paste ULID); provenance is the same-or-`NULL` gate (`source_key_mismatch`); the refine payload and all ancillary (`links` / `contact_methods` / `addresses`) are ignored. Re-retracting an already-archived assignment is a **no-op** (`auto-attached` + `attached_archived: true`, no clock bump) so a producer can safely re-emit it every cycle.

A retract is **authoritative**: a later standard-mode re-observation of `(person_id, role_id, start_date)` attaches to the archived row (`auto-attached`, that row's id, `attached_archived: true`) rather than minting a fresh active one — re-observing never resurrects, and the same holds for the `role_assignments: [...]` array embedded in a people observation. Un-retract is a deliberate admin unarchive; there is no `archived: false` producer verb.

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
| `auto-attached` | Active assignment already exists (standard) or known ULID supplied (PM-native); attribute writes still applied. Standard mode may carry `unapplied: [...]` (#311) — supplied `end_date`/`is_current` values that were **not** applied (differing non-`NULL` bound, currency flip, or foreign-source row); absent/`null` on clean attaches. Also the two #391 quiet outcomes, both of which now carry `attached_archived: true` (#477): re-retracting an already-archived assignment, and a standard-mode re-observation matching an **archived** twin (anti-resurrection — the archived row's id). On that second case **nothing at all is written**: bound deltas are withheld and ancillary (`links` / `contact_methods` / `addresses`) is skipped rather than attached to a retracted row; every withheld name is reported in `unapplied` — **even when the supplied value equals what the archived row stores**, since a retracted tenure *contradicts* the claim rather than satisfying it (this is where the rule diverges from the active-row `unapplied` semantics above, which report only a differing value). |
| `retracted` | `op="retract"` archived the `pm_assignment_id`-addressed assignment (#391). Assignments only — the other observation endpoints never return it. |
| `rejected` | Person or role unknown/archived; unknown/archived ULID (PM-native); PM-native update conflicts (#311): `source_key_mismatch`, `is_current_end_date_conflict`, `start_after_end_date`, `start_date_conflict` (sibling collision); retract conflicts (#391): `invalid` (not id-addressed), `assignment_not_found`, `identity_immutable`, `source_key_mismatch`; DB constraint violation. A human-readable `reason` string is always present on rejected responses. |


**Response field — `attached_archived` (#477):** additive, optional, `null`/absent unless the `auto-attached` addressed a **retracted** row (either #391 quiet outcome above). `auto-attached` alone cannot distinguish a healthy attach to a live tenure from an anti-resurrection attach to a retracted one, and `unapplied` never carried that meaning — a producer seeing `true` should stop re-emitting the tenure rather than keep retrying. The cross-path rule (events / citations / relationships carry it on their per-item results too) is in [OBSERVATIONS.md](OBSERVATIONS.md) §"Anti-resurrection is labelled".

**Response field — `provenance_claimed` (#478):** additive, optional, `null`/absent unless **this** observation stamped `source_key_id` onto a row that had none; never `false`. Assignment observations only, and only on the `pm_assignment_id` path. Same contract shape as `attached_archived` above and for the same reason: `auto-attached` is byte-identical whether the row was already yours or has just become yours, and a producer backfilling provenance across thousands of rows cannot afford a read-back each. Set on **both** claim paths — the identical-assertion claim below *and* the ordinary `COALESCE` that a value-changing update already performed.


### Write semantics & provenance (#311)


The `(person, role, start_date)` match key is **identity, not payload**: a producer correcting a start date used to miss the key and mint a duplicate, and an auto-attach used to silently discard `end_date`/`is_current` deltas. #311 splits update authority across the two resolution modes:

- **PM-native (`pm_assignment_id`) = the authoritative update channel.** The producer proves it means exactly this row, so `update_assignment_fields` *replaces* stored values (supersedes the #289 NULL→dated-only backfill): `start_date` moves (never clears), an **explicit** `end_date: null` clears (reopen) while an omitted field leaves the bound alone (the handler passes `"end_date" in req.model_fields_set` — JSON null ≠ omitted), `is_current` is tri-state (`bool | None`, None = omitted). A resulting dated end with `is_current` omitted implies `FALSE`. Merged-state guards reject before touching the row: `is_current_end_date_conflict`, `start_after_end_date`, `start_date_conflict` (sibling unique-index collision), `source_key_mismatch`.
- **Natural-key auto-attach = safe enrichment + honest signaling.** Exactly one mutation is allowed: closing an open tenure (stored end NULL → supplied dated end, `is_current → FALSE` in the same UPDATE so `chk_current_no_end_date` holds). Every other delta is withheld and echoed back in `ObservationResponse.unapplied` (additive field; `None` on clean attaches) so the producer can stop retrying and escalate to a PM-native update. Never add fuzzy "same person+role, overlapping window" re-matching — non-consecutive terms of the same role are legitimate, and guessing which tenure a mismatched key means corrupts the other term.
- **Provenance:** `role_assignments.source_key_id` (the #162 pattern) is stamped on observation-created rows (both `resolve_assignment` NEW and `write_role_assignments`) and claimed via `COALESCE` on first authoritative update of a pre-#311 NULL row. Updates require `source_key_id IS NULL OR = caller` — admin surfaces are not gated (they operate on any row); only the observation API enforces source authority.
- **Claiming by agreement (#478).** An **identical** id-addressed assertion against an **unowned** (`source_key_id IS NULL`) row claims it: a provenance-only `UPDATE … SET source_key_id=$2 WHERE id=$1 AND archived_at IS NULL AND source_key_id IS NULL RETURNING source_key_id`, nothing else about the row moved, answered `auto-attached` + `provenance_claimed: true`. Before this, the idempotence short-circuit ran *before* the `COALESCE`, so provenance could only ever be claimed as a side effect of a value change — a row whose stored span was already exactly right was unclaimable without falsifying a date, and 6,698 of 11,086 active assignments (2,468 on people usa-wa produces) are pre-#311 mintings in exactly that state. The re-checked `source_key_id IS NULL` makes a concurrent claim lose the race rather than overwrite one committed since the SELECT, and **both** claim paths report from the `RETURNING` rather than the stale read — `provenance_claimed: true` means the caller actually owns the row, never merely that it was unowned a statement ago. **An owned row still takes no write from an identical assertion** — same-source or foreign. CR round 1 of #311 ruled a foreign key must not be able to change anything by agreeing, and claiming *is* a change; only the unowned case has nothing to defend. A *differing* assertion against a foreign-owned row still rejects `source_key_mismatch`. The claim UPDATE fires the #327 touch triggers like any other write, so it bumps `updated_at` and emits one `entity_changes` row — deliberate: a subscriber that mirrors provenance sees the stamp. An id-addressed observation supplying **no** bound at all still short-circuits before the row is even fetched, so it claims nothing; a claim needs an assertion to agree with.
- **Duplicate cleanup:** `scripts/audit_assignment_duplicates.py` finds overlapping same-`(person, role)` dated pairs. **Auto-merge requires proof of coverage** (#476) — the orphan's end dated *and* the survivor covering it; creation order then names the covering pair `deepened_start` (survivor created later) or `subsumed`. Anything unproven — open-ended orphan, unknown survivor end, or a survivor ending before its orphan — is `overlapping_review`: reported, never merged. Merge = move side data to the survivor, concatenate notes, **archive** the orphan (never delete) with a note recording the discarded span — the archive UPDATE hits the outbox so subscribed producers drop stale anchors. The audit never widens a span to fit; see `docs/AUDITS.md` § Duplicate-assignment audit.

#### Retraction — `op="retract"` (#391)

Closing is not retracting. `end_date` + `is_current=false` asserts the tenure **ended**; a produced **artifact** (a tenure that never happened — e.g. the usa-wa WSL sponsor archive's spurious "John Wynne → LD39 State Senator, 2001–02") needs the assertion that it **never existed**. The two pre-#391 levers were both wrong: closing leaves the false claim standing, and simply ceasing to produce the row orphans the anchored PM assignment (the exact backlog `scripts/audit_assignment_duplicates.py` mops up). Reaching into `POST /admin/role-assignments/{id}/archive/` is out-of-band — admin-scoped auth, outside the producer/LWW contract.

`op: "observe" | "retract"` on `AssignmentObservationRequest` (`observe` default) closes that loop. Spelled as a **verb**, not an `archived: bool` payload field, for parity with events (#322) / citations (#319) / relationships (#301) — all four carry the same guard set, and a boolean would read as LWW-writable state and inherit the `end_date` omitted-vs-explicit-null ambiguity.

- **Always id-addressed.** `identifier_type=pm_assignment_id` required; a natural-key retract → `rejected` / `invalid` (the model validator defers to the handler here rather than 422-ing, so the reject shape matches the other retract surfaces).
- **Guards, in order:** already-archived → **no-op** (`auto-attached`, no UPDATE, no clock bump) — checked **before** provenance so a foreign re-emit stays quiet; unknown id → `assignment_not_found`; a supplied `person_id`/`role_id` differing from the stored row → `identity_immutable` (guards a copy-paste `pm_assignment_id`); live row with a foreign non-NULL `source_key_id` → `source_key_mismatch` (the slug this surface already speaks — not the events' `provenance_conflict`). The refine payload and all ancillary (`links`/`contact_methods`/`addresses`) are ignored on a retract.
- **Not routed through `resolve_entity`.** Its pm-native lookup filters `archived_at IS NULL`, which would turn a re-emit into `pm_id_not_found` instead of the quiet no-op a stateful producer needs; `retract_assignment` does its own unfiltered lookup (mirrors `_retract_event`).
- **Anti-resurrection — the load-bearing half.** `resolve_assignment` now attaches to an **archived twin** (`auto-attached`, that row's id, `attached_archived: true`) instead of minting a fresh active row. **Both** doors onto the identity are closed: `write_role_assignments` (the `role_assignments: [...]` embedded people-observation path) dedups on the *open* tenure, which an archived row no longer matches, so it carries its own archived-twin skip — otherwise a producer emitting the same tenure embedded would resurrect what it retracted through the assignment endpoint. On that attach **nothing is written at all**: bound deltas are withheld *and* ancillary is skipped (`resolve_assignment` returns `attached_archived=True`; the handler branches on it). Attaching links/contacts/addresses to a retracted row would put evidence on a soft-deleted entity and fire the #327 touch triggers, emitting an `entity_changes` row for something subscribers have already dropped. Everything withheld — bound names *and* ancillary names (`notes` stays exempt, as it is on the active path: create-only, never reported) — comes back in `unapplied`, so a producer that keeps sending a retracted tenure is told rather than silently no-op'd (the #311 honest-signaling rule applies here too). One deliberate divergence from the active path: a supplied value is reported **even when it equals what the archived row stores**. On an active row "equals stored" means the claim is already true in PM; on a retracted row PM asserts the tenure never existed, so the identical claim is contradicted, not satisfied — and the commonest payload (a producer re-emitting a currently-held tenure as `is_current: true`) is exactly the one that matches what the row stored when it was retracted. `uq_role_assignment_person_role_start` is partial on active rows so the DB *permits* the re-create — the app declines. Without this a producer that retracts by id but keeps the tuple in its sync set loops forever (retract → next cycle re-creates → orphan again), and admin suppression of any assignment would be defeated on the next sync. A retract is **authoritative**; un-retract is a deliberate admin unarchive only (`POST /admin/role-assignments/{id}/unarchive/`) — there is deliberately no `archived:false` producer verb, the same conclusion #322 CR round 2 reached for events. **Un-retract is not guaranteed to succeed (#424):** the same partiality that lets the DB permit a re-create means an admin or a producer may have put a live row on `(person, role, start_date)` since the retract, and the restore then hits `uq_role_assignment_person_role_start`. `ra_unarchive` refuses with a warning flash and leaves the row retracted rather than 500ing — see `docs/ADMIN.md § Archive model`.
- **Cascades come free.** The archiving UPDATE fires `trg_entity_changes_role_assignments` → outbox row (subscribers already mirror `archived_at`, usa-wa #41/#42) and `trg_cascade_assignment_relationships` → dependent `staff_of` edges archive with the seat (#301). Ancillary stays attached to the soft-deleted row, so the daily ancillary-orphan audit is unaffected. No schema change — `role_assignments.archived_at` already shipped.
- **New disposition** `retracted` on `Disposition` / `ObservationResponse.disposition`. Emitted only by this surface; the other single-object observation endpoints never return it.

**Changes feed:** `role_assignment` entities appear in `GET /api/v1/changes` with `entity_type: "role_assignment"`.
