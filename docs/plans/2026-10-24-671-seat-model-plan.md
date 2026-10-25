---
title: "#671 PR B implementation plan: role capacity periods, membership_type, D7 audit"
date: 2026-10-24
status: draft
---

# #671 PR B: implementation plan

Design: [2026-10-24-671-seat-model-design.md](2026-10-24-671-seat-model-design.md)
(approved). Section references (§n, Sn) point there.

## Problem

usa-wa 2.1.0 is live and PR A's re-pin has been executed, but PM cannot yet
store seat capacity or substitutes. Until it can, the seat-occupancy ratchet
counts the lawful 1889 multi-member seats and the three military substitutes as
defects (48 pairs today, against an expected 25).

## Approach

Each layer lands as a tested slice: schema, mapping, applier, audit, API, admin,
docs. Every slice is red → green and its own commit(s). The audit slice is
proved against prod (read-only) before the PR opens. With no periods and all
rows `principal`, it must reproduce today's 48 pairs exactly; fed usa-wa's
published inputs, it must predict their 25. One PR, minor bump to v0.68.0.
BASELINE is 48 in this PR; the measured count follows in a later PR (S8).

## Tradeoffs / alternatives

- **Split PR B into schema+applier and audit+API PRs.** Rejected: two deploys
  and two parity windows for one feature; the audit's 48-proof already isolates
  its risk.
- **Prove the applier plan against prod before merge.** Not possible: prod
  has no `role_capacity_periods` table until deploy. The fixture plan test plus
  the post-deploy dry run (317 inserts + 3 updates, nothing else) stand in.

## Steps

1. **Schema (§2).**
   - Test first (integration, test DB): both period CHECKs; the membership
     CHECK and its default; the partial unique index; cascade on role delete;
     the reconciliation block applied twice.
   - Then `schema.sql`: table, comment, trigger, column, `DO` block.
   - Apply with `bash scripts/apply-schema.sh --test`.
   - Done when the new tests pass and the schema-parity test is green.
2. **Mapping (§3).** *Revised:* split into 2a (`membership_type`, an existing
   binding shape) and 2b, which lands with step 3, because
   `test_manifest.py` refuses a mart without its manifest binding.
   - Pin `role_periods`; wire `PM_SRC_ROLE_PERIODS` and `USA_WA_SOURCES` like
     `org_lineage`.
   - Add `stg_usa_wa__role_periods`, plus `membership_type` in
     `stg_usa_wa__assignments`.
   - Add both marts, the `unresolved_role_capacity_periods` warn test, the
     `membership_type` overlay field, and the manifest `types`.
   - Fixtures first.
   - Done when the fixture tier passes and `test_output_types.py` passes on
     empty and populated stores.
3. **Applier (§4).**
   - Tests first:
     - `owns_rows: all` is accepted on a child and refused elsewhere.
     - A child archive without it is refused.
     - `overlay: none` is refused for a table outside `DATASET_OWNED_TABLES`.
     - A dropped period archives; a period on an out-of-scope role is
       untouched; an update writes null.
     - The fixture plan is N inserts + 3 updates.
   - Then `manifest.py`, `dataset_ownership.py`, `_absent_children`, and the two
     bindings.
   - Done when the applier and manifest tests pass, `test_manifest.py` holds the
     constant to the manifest, and the existing #592 lineage tests still pass.
4. **Audit (§5).**
   - Unit and integration tests first: every case in the spec's Testing list.
   - Then rewrite `_OVERLAPS_SQL` on D7 and set BASELINE 48.
   - Done when the tests pass.
5. **Pre-merge prod evidence (read-only).**
   - Run the new SQL against prod with an empty periods CTE and `membership_type`
     read as `principal`. Expect 48 pairs, identical to the current audit's pair
     log.
   - Run it again with the five published multi-member periods and the three
     substitute span_keys fed inline. Expect 25 pairs, identical to usa-wa's list
     once mapped through the crosswalk.
   - Post both results on #671. A mismatch stops the plan; trace it first.
6. **API (§6).**
   - Test first: list and detail return `membership_type`.
   - Then the model field and the selecting queries.
   - Bump to 0.68.0 everywhere, and run `scripts.regenerate_client`.
   - Done when the drift and version-sync checks pass.
7. **Admin badge (§6).**
   - Test first: a substitute row renders the badge; a principal row doesn't.
   - Then the three templates, with row queries selecting the column.
   - Done when admin tests pass and the a11y tier (run alone) is clean.
8. **Docs.**
   - Pages: `SCHEMA.md`, `SCHEMA_INDEXES.md` (the seat-cardinality note),
     `AUDITS.md` (D7 and the baseline), `RUNBOOK_DESIRED_STATE.md` (the new
     binding, `owns_rows`, the dataset-owned table), and `API_ASSIGNMENTS.md`.
   - Done when the context-budget and doc gates pass.
9. **Review and pause.**
   - Run `CR --fix` and the full pre-ship gate.
   - Open PR B: no closing keywords; the after-merge steps follow the spec's
     Rollout. List skipped verification on #671.
   - **Stop** for operator approval before merge, pull, restart, the services
     and the execute.

## Open questions / risks

- **`test_real_snapshot.py` needs `role_periods` in a store.** The worktree has
  no store, and the main store lacks `role_periods` until the pin deploys. Plan:
  land it in a scratch store (`pull_datasets --root <scratch>`, if the flag
  exists), measure 317 and 3 there, and record the commands. Otherwise the
  counts are checked post-deploy, and that is listed as skipped verification.
- **The integration and browser tiers share the test DB** (#480/#300). Run them
  alone, never alongside another worktree's integration run.
- **Merge-to-execute window.** After deploy, the 00:01Z audit reads 48 against
  BASELINE 48, which passes, until the execute lands the data and the count
  drops (exit 0 with a WARNING). No alarm path.
