---
title: Implementation plan — assignment-relationships (#301)
date: 2026-08-01
status: draft
---

# Implementation plan — assignment-relationships (#301)

Design: `docs/plans/2026-08-01-assignment-relationships-design.md` (approved).
Branch: `feat/301-assignment-relationships`. TDD throughout (red → green → refactor).

## Problem

Staffer→principal relationships survive only as free text in role titles. #301
models them as a directional, temporal Role-Assignment → Role-Assignment edge.
The build spans schema + triggers, core observation logic, temporal-integrity
cascades, public API, admin UI, merge integration, audit, and backfill — a wide,
shared-state change that needs an ordered, independently-verifiable sequence.

## Approach

Build bottom-up in dependency order: schema + triggers first (the cascade/change-
feed behavior everything else relies on), then the core edge-resolution module,
then the two API surfaces (public observation, admin UI), then merge re-homing,
then the operational scripts (audit, backfill), then docs/version. Each step lands
its own failing tests first and is independently verifiable against a scratch DB
(`PARITY_REFERENCE_URL`-style) or the rollback endpoint client. No step depends on
a later one.

## Tradeoffs / alternatives

- **One mega-commit** — rejected: unreviewable, and a trigger bug would be
  entangled with API changes. Vertical slices keep each surface bisectable.
- **API-first, schema stubbed** — rejected: the temporal cascade *is* the hard
  part and lives in the DB; stubbing it defers the real risk and forces rework.
- **Skip the DB-trigger backstop, app-guard only** — rejected: direct-SQL and
  admin/observation paths must all be safe; #307/#312 precedent is trigger +
  app guard. The trigger is the non-negotiable orphan-prevention layer.

## Steps

1. **Schema + triggers.** Add `role_assignment_relationship_types` catalog (seed
   `staff_of`) + `role_assignment_relationships` edge (FKs `ON DELETE CASCADE`,
   `chk_no_self_rel`, `chk_edge_valid_range`, identity unique index). Add triggers:
   `trg_entity_changes_role_assignment_relationships` (extend `fn_record_entity_change`
   CASE), `trg_touch_assignments_on_relationship_change` (bump both endpoints),
   `trg_cascade_assignment_relationships` on `role_assignments` (archive/clamp/invert→archive).
   (No blanket edge-window invariant trigger — it would block the observation
   path's record-freely contract; enforcement is app-guard-only per #307.) Extend `entity_type` CHECK
   on `entity_changes` / `deleted_entities` / `api_key_entity_subscriptions` via
   idempotent reconciliation `DO` blocks (#312). *Verify:* `apply-schema.sh` on a
   fresh scratch DB clean; pgTAP-style/asyncpg trigger tests for each cascade branch
   (archive, clamp-earlier-end, clamp-later-start, clamp-inverts→archive).

2. **Core edge module** `src/core/assignment_relationships.py`: `resolve` (refine-
   in-place on `(from,to,rel_type)` + `pm_relationship_id` direct refine),
   `op="retract"` (archive + anti-resurrection), `source_key_id` same-or-NULL gate,
   `check_edge_within_assignments()` (app guard, raises `EdgeOutsideAssignmentWindow`),
   diff-before-write no-op. *Verify:* unit tests for each — create, refine no-op,
   refine-with-change, retract, re-emit-after-retract no-op, provenance conflict,
   window-violation raise.

3. **Public API.** `src/api/public/assignment_relationships.py`:
   `POST /api/v1/assignment-relationships/observations` (partial-success, per-item
   savepoint + disposition slug), `GET /api/v1/assignments/{pm_assignment_id}/relationships`.
   Pydantic response models + `operation_id` + `fmt_ts` serializers; list meta
   envelope + unique final `ORDER BY` column. Seed scopes
   `assignment_relationships:read` / `:write`; wire read-bucket rate-limit extra.
   Extend `/changes` + `/subscriptions` entity_type enums. *Verify:* rollback-client
   integration tests (auth 401/403, partial-success dispositions, refine, retract,
   read pagination, scope enforcement).

4. **Admin UI.** `src/api/admin/role_assignments_relationships.py` + templates:
   "Relationships" panel on role-assignment detail, both directions ("serves →" /
   "← served by"), typeahead to pick the other assignment, HTMX partial CRUD with
   `flash_trigger` + `with_flash` non-HTMX fallback (§32/§351), `markupsafe.escape`
   on DB values. *Verify:* endpoint tests incl. the `test_mutation_fallback_sweep`
   + flash-level guards; HTMX + non-HTMX paths.

5. **Merge re-homing.** `rehome_assignment_relationships(db, losing, surviving)`
   in `ancillary_migrate.py` (re-point both sides, dedup on identity collision).
   Wire before the assignment DELETE in `people_merge.py`, `orgs_roles.py::role_merge`,
   `orgs_merge.py` role-pair. Add `assignment_relationship` scope to
   `audit_ancillary_orphans.py`. *Verify:* merge tests across all three paths incl.
   the collision-dedup case; orphan-audit count test.

6. **Audit reconcile script.** `scripts/audit_assignment_relationship_windows.py`
   — anti-join edges whose window exceeds the endpoint intersection; `--execute`
   clamps/archives. *Verify:* dry-run reports, `--execute` fixes, idempotent
   re-run clean. (Systemd timer unit deferred to deploy — note in COMMANDS.md.)

7. **Backfill script.** `scripts/backfill_assignment_relationships.py` for the 3
   rows: parse principal from title → resolve principal seat assignment overlapping
   staffer window → mint edge, `valid_*` = intersection; `notes` untouched; emit
   operator cleanup list. *Verify:* dry-run against a seeded fixture resolves all 3;
   `--execute` guarded/supervised.

8. **Docs + version.** Update `docs/CONVENTIONS.md` (new §), `docs/STYLE.md §32`
   (admin panel), `docs/COMMANDS.md` (audit/backfill), `AGENTS.md` DB/Public-API/
   Infra rules; bump `pyproject.toml` + `package.json` together. *Verify:*
   `check-version-sync` hook; full `pytest` + `ruff` green; schema-parity audit
   clean against a fresh reference DB.

## Open questions / risks

- **Cascade trigger recursion/perf** — `trg_cascade_assignment_relationships`
  updates edges, which fire the edge's own change trigger. Confirm no trigger loop
  (edge UPDATE doesn't re-touch the assignment that fired the cascade in a cycle);
  guard with a same-value no-op check if needed.
- **Backfill principal resolution ambiguity** — "Sen. Saldaña" etc. resolved by
  name + seat role_type + temporal overlap; a miss/ambiguity must surface in the
  dry-run list, not silently skip. Supervised `--execute` only.
- **Timer/systemd for the audit** — installing the `.timer` unit is a deploy-time
  infra step (like the #324 ancillary-orphans timer), tracked separately from the
  code merge.
