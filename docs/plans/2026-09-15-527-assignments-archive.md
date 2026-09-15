---
title: "#527 (#500 delivery 2, PR A): the assignments model and retraction by archive"
date: 2026-09-15
status: draft
---

# #527: the assignments model and retraction by archive

Design: `docs/plans/2026-09-14-assignments-archive-design.md` (approved
2026-09-14).

## Problem

The applier cannot apply usa-wa's `assignments` yet.
- Its entity shape refuses `retraction: archive`, and it has no restore.
- Its create inserts only an `id`, which `role_assignments` rejects (it needs
  `person_id` and `role_id`).
- Its column shape reads a null as "no claim", so an open span can never clear
  PM's `end_date`.

Without these, the 384 absent spans and usa-wa#289's 349 reopened party spans
cannot be expressed. #501's supervised pass then has nothing to size.

## Approach

Extend the manifest's entity and column shapes, keeping the applier generic:
- **Entity shape:** `identity` (columns written at create, with person and role
  references resolved from the live crosswalk or minted in the same run),
  `unique_live` (the partial index, for collision checks at diff time) and
  `supersession` (report pairing).
- **Column shape:** `asserts_null`, and a per-column `overlay` map.
- **New entry kinds:** `archive` and `restore`, gated by thresholds `archives: 0`
  and `restores: 0`. Only rows the applier archived are ever restored, which
  `producer_crosswalk.retracted_at` records.
- **Models:** `desired_role_assignments` and `desired_role_assignment_dates`.
- **Admin:** three `assignment` pin slots, with `tracked()` on the four
  date-writing routes.

## Tradeoffs / alternatives

- **An assignment primitive in core.** Rejected: it makes two write paths, and
  a dry run would have to predict what the primitive will do.
- **Computing absence in the models.** Rejected: the retraction policy would
  leave the manifest, and the models cannot see live twins.
- **Flag names.** `--allow-archives N` and `--allow-restores N`, matching the
  existing `--allow-creates` and `--allow-merges`. The design doc said
  `--max-…`; this is the only departure from it.

## Steps

Test first, one commit per step, `#527` in each message.

1. **Schema.** Add `producer_crosswalk.retracted_at TIMESTAMPTZ`, inline and
   with `ADD COLUMN IF NOT EXISTS`.
   - Tests: `test_schema_constraint_migrations.py` (a table without the column
     gains it) and `test_schema.py` (the column exists and is nullable).
   - Docs: a SCHEMA.md clause, at no net size.
2. **Manifest.** The loader learns:
   - thresholds `archives` and `restores`;
   - on an entity target, `identity` (a plain column, or
     `{column, entity}` for a reference), `unique_live` (PM columns) and
     `supersession` (PM columns);
   - on a column target, `asserts_null` (target columns);
   - the `overlay` map form, while one string stays valid.

   It refuses:
   - these keys on the wrong shape;
   - a `retraction: archive` entity table without `unique_live`;
   - an `asserts_null` column that is not in `columns`;
   - an overlay map whose keys are not owned columns.

   Tests: `test_manifest.py`. The production `manifest.yml` is unchanged in
   this step.
3. **Diff: archive, restore and PM-archived rows** (`applier.py`, unit tier
   with fakes).
   - `ENTRY_KINDS` gains `archive` and `restore`. The crosswalk read carries
     `retracted_at` on both `LiveStore`s, and the new
     `live_holders(table, columns, tuples)` joins the protocol and the fake.
   - `_diff_entity` for `retraction: archive` covers every row of the design's
     table. An anchored row PM archived (no `retracted_at`) becomes a
     non-blocking `retract` entry with its reason.
4. **Diff: identity creates and collisions.**
   - A create resolves its identity references: anchored, created in the same
     run, or `stale` when neither.
   - A create or restore that a live holder blocks becomes a `conflict`, and a
     holder that the same plan archives does not count.
   - A create gets a hint when an archived row holds its tuple.
   - `effects` carries `superseded_by` (published spans on the `supersession`
     tuple) on each archive.
5. **Diff: the column shape.**
   - `asserts_null` columns carry a null as a change.
   - A column update that changes a `unique_live` column of its table's entity
     binding is collision-checked.
   - Columns on a row PM archived are skipped, not `stale`, for tables whose
     entity binding archives.
   - On a row the run creates, columns the create's `identity` already wrote are
     left out.
6. **Report and gates** (`applier_report.py`, `scripts/apply_desired_state.py`).
   - `THRESHOLD_KINDS` gains `archives` and `restores`. Both kinds enter the
     digest, and the summary table picks them up.
   - `--allow-archives N` and `--allow-restores N` are added.
   - `summary.md` lists the supersession pairs.
   - Tests: `test_applier_report.py` and the script's unit tests.
7. **Writer and the Postgres store** (`applier_write.py`, `applier_pg.py`,
   integration tier).
   - Order: archives, then restores, then creates with their crosswalk rows,
     then children, then columns.
   - An archive or restore writes `archived_at` and `retracted_at`.
   - A create's INSERT carries the resolved identity columns.
   - `live_holders` is implemented with `NULLS NOT DISTINCT` semantics, and the
     `staff_of` cascade count is previewed in `effects`.
   - Integration tests:
     - an archive stamps `retracted_at` and archives its `staff_of` edge;
     - a restore clears it;
     - a collision is a `conflict` and never a unique violation;
     - usa-wa#289's shape in one verified transaction (archive the later
       segment, reopen the first, with the CHECK respected);
     - a person and its assignment created in one run.
8. **Models and manifest entries.**
   - `USA_WA_SOURCES` and `sources.yml` gain `assignments` and `roles`.
   - Models: `stg_usa_wa__assignments`, `stg_usa_wa__roles`,
     `desired_role_assignments` and `desired_role_assignment_dates` (the pins
     kept legal against the CHECK).
   - `overlay_field_unmapped.sql` gains the three `assignment` pairs, and
     `manifest.yml` gains both tables and the new thresholds.
   - dbt fixture tests (`tests/core/ingestion/mapping/`): the span_key key, the
     identity references, the dates rule, a pin on each slot, and the CHECK pair
     test.
9. **Admin pins.**
   - `overlay_slots.SLOTS` gains `assignment: start_date | end_date |
     is_current`.
   - The overlay routes learn the `assignment` entity, and the slot lines are
     hosted on the assignment's date and current edit form.
   - `tracked()` wraps the four date-writing routes (`people_assignments`,
     `roles_assignments_inline`, and `role_assignments`' `is_current` and dates
     endpoints).
   - The #498 sweep's regex gains `role_assignments` date writes, and the drift
     tests (`test_manifest.py` `OVERLAY_SLOTS`, `test_overlay.py`) learn the
     map form.
   - Endpoint tests; the browser tier covers the slot lines.
10. **Docs, version and gates.**
    - Docs: RUNBOOK_DESIRED_STATE.md (archive, restore, the flags, the
      non-blocking PM-archived report), ADMIN_OVERLAY.md (assignment slots),
      and an OBSERVATIONS.md note (the retract, anti-resurrection and
      `attached_archived` retirement for usa-wa is usa-wa#314's; no code).
      Plus the design doc's flag names.
    - Version 0.51.0 (pyproject, package.json, uv.lock).
    - Gates: pre-ship, then the integration tier alone, then the browser tier
      alone.
    - Last, a read-only rehearsal of the nightly from this worktree against
      production (`export_pm_tables`, `build_desired_state` with
      `--snapshot-root` on the main checkout's store, and `apply_desired_state`
      dry, writing artifacts under the worktree). It confirms roughly 384
      archives, 2 creates and 380 date updates, blocked. Plan status set to
      executed.

## Open questions / risks

- **Timing.** Once shipped, the nightly is blocked on archives as well as
  creates. That is intended: #501 sizes the first execute. The digest changes
  on the first run after deploy, so the streak restarts.
- **PM-archived rows as `retract`.** Reusing the report-only kind keeps
  `ENTRY_KINDS` small, but a reader must tell "absent from the snapshot" from
  "archived in PM" by the reason. If that proves unclear in #501's report, a
  dedicated kind is a small follow-up.
- **Admin surface.** Where assignment dates are edited is known (four routes).
  The slot line's placement is settled in step 9 against the existing form
  markup. The a11y browser tier covers it.
- **Rehearsal.** It reads production and writes only under the worktree's
  `data/`. If `build_desired_state` cannot point at the main checkout's
  snapshot store, the rehearsal waits for the deploy and the nightly.
