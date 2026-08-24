# power-map — Observation Write Semantics & Provenance

How the public observation path writes: the identifier-type vocabulary entities are
addressed by, what counts as identity versus payload, when a re-emitted observation
refines in place, how `op="retract"` works, the
`source_key_id` provenance gate, and the merge re-homing every conflict-delete owes
its ancillary rows. Table definitions live in `docs/SCHEMA.md`.

---

## Identifier types — the identity vocabulary (#459)

Every observation addresses its entity by `identifier_type` + `identifier_value`. The
registered set is a **live catalog, not a constant**: admin curates the table
(`settings_identifier_types.py`), so query
`GET /api/v1/entity-identifier-types` rather than hardcoding slugs — it returns
`id, slug, entity_type, display_name, full_name, is_internal`. An *unknown* slug is rejected outright
(`unknown_identifier_type`); a **valid-but-wrong** one is not — it silently mints a
duplicate entity, and identity duplication propagates into every assignment, event and
citation hung off the fork.

| Method | Path | Auth | Notes |
|---|---|---|---|
| `GET` | `/api/v1/entity-identifier-types` | API key | Unpaginated list of the whole vocabulary, `ORDER BY slug`. No `entity_type` filter — filter client-side. ETag caching (content hash) — see [Conditional requests](PUBLIC_API.md#conditional-requests). |

`is_internal` splits the vocabulary in two:

- **Internal (`pm_*`)** — addresses an existing entity by its PM ULID and can never
  create one (`pm_id_not_found` on a miss). Refused in `additional_identifiers`: an
  internal type is how you *reach* an entity, not a scheme you attach to one.
- **External** — auto-attaches on a known value (`auto-attached`), creates on an
  unknown one (`new`).

**Value conventions.** The catalog has no value-format column, so these stay here — the
ones worth knowing before minting:

| Slug | Entity | Value |
|---|---|---|
| `person_wa_pdc` | person | PDC numeric person-stable `person_id` |
| `person_wa_pdc_filer` | person | PDC per-filer registration key (#293) |
| `person_wa_pdc_lobbyist_agent` | person | PDC lobbyist `agent_id`; one person may carry several (#295) |
| `person_wa_legislature_member_id` | person | WSL member id — sponsor wire, bottoms out at 1991 |
| `person_wa_legislature_roster` | person | `<name fold>:<first session year>`, e.g. `aeolson:1923` — the archival 1889–2025 roster below the member-id floor (#456) |
| `observo_speaker` | person | opaque Observo ULID |
| `org_ubi` | organization | WA Unified Business Identifier |
| `org_wa_pdc` | organization | PDC **lobbyist-firm** filer_id, numeric |
| `org_wa_pdc_committee` | organization | PDC **campaign-finance** committee filer_id, e.g. `LABORG 503` (#296) |
| `org_wa_party` | organization | bare lowercase party slug — `democratic`, `republican` (#270) |
| `org_wa_legislature_committee_id` | organization | WSL numeric `committee_id` (successor committees get their own id) |

---

## Org lifespan bounds on assignments (#307)


An org's lifespan end is **derived, not a column**: `v_org_lifespan(organization_id, ended_on)` takes the earliest non-archived `dissolved` / `merged_with` entity event, resolved to the *latest* date within the event's known precision (year-only 2023 → `2023-12-31`; month-only → last day of month) so closing an assignment at `ended_on` never claims an earlier end than the source supports. `renamed` / `split_from` imply continuity, not an end; an end event without a year (`merged_with` doesn't require one) derives no bound. `organizations.active` is dateless state and `archived_at` is admin bookkeeping — neither is a lifespan; an org marked inactive **should** also get an end event when the date is known.

**Invariant:** an assignment's window falls within its org's lifespan.

- Org ended → no `is_current=TRUE` assignment on its roles (hard).
- `start_date` / `end_date` ≤ `ended_on` when both known (contradiction otherwise).
- `is_current=FALSE, end_date NULL` = **unknown end**, not "ongoing" — allowed on an ended org; never invent an end date for it. Exclude these rows from "current members" displays (`is_current`, not `end_date IS NULL`, is the currency signal).

**Enforcement (deliberately app-layer, no DB trigger):** `src.core.org_lifecycle.check_assignment_lifespan(conn, role_id, *, is_current, start_date, end_date)` raises `AssignmentOutsideOrgLifespan` (codes mirror the audit categories); all three admin write surfaces call it (role-assignments section, role-detail inline rows, person-detail inline rows) and render `lifespan_error_message(exc)` inline. The public observation path is *not* gated — server-to-server writes record what the source asserts and `scripts/audit_org_lifecycle_assignments.py` reconciles (report mode lists violations; `--execute` closes `current_on_ended` rows at `ended_on` with a provenance note; contradictions and unknown-end rows are report-only). A cross-table temporal trigger would misfire on messy, out-of-order ingested history — revisit only after the audit runs clean. Complements the *role*-level `established_on`/`abolished_on` bounds (`_check_assignment_within_bounds` in `roles_shared.py`), which remain a pure-date check against the role row.

**UX:** org detail shows a warning banner when an archived/inactive/ended org still carries open assignments; marking an org inactive flashes the open count. Close/re-home flows ride on #266 / #305 tooling.

---

## Assignment observations (#311/#391)

Moved to [API_ASSIGNMENTS.md](API_ASSIGNMENTS.md) §"Write semantics & provenance" —
update-in-place semantics, the `unapplied` echo, the `source_key_id` gate, and
`op="retract"`, beside the endpoint they apply to.

---

## Org parent — authoritative reparent & provenance (#334)


`organizations.parent_id` on the org observation path is the org analog of the #311 assignment split — same "identity vs. authoritative field-update" shape, resolved on the **identifier mode**, not the parent-specifier (`organization_parent_id` / `_name` / `_acronym` all resolve to one `parent_id` first, then share the write):

- **PM-native (`pm_org_id`) = the authoritative reparent channel.** The producer proves it means exactly this org, so `write_org_parent(..., authoritative=True)` *replaces* the stored parent — the fix for the reported bug where re-observing an already-anchored subcommittee with the correct parent was a silent no-op. Guards reject before touching the row: `parent_not_found` (unknown/archived parent), `parent_cycle` (self-parent or an ancestor loop caught from `trg_no_org_cycle` — the app pre-checks self + existence, then maps the trigger's `RaiseError`), `source_key_mismatch`.
- **Natural-key / external-identifier auto-attach = write-if-null.** Fills the parent only when currently NULL (and claims source on that fill); an org that already has a parent is left untouched — a natural-key match never reparents. There is no `unapplied` echo (unlike assignments) because the only parent delta a producer can assert authoritatively is via `pm_org_id`; a silent no-op on the natural path is the expected monotonic behavior. Filling a NULL parent with a descendant would close a loop → rejected `parent_cycle` (the write-if-null UPDATE fires `trg_no_org_cycle` too; both paths map the `RaiseError` via `_set_org_parent` rather than bubbling a 500).
- **Provenance:** `organizations.source_key_id` (the #162/#311 pattern) is claimed via `COALESCE` on the first write (either mode), never stamped at org creation (`_create_entity` leaves it NULL — the claim is lazy, on first parent write). An authoritative reparent requires `source_key_id IS NULL OR = caller`; a NULL source (admin-set via `inline/parent/`, or pre-#334) is claimable-once. Curator-precedence (admin parent always wins) is **not** modelled — a NULL-source curator parent is claimable, symmetric with #311; revisit only on real curator/producer hierarchy contention.
- **Idempotence:** re-asserting the same parent (authoritative) is a quiet no-op *before* the provenance gate, so a mirroring producer re-emitting current state never sees `source_key_mismatch` and never bumps `updated_at` (no producer↔PM ping-pong). Same ordering as #311.

---

## Event observations — refine-in-place, partial-success, `succeeded_by` & retract (#321/#322)


Entity events are producer-writable two ways: **embedded** in the org/person observation payload (`events: [...]`, all-or-nothing — a rejected event raises `ObservationRejected` and rolls the whole observation back) and via the **event-native** `POST /api/v1/orgs/{org_id}/events/observations` (partial-success). Both go through the same per-event core (`_apply_one_event`); neither decouples events from the org LWW clock — an event INSERT/UPDATE still fires `trg_touch_entity_on_event_change` → `organizations.updated_at`, which is load-bearing for the usa-wa `sync_entity_events` reconcile.

- **Identity vs. mutable — the #311 analog.** `(event_type, linked_entity)` is **identity**; `date`/`notes`/`place`/`visibility` is the mutable set. A `pm_event_id` update refines only the mutable set (the date as a unit — a year-only sharpening clears finer precision). Changing `event_type` or a supplied, differing `linked_entity` is a *different* event → rejected `identity_immutable`, never a silent reclassify (mirrors `resolve_role`'s never-reclassify rule).
- **Diff-before-write no-op gate.** An unchanged `pm_event_id` re-emit skips the UPDATE (so `updated_at` doesn't bump and re-arm the producer↔PM ping-pong) and returns `auto-attached`. The no-op check precedes the provenance gate, so an identical redelivery by a foreign key stays quiet (same as #311).
- **Provenance:** `entity_events.source_key_id` stamped on create; a refine requires `source_key_id IS NULL OR = caller` (claimed via `COALESCE`), else rejected `provenance_conflict`. Admin surfaces are not gated.
- **Partial-success + reason slugs.** The event-native endpoint runs each event in its own savepoint: a rejection rolls back only that event and is reported alongside the ones that landed. This gives **ordering-tolerance for free** — a `succeeded_by` ahead of its (unanchored) successor comes back `linked_entity_unresolved` (the one **transient** reason — self-heals next cycle) while its siblings commit. Terminal reasons: `identity_immutable`, `event_not_found`, `provenance_conflict`, `applies_to_mismatch`, `missing_required_field`, `unknown_event_type`, `invalid`. Per-event dispositions (`new｜auto-attached｜updated｜retracted｜rejected`) surface in `ObservationResponse.events` (embedded) and `EventObservationsResponse.results` (event-native).
- **`succeeded_by` slug.** The renamed-continuity link (WA committee re-keys): `applies_to=organization`, `requires_linked_entity`, no year. Direction (not derivable from the row): the event lives on the **predecessor**; `linked_entity_id` → successor. `founded`/`dissolved` are the lifespan window; `split_from`/`merged_with` are branches; each event carries exactly one linked entity, so multi-way re-orgs are expressed pairwise.
- **Retract / void (#322).** `op="retract"` on an event item archives the `pm_event_id`-addressed event (`archived_at = now()`, never hard-delete) — the only correction for a mis-linked **dateless** linked event (`succeeded_by`/`split_from`/`merged_with`), which has no mutable field to refine, so a re-link is **create-new + retract-old** (both land in one partial-success batch). Retract is always id-addressed (no `pm_event_id` → `invalid`); the supplied `event_type` — **and** a supplied `linked_entity` — must match the stored row (else `identity_immutable`, guards a copy-paste `pm_event_id`, symmetric with the refine guard); provenance is the same-or-NULL gate (foreign non-NULL source → `provenance_conflict`), and any refine payload is ignored. Two lookups deliberately see archived rows (each for its own reason): (1) the **create-path** content-dedup — a retract is **authoritative**, so re-observing content identical to a retracted event does **not** resurrect it; the dedup matches the archived row and returns `auto-attached` (no fresh row, event stays retracted), mirroring the address dateless-reobservation anti-resurrection rule (§"Address validity windows"). Un-retracting is a deliberate act (admin unarchive), not a side effect of re-observation. (2) the **retract-path** lookup — unlike refine (which filters `archived_at IS NULL`), it is unfiltered so an already-archived retract is a **diff-gated no-op** (`auto-attached`, no UPDATE, no clock bump) — checked before the provenance gate — so a producer that re-emits the retract every cycle doesn't re-arm the ping-pong. The archiving UPDATE fires the same org-touch trigger → outbox row, so subscribers drop the stale anchor (public event reads already filter `archived_at IS NULL`). Works on both transports (embedded all-or-nothing, event-native partial-success). Not gated to dateless links — the op is general; datable events keep refine as the primary correction, retract is the escape hatch when the event shouldn't exist at all.

---

## Citations — write semantics (#319)


A **citation** is human-checkable evidence (`url` / `title` / `excerpt` / `accessed_at`) for a fact, attached to an entity or one of its fields. It is a fifth provenance axis, distinct from `source_key_id` (actor), `import_provenance` (ingestion batch), and `field_confidence` (automated reliability) — curated, observable, and retractable. It supersedes the ad-hoc `role_assignments.notes` capture (#314/#318). Design doc: `docs/plans/2026-07-29-citations-pattern-design.md`.

- **Table:** `citations` — polymorphic no-FK ancillary over **seven** citable types (`organization`, `person`, `role`, `role_assignment`, `jurisdiction`, `person_name`, `entity_event`). `chk_citation_url_or_title` (a URL-less citation must carry a title). Soft-delete via `archived_at` (never hard-delete).
- **Identity** = `(entity_type, entity_id, field_name, url)`, `uq_citation_identity` with **`NULLS NOT DISTINCT`** over active rows: a NULL `url` (and NULL `field_name` = whole-entity) is one distinct slot, so at most one URL-less citation per `(entity, field)`. `title`/`excerpt`/`accessed_at` are mutable payload, never identity.
- **Observation semantics** (mirror events #321/#322, in `src/core/citations.py`): natural-key observe → refine the matched active row or create; `pm_citation_id` → id-addressed refine (identity immutable → `identity_immutable`); `op="retract"` archives the id-addressed row and is anti-resurrection-safe (re-observing retracted content auto-attaches to the archived row). Diff-before-write no-op precedes the `source_key_id` same-or-NULL provenance gate (`provenance_conflict`). `field_name` (non-NULL) validated against the per-entity `CITABLE_FIELDS` allowlist (`citable_field_unknown`); reason slugs incl. one transient `entity_unresolved`.
- **Transports:** `write_citations` (embedded on org/person observation payloads — all-or-nothing) and `apply_citation_observations` (the citation-native endpoint — partial-success). See `docs/PUBLIC_API.md`.
- **`entity_changes` emit:** DB touch trigger `trg_touch_entity_on_citation_change` (per the #327 model — no app-layer emit). Sub-entity citations indirect: a `person_name` citation touches the owning person, an `entity_event` citation touches the event's owning entity.
- **Merge re-homing:** every merge/delete that collapses a citable entity re-homes its citations onto the survivor **before** the delete (`migrate_citations`/`rehome_citations`, NULL-safe active-scoped dedup) — wired into `rehome_conflicting_assignment_ancillary` (role_assignment), `rehome_role_ancillary` (role), `delete_role_ancillary` (drop), and the primary person/org DELETE in `people_merge`/`orgs_merge`. Citations self-emit the survivor signal via the touch trigger. **Sub-entities** (`person_name`/`entity_event`) are handled proactively too: `people_merge` re-homes a deduped loser name's citations onto the surviving winner name (same LATERAL match as the #309 reading re-point) and drops citations for curated/purged names; `entity_event` rows aren't re-pointed by any merge (they dangle when the parent is deleted), so `delete_event_citations_for_owner` drops their citations before the person/org DELETE, and the admin event/name hard-delete paths call `delete_citations`. The daily orphan audit (`count_orphaned_citations`, `citation.<type>` scope) remains the backstop.

---

## Role-assignment relationships — RA→RA edges (#301)


A **role-assignment relationship** is a directional, temporal edge between two `role_assignments`: the staffer's assignment (`from`) serves a principal legislator's seat assignment (`to`). It models a person→person staff relationship the flat role model can't hold, while preserving the object assignment context (org, role, window) on both sides. Anchoring on assignments (not people) is deliberate: biennium turnover = new assignments on both sides = a new edge. Design doc: `docs/plans/2026-08-01-assignment-relationships-design.md`.

- **Table:** `role_assignment_relationships` — FK-backed (both endpoints `REFERENCES role_assignments(id) ON DELETE CASCADE`), soft-delete via `archived_at`. Typed via the `role_assignment_relationship_types` catalog (seed `staff_of`, `is_symmetric=FALSE`, from=staffer→to=principal). `chk_no_self_rel_assignment` + `chk_edge_valid_range`. **Identity** = `(from_assignment_id, to_assignment_id, rel_type_id)` — `uq_assignment_relationship_identity` over active rows; `valid_from`/`valid_until`/`notes` are mutable payload.
- **Temporal invariant:** an active edge's window ⊆ the intersection of both endpoint assignment windows, and the edge dies when either endpoint ends. Enforced **app-layer only on admin/direct writes** (`check_edge_within_assignments` → `EdgeOutsideAssignmentWindow`); the observation path **records freely** (mirrors #307). No blanket DB invariant trigger (it would block the observation record-freely contract). Steady-state drift is reconciled by `scripts/audit_assignment_relationship_windows.py`.
- **Cascade (DB trigger `cascade_assignment_relationships` on `role_assignments`):** when an endpoint's window shrinks or it is archived, dependent active edges auto-**clamp / archive** — `valid_from` clamps up only a *defined* start (an unknown start is never invented, #307); `valid_until` clamps down a defined end **and** closes a NULL/ongoing edge at a defined endpoint end; a clamp that inverts the window archives the edge; an archived endpoint archives the edge. WHEN-gated on `start_date`/`end_date`/`archived_at` so the touch-trigger `updated_at` bump doesn't recurse. Every mutation is an edge `UPDATE`, so it self-emits (observable auto-clamp). The audit shares this exact clamp rule so trigger and audit never diverge.
- **Change feed:** the edge has its **own** `entity_type` `role_assignment_relationship` (`trg_entity_changes_role_assignment_relationships`) — independently addressable/retractable — **and** touches both endpoint assignments (`trg_touch_assignments_on_relationship_change`), so it surfaces on each assignment's feed too. `entity_type` CHECKs extended on `entity_changes`/`deleted_entities`/`api_key_entity_subscriptions` (+ subscription resolve UNION) so an edge id is subscribable. This goes further than the admin-only, touch-only `jurisdiction_relationships` precedent.
- **Observation semantics** (`src/core/assignment_relationships.py`, mirrors events/citations): pm-native only — each claim references its endpoints by `pm_assignment_id`. Natural-key observe (`from`+`to`+`rel_type`) → refine matched active row / anti-resurrect archived twin / create; `pm_relationship_id` → id-addressed refine (identity immutable → `identity_immutable`); `op="retract"` archives (already-archived no-op). Diff-before-write precedes the `source_key_id` same-or-NULL gate (`provenance_conflict`). Reason slugs incl. transient `assignment_unresolved`, plus `rel_type_unknown`/`self_relationship`/`relationship_not_found`.
- **Transports:** native `POST /api/v1/assignment-relationships/observations` (partial-success) + read `GET /api/v1/assignments/{pm_assignment_id}/relationships` (both directions); scopes `assignment_relationships:read`/`:write`. No embedded transport (the edge references assignments, not a parent entity). Admin panel on the role-assignment detail (`src/api/admin/role_assignments_relationships.py`).
- **Merge re-homing:** the edge is FK-backed so it can't *orphan*, but a merge's hard-delete of a losing assignment would silently **CASCADE-delete** its active edges. `rehome_assignment_relationships` re-points them onto the survivor (deleting self-edges + winner collisions) **before** the DELETE — wired into `people_merge`, `orgs_roles::role_merge`, and both `orgs_merge` role-pair sites. Re-point/delete self-emit via the edge's touch + change triggers (no manual signal). No orphan-audit scope (FK makes orphans impossible).
- **Backfill:** `scripts/backfill_assignment_relationships.py` resolves the 3 concrete #266-descoped rows (heuristic, supervised; misses reported, never guessed).

---

## Ancillary rows

Merge re-homing (#324/#326) and the DB touch triggers that emit a parent
`entity_changes` signal for ancillary edits (#327) live in `docs/ANCILLARY.md` —
both are properties of the polymorphic ancillary tables rather than of any one
observation kind.
