# Re-key the assignment crosswalk onto usa-wa's `span_key`: design

Part of #500 (delivery 1 of 2) under epic #490. Approved 2026-09-13.

## Goal

Make `producer_crosswalk.producer_id` the key the **dataset** uses for every
assignment anchor, so that #500's `retraction: archive` measures absence in the
published dataset's key space (usa-wa's "reading B") and never against an
anchor's Postgres ULID ("reading A").

## Why this is needed

The #499 applier measures absence as `scope.in_scope(entity) - present`: the
crosswalk `producer_id`s that no desired row names. For persons, organizations
and roles that is correct, because the dataset's `entity_id` *is* the anchor
id (roles: 312 / 312). For assignments it is not. The published `assignments`
dataset has no assignment-level id; its rows are identified by the structural
key `entity_id | role_key | span_kind | span_discriminator | span_start_biennium`,
and `entity_id` is the **person**. None of the 8,777 live assignment anchors'
`usa_wa_id`s appears in the dataset. As built, turning archive on for
assignments would retire the entire WA assignment slice on the first run.

usa-wa closed the gap on its side (#490 thread, 2026-09-13): catalog
`v20260913T033950Z-13c981`, schema 1.7.0, publishes `span_key` on both
`assignments` (8,395 rows, unique) and `pm_anchors`. Of 8,777 assignment
anchors, 8,393 carry a key and 384 are empty; an empty key means "this anchor
has no published assignment", and that is usa-wa's absence signal. No person,
organization or role anchor carries a key. `anchor_export` copies the published
key verbatim rather than re-deriving it, so the two sides cannot disagree on
its serialization.

## Approach

### Data model

`producer_crosswalk` gains `exported_producer_id TEXT`, beside
`exported_pm_id`. Each pair has the same shape: what the producer exported,
and what PM keys on.

| column | meaning |
|---|---|
| `exported_producer_id` | the anchor's `usa_wa_id` as exported. NULL only on rows the applier mints (no export, like the other export columns) |
| `producer_id` | the key the dataset uses, which is what the applier diffs on. `span_key` for keyed assignment anchors; the `usa_wa_id` everywhere else, including the 384 empty ones, which will then read as absent |

Migration, in `schema.sql` and idempotent:
- `ADD COLUMN IF NOT EXISTS`
- a one-time backfill, `exported_producer_id = producer_id` where it is NULL
  and `export_sha256` is set (every row on production: 12,427 rows from one
  export)
- a partial unique index on `(source, kind, exported_producer_id)` where it is
  not NULL

The existing uniqueness on `(source, kind, producer_id)` stays: one row per
dataset key. Resolution, the merge walk, `repoint_anchors`, the applier's
scope query and the overlay's scope check all read `pm_id` / `producer_id` and
are unchanged.

### Loader

- The header must be exactly `kind,usa_wa_id,pm_id,span_key`. The three-column
  header is **refused**: after the re-key, a keyless export would move every
  assignment key back to its ULID.
- `span_key` is allowed only on `kind=assignment`. It must have five
  `|`-separated fields with a ULID first (the person's registry id), and be
  unique in the file. Empty is allowed.
- `Anchor` gains `span_key: str | None` and `key`, which is `span_key` when
  present and `usa_wa_id` otherwise.

### Seed

- The upsert matches `(source, kind, exported_producer_id)` and sets
  `producer_id = anchor.key` beside the fields it already writes.
- The first run re-keys the 8,393 keyed rows in place (same row id). Re-running
  the same export changes nothing. A later export that moves an anchor's key
  updates that row in place.
- The report gains `rekeyed` (rows whose `producer_id` changed) and `unkeyed`
  (assignment anchors with no published row). The stale check compares on
  `exported_producer_id`. The existing blocking conditions (unresolved anchors,
  and two anchors landing on one PM row) are unchanged.
- Fail-safe edge: two anchors swapping keys between exports would collide on
  `(source, kind, producer_id)`. The unique constraint aborts the whole seed
  transaction, so nothing is half-applied. We accept that rather than
  pre-checking for it.

## Alternatives considered

- **Rewrite `producer_id` in place, no new column.** Smaller and no schema
  change, but once a row is keyed by `span_key` nothing ties it to its anchor.
  A later export that moves a key would insert a second row for the same PM
  assignment, putting it in scope twice.
- **Keep the ULID, add a `dataset_key` column.** The applier's generic absence
  check compares `producer_id`, so it would stay reading A for assignments
  unless the applier special-cased the kind, which is exactly what the
  manifest-driven design exists to avoid.

## Rollout

Each step that touches production waits for the user's go-ahead.

1. Ship: PR, merge, pull, restart (the restart applies the migration).
2. Pull `pm_anchors` `v20260913T033950Z-13c981` into the store by name (files
   only; the nightly pull does not subscribe to it).
3. Seed dry run against production. Expected: 8,393 re-keyed, 384 unkeyed, 2
   stale (a merged person and an archived assignment the export no longer
   carries), and unresolved and collision results as in the 2026-09-09 seed.
4. Supervised `--execute`.
5. Verify: 8,393 assignment rows keyed by `span_key`; `exported_producer_id`
   set on every row; an A/B dry run gives the same digest as the last nightly
   run; scope counts unchanged.
6. Tell usa-wa on #490 and usa-wa#314 that the re-key has landed, so the
   `pm_*` columns can go.

## Tests

- Loader: the four-column header is accepted and the three-column one refused
  with its reason; a key on a non-assignment row, a malformed key (field count,
  non-ULID first field) and a duplicate key are refused; an empty key on an
  assignment is accepted.
- Seed (integration): the first seed re-keys a ULID-seeded row in place (same
  id, `producer_id = span_key`, `exported_producer_id` = ULID); a re-run
  re-keys nothing; a key moving between two exports updates in place; an
  unkeyed anchor keeps its ULID and is counted; the stale check uses the
  exported id.
- Migration: a table without the column gains it with the backfill and the
  index, and the index refuses two rows with one exported id.
- End to end: the seed script on a directory shaped like a pulled snapshot
  (`data.csv` + `snapshot.json`) with keys.
- The applier suites pass unchanged; a minted create's exported id is NULL.

Docs: SCHEMA.md § Producer crosswalk (no net growth, #518) and the RUNBOOKS.md
seed section. Version 0.50.0.

## Out of scope

Delivery 2, which is the rest of #500: the role and assignment models,
`retraction: archive`, the archive threshold, #424 restore collisions. Also
#501's supervised execute.
