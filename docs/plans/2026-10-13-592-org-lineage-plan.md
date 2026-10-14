---
title: "#592 implementation plan: org_lineage links, the lock, the active hint"
date: 2026-10-13
status: draft
---

# #592 implementation plan

Design: [`2026-10-13-592-org-lineage-active-design.md`](2026-10-13-592-org-lineage-active-design.md) (approved).

## Problem

usa-wa now publishes committee lineage (`org_lineage` 1.0.0) and INV2 `active`. PM does not subscribe to the first and ignores the second. The design decides two things:

1. PM takes the links, and the applier owns them on anchored pairs. Those links are locked in admin and in the API, and a dropped link is archived through a gated step.
2. PM keeps `active` and adds a warn-level hint.

This plan sequences that work so each step can be checked on its own.

## Approach

TDD, bottom-up along the data flow: pin and staging, then the mart, then the manifest, then the applier. The lock goes in next (core predicate, then admin, then API), and the `active` hint, docs and version bump come last. Every step lands as its own commit(s), red test first. The applier and lock steps carry the risk, so they get the integration tests. Before the PR, a worktree-local dry run against the real snapshot and prod (read-only, no `--execute`) has to show the design's expected first run: 161 noop / 1 update / 0 archives / `clean`.

## Tradeoffs / alternatives

- **Lock first, mapping second:** rejected. The lock's type constant is pinned to the manifest by a drift test, so the manifest entry has to exist first.
- **One commit per layer for the entire step:** rejected. Red/green per behaviour keeps CR diffs reviewable. Steps group the commits; they don't merge them.
- **Skip the pre-PR real dry run and rely on the post-deploy one:** rejected. The fixture store can't show that the 162 published links key-match PM's rows. A mismatch there turns into 162 inserts, which the unique index would reject. That is cheap to catch before merging and expensive after.

## Steps

1. **Pin and staging.**
   - `sources.yml`: add `org_lineage` (major 1, hash `a7585012…`).
   - `mapping/__init__.py`: map `PM_SRC_ORG_LINEAGE`.
   - Add a fixture store version.
   - Add `stg_usa_wa__org_lineage`, with `not_null` + `accepted_values` on `slug` (severity error).
   - *Done when:* the pin/contract tests and a new staging test pass, and an unknown slug fails the fixture build.
2. **Mart.**
   - Add `desired_organization_lineage`: both ends through `desired_organizations` with a non-null `pm_id`.
   - Add the `unresolved_org_lineage` warn test and the `desired_organization_lineage_key_unique` test.
   - *Done when:* fixture tests cover a resolved link, a create end, an archived end and an unresolved end, and `test_output_types` holds from an empty store.
3. **Manifest and lock constant.**
   - Add the `desired_organization_lineage` table entry.
   - Loader: accept `overlay: none` only on a child binding whose `owned_event_types` all lie in the lock list.
   - Add `DATASET_OWNED_LINK_TYPES` in a new `src/core/dataset_ownership.py`, plus a drift test tying it to the manifest.
   - *Done when:* `test_manifest` covers acceptance on the lineage binding and refusal on an unlocked child and on a column binding, and the drift test is green.
4. **Applier diff and write.**
   - Diff:
     - `retraction: archive` on a child binding emits `archive` entries with `row_id` for owned-type rows the snapshot dropped.
     - The archive scope is the anchored pair: a link to an unanchored org is ignored.
     - Retract and archive rows carry every key column.
   - Write: a child archive updates `entity_events` by `row_id` and stamps no crosswalk row.
   - *Done when:* unit tests cover insert, year update, archive, unanchored link ignored, unowned type ignored, full key, archive blocking at threshold 0, and reinsert after archive. An integration test (run serially) executes the insert, update and archive against the test DB, and `desired_entity_events` tests stay green.
5. **Core predicate.**
   - Add `dataset_owned_link(conn, entity_type, entity_id, slug, linked_type, linked_id)` in `src/core/dataset_ownership.py`, reusing the `in_scope` SQL.
   - *Done when:* the integration truth table passes: both ends anchored, one end, neither end, each type, a person-linked event, an archived anchor.
6. **Admin lock.**
   - Refuse in the succession `link-successor` route and in all five events-panel doors (add, edit-row, archive, unarchive, delete). Check the row as it was before the write and as it would be after.
   - On refusal: a warning flash, plus a `with_flash` redirect fallback.
   - The row renders read-only with a `usa-wa` badge.
   - *Done when:* each door's tests (HTMX and fallback) pass, an edit moving a row into or out of the lock is refused, the template test passes, and `test_mutation_fallback_sweep.py` and `test_flash_levels.py` stay green.
7. **Observation API lock.**
   - Add `EventRejectReason.EVENT_OWNED_BY_DATASET`; create, refine and `op="retract"` on an org event return `REJECTED` with it.
   - Update `docs/API_EVENTS.md` and `docs/OBSERVATIONS.md`.
   - *Done when:* the endpoint tests for all three ops pass and the OpenAPI drift test reflects the new enum value.
8. **`active` hint.**
   - `stg_usa_wa__organizations`: add `active`.
   - `export_pm_tables.py`: add `organizations` (`id`, `active`, `archived_at`), the `pm.organizations` source and `PM_SRC_PM_ORGANIZATIONS`.
   - Add `tests/org_active_divergence.sql` (warn).
   - Update the comment on the `sources.yml` organizations entry.
   - *Done when:* the export column-order test passes and fixture tests show the hint firing on a null-biennium mismatch, staying silent on a committee mismatch, and staying silent on agreement.
9. **Docs, version, real-data check.**
   - Docs: `RUNBOOK_DESIRED_STATE`, `ADMIN_OVERLAY`, `MERGE`, the manifest header.
   - Bump 0.63.0 → 0.64.0 (pyproject, package.json, uv.lock client entry), then `scripts.regenerate_client`.
   - Extend `test_real_snapshot` to `org_lineage`.
   - Pull into a worktree-local store (`pull_datasets --root`), then build and dry-run (no `--execute`).
   - *Done when:* the vendored ship gate is green and the dry run reports 161 noop / 1 update (Civic Health → 2026) / 0 inserts / 0 archives / `clean`.
10. **Review and ship.**
    - CR rounds, then a PR whose body has no closing keyword.
    - Then, each separately authorized: merge, pull into main, and restart; pull datasets, then build.
    - A supervised execute then applies the update (the nightly unit is dry-only; #656 tracks the block ahead of it). Verify `event_year = 2026`, post the decisions on #592, and close it.

## Open questions / risks

- **Live API writers.** Before step 7 ships, check `api_request_log` for observation event writes of these three types on anchored orgs in the last 90 days. A live caller would start getting `REJECTED`. None is expected: usa-wa stopped writing through the API at the #501 cutover.
- **Step 9 dry run touches prod read-only.** It reads the prod DB through the applier's dry-run path and writes its ledger under the worktree's `data/applier/`, never main's. The worktree setup symlinks only `data/cannabis_observer`, so the ledger can't leak into the production streak. I'll confirm that path before running it.
- **Edit-row lock semantics.** If a curator edits an unlocked event's slug to `succeeded_by` between two anchored orgs, it is refused, which is intended. The flash has to say why, because the curator started from an unlocked row.
- **Nightly streak.** The Civic Health update changes the run's digest once, which resets the streak. No run executes on its own (the unit is dry-only), and the nightly has been blocked since 10-09 by assignment entries (#656): the update waits for that supervised execute.
