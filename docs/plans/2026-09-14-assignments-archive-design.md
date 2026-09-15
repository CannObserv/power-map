# The assignments model and retraction by archive: design

Part of #500 (delivery 2, PR A) under epic #490. Approved 2026-09-14. Delivery 1
(#525) re-keyed the assignment crosswalk onto usa-wa's `span_key`; this builds
the model that uses it.

## Goal

Apply usa-wa's published `assignments` dataset to PM's `role_assignments`:
- create what PM lacks;
- keep each span's dates;
- archive a span the snapshot no longer carries;
- restore one the applier archived when it comes back.

Nothing is written until #501's supervised pass. This PR makes the nightly dry
run report the assignment diff and gives `--execute` a gated path.

## Sizing (production, read-only, 2026-09-14)

- **Anchored:** 8,395 published assignments; 8,393 of them anchored.
- **Creates (2):** new House seat spans whose person and role are both
  anchored. One has an archived, unanchored PM row on the same
  (person, role, start).
- **Date differences:** 349 `end_date`, 19 `start_date`, 14 `is_current`.
  - Most are usa-wa#289's collapsed party spans: the published span stays open
    where PM's first segment ended.
  - The `start_date` ones are precise dates the producer now has where PM
    holds 1 January.
- **Archives (384):** in-scope anchors absent from the dataset (371 closed, 13
  current). 380 of them still have a published span on the same person and
  role; those are the supersession pairs.
- **Restores:** no archived assignment has a live twin today, so no restore can
  collide yet.
- **Dataset rule:** `is_active` is exactly "no `valid_to`" (773 open spans,
  7,622 closed), and every row has a `valid_from`.
- **Roles:** 312 of 312 anchored, all matching. That is PR B, where roles
  reuse this machinery.

## Decisions

| Question | Decision | Why |
|---|---|---|
| Split | Assignments first (PR A), then roles (PR B) | Assignments exercise every new applier feature, and roles' first diff is all no-ops |
| Restore | Only what the applier archived | A curator's or the duplicate audit's archive is never undone without a person |
| Dates | Owned and pinnable, in PR A | Archiving #289's later segments is only correct if the first segment's end is extended too; unowned dates would wrongly end about 349 party memberships |
| Shape | Extend the manifest's entity shape | The applier stays generic and driven by the manifest |
| PM-archived in-scope row | A non-blocking report; its dates are skipped | `stale` would refuse every plan, and #311 alone has 639 pending audit archives |

## Approach

### Applier

With `retraction: archive`, an entity table classifies its rows:

| Case | Entry |
|---|---|
| Anchored, PM row live | `noop`: the identity never changes, and a new identity is a new key |
| Anchored, PM row archived, crosswalk `retracted_at` set | `restore`, or `conflict` when a live row holds its `unique_live` tuple (#424) |
| Anchored, PM row archived by PM (no `retracted_at`) | a non-blocking report; the row's date updates are skipped |
| In-scope anchor absent from the snapshot, PM row live | `archive` (merged-away anchors are excluded, as in the report path) |
| Absent anchor, PM row already archived | `noop` |
| Unanchored desired row | `create` carrying `identity`: a `conflict` if a live row holds its tuple, with a hint if an archived one does |

- **Write order:** archives, then restores, then creates with their crosswalk
  rows, then children, then columns. Archives go first because they free the
  index slots that a re-segmented create or a restore may need.
  - An archive sets `archived_at = now()` and stamps
    `producer_crosswalk.retracted_at`.
  - A restore clears both.
  - The re-diff inside the transaction still gates the commit.
- **Collision checks at diff time:** `LiveStore.live_holders(table, columns,
  tuples)` finds live rows holding a `unique_live` tuple, with NULLs matching
  as the index does.
  - It covers creates, restores, and any column update that changes a tuple
    column. The dates table's `start_date` is checked against its entity
    table's `unique_live`.
  - A holder that the same plan archives does not count.
- **Gates:** new thresholds `archives: 0` and `restores: 0`, raised for one run
  by `--allow-archives N` and `--allow-restores N` (the `--allow-creates` idiom), as promised on #490. Both kinds
  enter the digest, so the streak ledger counts them.
- **Previews in an `archive` entry's `effects`:**
  - the #301 `staff_of` edges that archive with it (a restore does not bring
    them back, and the report says so);
  - `superseded_by`: the published spans on the same `supersession` tuple.
- **Identity references:** the model carries `person_producer_id` and
  `role_producer_id`, not PM ids. The applier resolves each from the live
  crosswalk, or to an id minted in the same run (as children of a created
  parent do today), so a new legislator's person and assignment can be created
  in one run. A reference that is neither is `stale`.

### Schema

`producer_crosswalk.retracted_at TIMESTAMPTZ`, added only if missing and NULL
by default. It carries one SCHEMA.md clause, with no net growth (#518).

### Models

- **Staging:** `stg_usa_wa__assignments`, plus `stg_usa_wa__roles`, used only
  to map `role_key` to a role `entity_id`.
- **`desired_role_assignments`:** keyed by `span_key`, with its `pm_id` from
  the crosswalk export. It carries the person and role producer ids, and
  `start_date = valid_from`.
- **`desired_role_assignment_dates`:**
  - Mapped values: `start_date = valid_from`, `end_date = valid_to`,
    `is_current = (valid_to IS NULL)`.
  - Each field comes from its pin when one exists, and the model keeps the pair
    legal: a pinned `is_current = true` clears `end_date`, and a pinned
    `end_date` sets `is_current = false`.
  - A dbt test holds `chk_current_no_end_date`.

### Manifest

```yaml
thresholds: {creates: 0, merges: 0, conflicts: 0, stale: 0, archives: 0, restores: 0, updates: null}
desired_role_assignments:
  entity: assignment
  key: [producer_id]                 # the published span_key
  pm_key: pm_id
  retraction: archive
  owned_columns: []
  target:
    shape: entity
    table: role_assignments
    identity:                        # written once, at create
      person_producer_id: {column: person_id, entity: person}
      role_producer_id: {column: role_id, entity: role}
      start_date: start_date
    unique_live: [person_id, role_id, start_date]   # uq_role_assignment_person_role_start
    supersession: [person_id, role_id]
desired_role_assignment_dates:
  entity: assignment
  key: [producer_id]
  pm_key: pm_id
  retraction: none
  owned_columns: [start_date, end_date, is_current]
  overlay: {start_date: start_date, end_date: end_date, is_current: is_current}
  target:
    shape: column
    table: role_assignments
    columns: {start_date: start_date, end_date: end_date, is_current: is_current}
    asserts_null: [end_date]         # null here clears: an open span reopens
```

The column shape gains two things, and neither changes an existing table:
- **`asserts_null`:** for the columns it names, a null means "clear it", not
  "no claim" (CR 5's rule stays the default).
- **An `overlay` map:** one binding covers three slots, so each row gets a
  single UPDATE. Separate UPDATEs could break the CHECK partway through.

### Admin pins (#498)

- **Slots:** three, `assignment: start_date | end_date | is_current`, shown as
  slot lines on the assignment's date and current edit form.
- **`tracked()`:** added to the four routes that write those columns:
  - the `people_assignments` inline edit;
  - the `roles_assignments_inline` edit;
  - `role_assignments`' `is_current` endpoint;
  - `role_assignments`' dates endpoint.
- **Sweep:** the #498 sweep's regex gains `UPDATE role_assignments …
  start_date|end_date|is_current`.
- **Drift test:** the test holding the slot registry, manifest and dbt
  vocabulary together learns the map form.

## Alternatives considered

- **An assignment primitive in core** (like #514's merge primitive). It puts
  domain rules beside `observation.py`, but it makes two write paths, and a dry
  run would have to predict what the primitive will do.
- **Computing absence in the models.** It needs no new entry kinds, but it
  moves the retraction policy out of the manifest, and the models cannot see
  live twins because they never open the database.

## Tests

- **Unit, with fakes:**
  - every row in the classification table;
  - restore and its #424 conflict;
  - identity references (anchored, minted in the same run, or neither);
  - a holder freed by a same-plan archive;
  - start-date update collisions;
  - `asserts_null` and the overlay map;
  - the new thresholds and flags;
  - supersession pairing;
  - the new kinds in the digest.
- **Integration:**
  - an archive stamps `retracted_at` and archives its `staff_of` edges;
  - a restore clears the stamp;
  - a collision surfaces as a `conflict` and never as a unique violation;
  - usa-wa#289's shape end to end: archive the later segment and reopen the
    first in one transaction, with the CHECK respected.
- **dbt fixture tests:** the CHECK pair, `span_key` uniqueness, and pins.
- **Admin:** the slot lines, `tracked()` on the four routes, and the extended
  sweep. The browser tier covers the new lines and is run on its own.

## Rollout

- **Ship:** the restart adds `retracted_at`.
- **Nightly:** it then dry-runs the assignment diff:
  - `desired_role_assignments`: about 8,393 no-ops, 2 creates and 384
    archives;
  - `desired_role_assignment_dates`: about 380 updates.

  It stays blocked, with no writes.
- **First execute:** the first archiving `--execute` is #501's supervised
  pass, sized from that dry run.
- **Docs:**
  - RUNBOOK_DESIRED_STATE.md: archive, restore, and the flags;
  - SCHEMA.md;
  - ADMIN_OVERLAY.md: the assignment slots;
  - an OBSERVATIONS.md note that `op="retract"`, anti-resurrection and
    `attached_archived` retire for usa-wa when usa-wa#314 removes its sync
    stack. They stay for other producers, so no code changes.
- **Version:** 0.51.0.

## Out of scope

- **PR B, the roles model.** `desired_roles` with `retraction: archive`. Its
  owned columns and role creates, which need PM's role guards (#266, #273,
  #302), are decided then.
- **#501's supervised execute**, including the 17 person creates.
- **Any change to the observation API.**
