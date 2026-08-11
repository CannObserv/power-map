# power-map — Validity Windows & Change-Feed Broadcast

Real-world validity windows on names and address links, and the touch triggers that
carry every such edit onto the change feed. Identity indexes are in
`docs/SCHEMA_INDEXES.md`; the feed itself in `docs/PUBLIC_API.md`.

---

## Org name effective dates (#239)


`organization_names` carries `effective_start` / `effective_end` (nullable `DATE`, `CHECK (start <= end)` = `chk_org_name_effective_date_order`) — the name's real-world validity timeline. PM is the system of record for "which name was in effect when"; consumers filter the dated name list rather than calling an as-of endpoint.

- **Identity model:** a rename is **one durable Org**, never a fork. An external identifier (e.g. `org_wa_legislature_committee_id`) anchors exactly one Org for its whole life — "one WSL Id = one committee" is a deliberate invariant. Resolves CannObserv/usa-wa#40.
- **Orthogonal axes:** effective dates are independent of `is_canonical` (the display pointer) and `name_type` (the kind of name). NULL `effective_start` = unknown lower bound (−∞); NULL `effective_end` = still in effect (+∞).
- **Ingestion is append-only:** `write_names` stores dates only on a newly inserted row; dates sent for an already-present name are a no-op. Rename transitions (close the old interval, promote the new canonical) are **curated in admin**, never feed-driven.
- **Broadcast:** any name-row INSERT/UPDATE/DELETE fires `trg_touch_org_on_name_change` → bumps `organizations.updated_at` → emits an `entity_changes` `'updated'` row, so change-feed subscribers re-fetch and pick up the new dates.

---

## Address validity windows (#181)


`entity_addresses` carries `valid_from` / `valid_until` (nullable `DATE`, `CHECK (from <= until)` = `chk_ea_validity`) — the link's real-world validity window. NULL = open-ended on that side. Overlapping windows are legitimate (mailing + physical simultaneously, two offices of the same type) — do **not** add an overlap-exclusion constraint.

- **Identity model:** the window is part of the link's identity. `entity_addresses_entity_addr_uniq` is `UNIQUE NULLS NOT DISTINCT (entity_type, entity_id, address_type, address_id, valid_from, valid_until)` — the same entity at the same address across two windows is history, not a duplicate. Any dedup logic (merge anti-joins, schema-migration dedup DELETEs) must compare windows with `IS NOT DISTINCT FROM`.
- **No `is_current` flag:** a generated column is impossible (`CURRENT_DATE` is not immutable); call sites filter with `valid_until IS NULL OR valid_until >= CURRENT_DATE`, matching the jurisdictions pattern. Admin detail queries sort current-first, then `valid_from DESC NULLS LAST`.
- **Ingestion:** `write_addresses` dedups on the normalized form plus the window, mirroring the unique key (#256). Dateless claims (both bounds NULL) stay window-agnostic and dedup against any existing row; dated claims (`ObservationAddress.valid_from` / `.valid_until`, ISO `YYYY-MM-DD`, `from <= until`) dedup on the exact window via `IS NOT DISTINCT FROM` and record a fresh link for a new window, reusing the existing `addresses` row rather than minting a per-window duplicate. Supply dates only when an upstream source carries them; validity dates are otherwise curated in admin.
- **Historical-window semantics — admin end-dating is authoritative over feeds (#256 decision, from #181 CR finding 4):** a dateless claim keeps matching *any* existing row, including an expired historical row (`valid_until < CURRENT_DATE`). So once an admin end-dates an entity's address, a later dateless re-observation records **nothing** — it does not resurrect a current, open-ended row. Rationale: curation is deliberate and human; silently reopening a closed window on the next ingest run would be whack-a-mole. A dateless re-observation of an expired address therefore leaves no trace (observations aren't logged as per-sighting events) — intentional. A source that genuinely needs to assert a *current* window supplies explicit `valid_from`/`valid_until` (the dated-claim escape hatch, #256 item 1); dated claims dedup with strict `IS NOT DISTINCT FROM` window equality, while dateless claims deliberately ignore the window.
- **Broadcast:** any `entity_addresses` INSERT/UPDATE/DELETE fires `trg_touch_entity_on_address_change` → bumps the parent entity's `updated_at` → emits an `entity_changes` `'updated'` row (all five entity types), so change-feed subscribers re-fetch and pick up the new window.

---

## Jurisdiction graph broadcast (#275)


`jurisdiction_relationships` and `organization_jurisdiction_affiliations` are curated from the admin (Phase 3); both propagate to the change feed so a jurisdiction subscriber sees graph edits (the public API exposes them: `GET /api/v1/jurisdictions/{id}/relationships` and the org read model's `jurisdiction_affiliations`).

- **Relationship edges:** any `jurisdiction_relationships` INSERT/UPDATE/DELETE fires `trg_touch_jurisdiction_on_relationship_change` → `touch_parent_jurisdiction()` bumps **both** endpoints' (`from_id` and `to_id`) `updated_at` → emits an `entity_changes` `'updated'` row per endpoint.
- **Org affiliations:** any `organization_jurisdiction_affiliations` INSERT/UPDATE/DELETE fires **two** touch triggers — `trg_touch_org_on_affiliation_change` (org) and `trg_touch_jurisdiction_on_affiliation_change` (jurisdiction) — so a subscriber on either side re-fetches.
