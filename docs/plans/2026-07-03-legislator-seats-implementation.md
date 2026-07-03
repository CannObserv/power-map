---
title: Implement canonical legislator seat-Roles (#261)
date: 2026-07-03
status: draft
---

# Implement canonical legislator seat-Roles (#261)

Design: `docs/plans/2026-07-03-legislator-representation-design.md`
Issue: #261
Branch: `feature/legislator-seats`

## Problem

There is no structured way to represent a legislative seat (office + district +
position). `roles` is title + org only; `observation.resolve_role` dedups by
`(organization_id, lower(title))` and auto-attaches to any active match, so two
"State Representative" seats in the same chamber (LD-5 Position 1 vs Position 2,
or two different districts) cannot coexist without silently collapsing.
Aggregating "all Representatives" / "all Senators" depends on fragile title
string matching. #261 approved modeling each seat as a durable `roles` row
carrying structured `role_type_id` + `jurisdiction_id` + `qualifier`.

## Approach

Land the approved design as a schema foundation plus the minimum end-to-end
plumbing to create and read seat-Roles through the public API, TDD throughout.
Add a `role_types` classifier + three nullable `roles` columns; split the single
title-uniqueness index into a districted (seat-identity) partial index and a
non-districted (title-identity) partial index; make `resolve_role` seat-aware so
distinct seats no longer collapse; surface the new fields on the public roles
read and observation write paths. Admin dashboard surfacing, WA district/seat
seeding, generic-role backfill, and leadership/committee roles are deferred to
separate follow-on issues (listed in the design doc's out-of-scope). Role
lifecycle dates already exist as `established_on` / `abolished_on`; no new date
columns.

## Tradeoffs / alternatives

- **Schema-only, defer the `resolve_role` change** — rejected: the moment two
  same-title roles per org are allowed, the existing observation write path
  auto-attaches a new seat to the wrong existing role (title collision). That is
  a silent correctness bug, so the write-path change is not optional.
- **Include admin UI surfacing in this plan** — rejected: the admin role surface
  is 7+ files plus merge/inline flows; folding it in makes the plan unreviewable.
  Admin keeps creating title-only (jurisdiction NULL) roles unchanged; the split
  index leaves that path's behavior identical. Separate plan.
- **Add new role validity-date columns** — unnecessary: `established_on` /
  `abolished_on` already exist and serve as seat lifecycle dates.

## Steps

1. **Schema** (TDD via a core schema/apply test): add `role_types` (id, slug,
   display_name, created_at) + seed `state_representative`, `state_senator`; add
   nullable `role_type_id` (FK role_types), `jurisdiction_id` (FK jurisdictions),
   `qualifier` to `roles`; replace `uq_role_org_title` with two partial unique
   indexes — districted `(organization_id, role_type_id, jurisdiction_id,
   qualifier) NULLS NOT DISTINCT WHERE jurisdiction_id IS NOT NULL AND
   archived_at IS NULL` and non-districted `(organization_id, lower(title)) WHERE
   jurisdiction_id IS NULL AND archived_at IS NULL`. Idempotent migration-block
   style. Verify `apply_schema` on fresh + already-migrated DB.
2. **Seat-aware `resolve_role`** (red first): failing tests that (a) same
   org+title but distinct `(jurisdiction_id, qualifier)` create distinct roles,
   (b) an exact seat match AUTO_ATTACHes, (c) legacy title-only path (jurisdiction
   NULL) is unchanged. Then implement: match key includes role_type/jurisdiction/
   qualifier when a jurisdiction is supplied, else fall back to `(org,
   lower(title))`.
3. **Observation write surface**: extend `RoleObservationRequest` + `resolve_role`
   signature to accept `role_type` (slug→id resolve) / `jurisdiction_id` /
   `qualifier`; validate FKs (reject unknown role_type slug or jurisdiction_id
   with a REJECTED disposition + reason). Tests for accept + both reject paths.
4. **Public read surface**: add `role_type_id` (+ slug), `jurisdiction_id`,
   `qualifier` to the list query, `_role_row_to_dict`, and `RoleDetail` /
   `RoleListResponse` schemas; keep existing `established_on` / `abolished_on`.
   Round-trip test: create a seat via observation, read it back on list + detail.
5. **Reconcile `deduplicate_roles.py`**: its conflict key / `uq_role_org_title`
   assumption changes under the split index; update the script and any other
   `uq_role_org_title` reference in lockstep; run `tests/scripts/
   test_deduplicate_roles.py`. Confirm `entity_changes` change-feed captures the
   new columns (no hardcoded column list in the role trigger).
6. **Docs**: correct the design doc note about role dates (established_on/
   abolished_on already exist); update `docs/CONVENTIONS.md` (roles/DB rules) and
   `docs/PUBLIC_API.md` for the new fields and seat-identity dedup. Update
   alongside code, not deferred.
7. **Verify**: full `pytest` + `ruff` + pre-commit green; spot-check the public
   roles endpoints against the dev server on 8001.

## Open questions / risks — resolved 2026-07-03

- **role_type enforcement** → DECIDED: require `role_type_id` when
  `jurisdiction_id` is set, via a CHECK (`jurisdiction_id IS NULL OR
  role_type_id IS NOT NULL`).
- **`uq_role_org_title` consumers** → AGREED: update `deduplicate_roles.py` (and
  any other reference) in lockstep (step 5). Missed reference would break dedup
  silently — grep-verify.
- **Public API shape** → DECIDED: expose `role_type` as `id` + `slug` (matches
  jurisdictions/link_types).
- **Change-feed / entity subscriptions** → AGREED: verify role column additions
  propagate through `entity_changes` without a trigger column-list edit.
- **Title still required** → AGREED: `roles.title` stays NOT NULL; observation
  callers supply a display title for seats. Auto-derived label deferred to the
  admin plan.
- Also register `role_types` in `tests/conftest.py::_REFERENCE_TABLES` so its
  seed survives per-session truncation.
