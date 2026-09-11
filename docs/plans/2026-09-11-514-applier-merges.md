---
title: "#514 applier acts on producer person-merge tombstones — build plan"
date: 2026-09-11
status: draft
design: 2026-09-10-diff-applier-design.md (§ Merges are report-only — this plan lifts it for persons)
---

# #514 applier acts on producer person-merge tombstones

## Problem

usa-wa published its first real merge tombstone (usa-wa#366 → power-map#515):
loser PM `01M0P4P2CYKHSKHRVT737TWNWA` (Dennis L. Heck) → survivor
`01KV6PQP8MME81ZKDEXG4QBQ18` (Denny Heck). #499 lists it as a `merge` entry and
never acts, and there is no `--allow-merges`, so it blocks every `--execute`:
#514 is on #501's critical path. Wiring the merge as written would fail in five
ways, all confirmed in code:

- `merge_person_into` lives in `src/api/admin/people_merge.py`; core cannot call a route module.
- A merge touches neither `producer_crosswalk` (no FK on `pm_id`) nor `curation_overlay`. The
  loser's crosswalk row keeps naming a deleted row, the mart re-emits the merge every night,
  and a second apply raises `PersonNotFoundError`.
- `_diff_merge` reads no live state, so the in-transaction re-diff cannot see a merge was done.
- Merge and row writes in one plan destroy `Denny Heck` silently. In either order the survivor
  ends with no `Denny Heck`, and the re-diff passes because that state matches the desired state.
  (Defused for this person by the 2026-09-11 retype to `preferred`; the class remains.)
- The loser is also reported as a `retract` (absent from `persons`), which would become permanent
  once its crosswalk row is `merged`.

## Approach

**Persons only.** Move the person merge primitive to `src/core/person_merge.py` and make it
re-home `curation_overlay` rows like any other ancillary row (the survivor's override wins a field
clash). It also gains a read-only `preview_person_merge` that shares its SQL predicates. The
manifest binds a merge table to a primitive (`target.primitive: person`); an unbound merge table
(organizations) stays report-only. `_diff_merge` classifies against live state:

- `noop` when the live crosswalk already resolves the loser's producer id to the survivor;
- an actionable `merge` carrying `effects` (the preview, in the digest) when loser and survivor
  are live, or when PM already merged the loser into the survivor and only the crosswalk lags;
- report-only `merge` for a null survivor or an unbound kind;
- `stale` otherwise.

A diff with actionable merges is in the **merge phase**: only the merges, stale and conflicts
thresholds decide its verdict (nothing else can be written in that run), and the other exceeded
thresholds are reported as deferred. An execute in the merge phase writes only merges: the
primitive, then re-point every crosswalk row naming the loser (and the conflict-dropped
assignments) to the survivor as `merged`. It re-diffs inside the transaction and commits only if
every merge it acted on is now `noop` and the re-diff has no entry the pre-write diff lacked. The
next night's diff, computed against the merged state, is what the row writes are approved
against. `--allow-merges N` opens the threshold, the same pattern as `--allow-creates`.

## Tradeoffs / alternatives

- **Re-point the crosswalk inside the shared primitive** (my review's first recommendation):
  rejected. An admin merge of two producer-distinct anchored persons would put two producer ids
  on one `pm_id`, and `desired_people.pm_id`'s `unique` test at error would halt the nightly build
  instead of the applier reporting `stale` — against the #497 rule that one bad row never halts
  the build. That case is finding 25's, held for #501. The applier re-points after its own merges
  only.
- **A `deleted_entities` trigger that re-points the crosswalk on every tombstone**: rejected for
  the same reason, and it is a schema change for a behaviour only one door needs today.
- **Dry-run preview by executing the primitive in a savepoint and rolling back**: exact, but the
  nightly dry run would write, lock rows and consume outbox ids. Rejected for a read-only preview
  sharing the primitive's predicates; the in-transaction verification catches drift between them.
- **Merge, then re-diff and write the rest in the same run**: rejected. The rows written would not
  be the ones the digest approved.
- **Organizations too**: rejected for now. There is no org tombstone to test against, and an org
  merge brings role absorption and role/assignment anchors (#500's territory). Split to a follow-up.

## Steps

1. **Extract** `merge_person_into` + `PersonNotFoundError` + its SQL constants to
   `src/core/person_merge.py`, verbatim. Repoint the admin route, the cleanup script and tests.
   The existing merge suites stay green unchanged.
2. **Overlay re-home**: `rehome_curation_overlay(db, entity_type, pairs)` in
   `src/core/ancillary_migrate.py`, called by the person merge (person pair + conflict-dropped
   assignments) and the org merge (org pair, absorbed roles and their dropped assignments).
   `merge_person_into` returns the conflict pairs.
3. **Preview**: `preview_person_merge(db, winner_id, loser_id)` returns the names moved or
   deduped (with the winner row), the assignments moved or conflict-dropped, identifiers moved,
   other ancillary counts and overlay rows. An integration test asserts preview == the actual
   effect on a fixture.
4. **Crosswalk re-point**: `repoint_anchors(db, kind, pairs)` in `crosswalk.py` (all sources,
   resolution `merged`, `exported_pm_id` kept).
5. **Engine**: manifest `target.primitive` (loader + `test_manifest` gate); `LiveStore` gains
   `tombstones()` and `merge_preview()` (pg + fake); `_diff_merge` classifies live; `Entry.effects`
   enters the digest and `diff.jsonl`; a merge loser is never a `retract`.
6. **Verdict + CLI**: merge phase in `verdict_for`; `--allow-merges N`; summary.md lists each
   merge's effects and the deferred counts.
7. **Writer**: merge-phase `apply_diff` (registry `person → merge_person_into`, injectable for the
   unit tier) with the ⊆ verification.
8. **Acceptance (db tier)**: the Heck shape — survivor canonical `preferred` `Denny Heck` with the
   `member_id` identifier; loser canonical legal `Dennis L. Heck` with the `roster` identifier and
   one party assignment; producer asserts legal `Dennis L. Heck`. After one execute: `Denny Heck`
   canonical, `Dennis L. Heck` held as a non-canonical legal name, both identifiers, the assignment
   on the survivor, loser crosswalk row `merged` → survivor. A second run is all-noop for the merge.
   A merge-phase execute writes no row entry.
9. **Docs + version**: `docs/RUNBOOK_DESIRED_STATE.md`, `docs/MERGE.md`, the manifest comment,
   the applier design doc's merges line; bump 0.47.2 → 0.48.0.
10. **Prod proof (read-only)**: a scratchpad dry run against prod shows exactly one actionable
    merge with its effects, and no Heck name entry.

## Open questions / risks

- The supervised execute on prod (`--allow-merges 1 --streak 1`) happens after merge + deploy
  approval and is not part of this PR.
- An admin merge of producer-distinct anchored persons still leaves the crosswalk stale (finding
  25). It stays with #501, called out on the PR.
