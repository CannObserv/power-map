---
title: "#500 delivery 1: re-key the assignment crosswalk onto usa-wa's span_key"
date: 2026-09-14
status: executed
---

# #500 delivery 1: re-key the assignment crosswalk onto `span_key`

Design: `docs/plans/2026-09-13-crosswalk-span-key-design.md` (approved 2026-09-13).

## Problem

The crosswalk keys every assignment anchor on usa-wa's Postgres ULID, which the
published `assignments` dataset never carries: 0 of the 8,777 live anchors
appear in it. The applier's absence check compares crosswalk `producer_id`s
with desired rows, so once #500 turns on `retraction: archive`, every
anchored WA assignment would read as absent. usa-wa now publishes `span_key`
on `pm_anchors` (catalog `v20260913T033950Z-13c981`), but the loader refuses
that file's four-column header, and the seed's upsert on `producer_id` would
insert a second row beside each existing one rather than moving its key.

## Approach

Add `producer_crosswalk.exported_producer_id` (the anchor's `usa_wa_id` as
exported, backfilled from `producer_id`, partial unique index). The loader
takes exactly `kind,usa_wa_id,pm_id,span_key` and refuses the old header. The
seed matches rows on `exported_producer_id` and sets `producer_id` to
`span_key`, or to `usa_wa_id` when there is none. That re-keys 8,393 rows in
place and leaves 384 empty-key anchors on their ULID, where they read as
absent. It is idempotent, and a key that moves updates in place. The report
gains `rekeyed` / `unkeyed`.

## Tradeoffs / alternatives

- **Rewrite `producer_id` in place, no column**: rejected because once re-keyed
  nothing ties a row to its anchor, so a key that moves between exports inserts
  a second row for the same PM assignment.
- **Keep the ULID, add `dataset_key`**: rejected because the generic applier
  would have to special-case the assignment kind to stop behaving like reading A.

## Steps

Test first, one commit per step, `#500` in each message.

1. **Schema.** Add `exported_producer_id TEXT` inline and via
   `ADD COLUMN IF NOT EXISTS`; backfill from `producer_id` where it is NULL and
   `export_sha256` is set; add the partial unique index
   `uq_producer_crosswalk_exported` on `(source, kind, exported_producer_id)`
   where it is not NULL. Tests: `test_schema.py` (the index refuses a duplicate
   exported id; NULL is free), `test_schema_constraint_migrations.py` (a table
   without the column gains it, backfilled, with the index). One SCHEMA.md
   clause, at no net size.
2. **Loader.** `ANCHOR_HEADER` becomes `kind,usa_wa_id,pm_id,span_key`, and the
   three-column header is refused, naming the reason. `Anchor` gains
   `span_key: str | None` and `key`. A key is allowed on assignments only, must
   have five `|`-separated fields with a ULID first, and must be unique in the
   file; empty is allowed. Update `test_crosswalk_parse.py` and every fixture
   that writes an anchor CSV.
3. **Seed.** `_UPSERT_SQL` conflicts on `(source, kind, exported_producer_id)`
   and sets `producer_id = EXCLUDED.producer_id`. `load_anchors` reads each
   row's current `producer_id` once (dry runs too) and counts `rekeyed` and
   `unkeyed` on `SeedReport`. The stale check compares exported ids and ignores
   rows the applier minted. `_log_report` prints the new counts. Integration
   tests in `test_crosswalk_load.py`: re-key in place (same id), re-run re-keys
   nothing, a key move updates in place, unkeyed keeps its ULID, and the stale
   check uses the exported id.
4. **End to end.** `test_seed_producer_crosswalk.py`: the script, run on a
   directory shaped like a pulled snapshot (`data.csv` + `snapshot.json`,
   four columns, keyed and unkeyed rows), lands the expected rows. Applier: an
   existing create test asserts the minted crosswalk row's
   `exported_producer_id` is NULL.
5. **Docs and version.** RUNBOOKS.md seed section: re-seed from the catalog's
   `pm_anchors`, the four-column contract, and the `rekeyed` / `unkeyed` /
   stale lines. Version 0.50.0 (pyproject, package.json, uv.lock). Plan status
   set to executed.
6. **Gates.** Pre-ship green, then the integration tier on its own. No UI
   changes, so the browser tier is not needed.

The rollout (the design doc's § Rollout) follows after review and each step
waits for a go-ahead: ship, then pull `pm_anchors`, the seed dry run, the
supervised `--execute`, the verification (counts and an unchanged digest), and
finally telling usa-wa.

## Open questions / risks

- **GitHub API access:** power-map's `GH_TOKEN` and the `gh` CLI token are
  invalid (HTTP 401) as of 2026-09-13. Building and pushing are fine, but the
  sub-issue, the PR and the merge wait on a refreshed token. (Refreshed
  2026-09-14; sub-issue #525 opened and linked under #500.)
- **Dry run before deploy:** a seed dry run needs the new column, so it cannot
  run against production until after the restart. The rollout order handles
  this.
- **Future key collision:** after delivery 2, an applier-minted assignment row
  and a re-seeded anchor could claim one `span_key`. The seed would abort
  loudly on `(source, kind, producer_id)`. There are no seeds after cutover, so
  this is accepted.
- **Stale rows:** the 2 rows the new export no longer carries (a merged person
  and an archived assignment) are reported, not deleted. Retiring them is
  #501's triage.
