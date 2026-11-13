# power-map — Validity Windows & Parent Touch

Real-world validity windows on names and address links, how each address was
standardized, and the touch triggers that turn every such edit into a parent
`updated_at` bump, hence a new detail ETag. Identity
indexes are in `docs/SCHEMA_INDEXES.md`; conditional GETs in `docs/PUBLIC_API.md`.

---

## Org name effective dates (#239)


`organization_names` carries `effective_start` / `effective_end` (nullable `DATE`, `CHECK (start <= end)` = `chk_org_name_effective_date_order`) — the name's real-world validity timeline. PM is the system of record for "which name was in effect when"; consumers filter the dated name list rather than calling an as-of endpoint.

- **Identity model:** a rename is **one durable Org**, never a fork. An external identifier (e.g. `org_wa_legislature_committee_id`) anchors exactly one Org for its whole life — "one WSL Id = one committee" is a deliberate invariant. Resolves CannObserv/usa-wa#40.
- **Orthogonal axes:** effective dates are independent of `is_canonical` (the display pointer) and `name_type` (the kind of name). NULL `effective_start` = unknown lower bound (−∞); NULL `effective_end` = still in effect (+∞).
- **Ingestion is append-only:** `write_names` stores dates only on a newly inserted row; dates sent for an already-present name are a no-op. Rename transitions (close the old interval, promote the new canonical) are **curated in admin**, never feed-driven.
- **Broadcast:** any name-row INSERT/UPDATE/DELETE fires `trg_touch_org_on_name_change` → bumps `organizations.updated_at`, so the org's detail ETag changes and a conditional re-read picks up the new dates.

---

## Address validity windows (#181)


`entity_addresses` carries `valid_from` / `valid_until` (nullable `DATE`, `CHECK (from <= until)` = `chk_ea_validity`) — the link's real-world validity window. NULL = open-ended on that side. Overlapping windows are legitimate (mailing + physical simultaneously, two offices of the same type) — do **not** add an overlap-exclusion constraint.

- **Identity model:** the window is part of the link's identity. `entity_addresses_entity_addr_uniq` is `UNIQUE NULLS NOT DISTINCT (entity_type, entity_id, address_type, address_id, valid_from, valid_until)` — the same entity at the same address across two windows is history, not a duplicate. Any dedup logic (merge anti-joins, schema-migration dedup DELETEs) must compare windows with `IS NOT DISTINCT FROM`.
- **No `is_current` flag:** a generated column is impossible (`CURRENT_DATE` is not immutable); call sites filter with `valid_until IS NULL OR valid_until >= CURRENT_DATE`, matching the jurisdictions pattern. Admin detail queries sort current-first, then `valid_from DESC NULLS LAST`.
- **Ingestion:** `write_addresses` dedups on the normalized form plus the window, mirroring the unique key (#256). Dateless claims (both bounds NULL) stay window-agnostic and dedup against any existing row; dated claims (`ObservationAddress.valid_from` / `.valid_until`, ISO `YYYY-MM-DD`, `from <= until`) dedup on the exact window via `IS NOT DISTINCT FROM` and record a fresh link for a new window, reusing the existing `addresses` row rather than minting a per-window duplicate. Supply dates only when an upstream source carries them; validity dates are otherwise curated in admin.
- **Historical-window semantics — admin end-dating is authoritative over feeds (#256 decision, from #181 CR finding 4):** a dateless claim keeps matching *any* existing row, including an expired historical row (`valid_until < CURRENT_DATE`). So once an admin end-dates an entity's address, a later dateless re-observation records **nothing** — it does not resurrect a current, open-ended row. Rationale: curation is deliberate and human; silently reopening a closed window on the next ingest run would be whack-a-mole. A dateless re-observation of an expired address therefore leaves no trace (observations aren't logged as per-sighting events) — intentional. A source that genuinely needs to assert a *current* window supplies explicit `valid_from`/`valid_until` (the dated-claim escape hatch, #256 item 1); dated claims dedup with strict `IS NOT DISTINCT FROM` window equality, while dateless claims deliberately ignore the window.
- **Broadcast:** any `entity_addresses` INSERT/UPDATE/DELETE fires `trg_touch_entity_on_address_change` → bumps the parent entity's `updated_at` (all five entity types), so its detail ETag changes and a conditional re-read picks up the new window.

---

## Address standardization provenance (#595)


`addresses.standardization_status` records why a row has, or lacks, a standardized form; `standardization_checked_at` records when the current status was set: a retry that returns the same status leaves it alone (#797), so the sweep's 3-day audit reads time stuck. NULL = unknown (written before #595). The CHECK (`addresses_standardization_status_check`) mirrors `STANDARDIZATION_STATUSES` in `src/core/normalizers/address.py`.

| Value | Meaning | Sweep retries? |
|---|---|---|
| `standardized` | a standardized form is stored | — |
| `declined` | a curator chose "Keep my input" | **never** |
| `unavailable` | the validator was down (outage, timeout, 5xx, 429 given up) | yes (default) |
| `rejected` | 400/422: the validator refused the input | no |
| `unsupported` | 422 `country_not_supported` | opt-in |
| `no_result` | 200 with no standardized form (e.g. `not_found`) | no |
| `local` | never asked: a `local_only` import (#402) or no API key | opt-in |

- **One derivation:** `standardization_status(result)` maps a normalizer result; every writer calls it — the three admin address routers, `write_addresses`, and the CSV pipeline. `declined` is never derived: the admin sets it on a `mode=save` submit without a standardized form (`saved_status()`). The #589 `keep_stored` edit leaves the row, status included, untouched.
- **Backfill:** schema.sql's #595 block fills NULL statuses once from `field_confidence` (matched on `sha256(COALESCE(standardized, raw_input))`). A markerless pre-#589 `usaddress` fallback is ambiguous (outage or rejected input?), so it becomes `unavailable`: the sweep's first retry classifies it. No match → stays NULL.
- **Repair:** `scripts/restandardize_addresses.py` (runbook: `docs/RUNBOOKS.md`) retries a status, writes a standardized form with Accept semantics, and folds the row into a twin the entity already holds (`src/core/address_repair.py`): links move window-exact (#256), exact duplicates drop, and the row is deleted only once no link and no event place references it.
- **Broadcast:** an UPDATE that changes a served column of `addresses` (the lines, `raw_input`, `standardized`, lat/long, `components`, `precision`) fires `trg_touch_parents_on_address_change` → bumps every parent linked through `entity_addresses` and every `entity_events` row using it as a place. Role/assignment details and the event lists serve `standardized`, so their ETags move. The status columns are excluded: a retry that stays `unavailable` moves no ETag.

---

## Jurisdiction graph broadcast (#275)


`jurisdiction_relationships` and `organization_jurisdiction_affiliations` are curated from the admin (Phase 3); both bump the touched entities' `updated_at`, so a conditional re-read sees graph edits (the public API exposes them: `GET /api/v1/jurisdictions/{id}/relationships` and the org read model's `jurisdiction_affiliations`).

- **Relationship edges:** any `jurisdiction_relationships` INSERT/UPDATE/DELETE fires `trg_touch_jurisdiction_on_relationship_change` → `touch_parent_jurisdiction()` bumps **both** endpoints' (`from_id` and `to_id`) `updated_at`.
- **Org affiliations:** any `organization_jurisdiction_affiliations` INSERT/UPDATE/DELETE fires **two** touch triggers — `trg_touch_org_on_affiliation_change` (org) and `trg_touch_jurisdiction_on_affiliation_change` (jurisdiction) — so either side's detail ETag changes.
