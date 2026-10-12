---
title: "#502 retirement sweep: the change feed, its scopes, the cutover seeds, the prune hold"
date: 2026-10-12
status: draft
---

# #502 retirement sweep

## Problem

The #490 cutover (#501) is done, so the change feed has no reason to exist. `/changes`, five `/subscriptions` routes, discovery, `min_seq`, the `subscriptions:write` scope and the subscription table are dead public surface. Two cutover-only seed scripts linger. The prune unit still carries the temporary 104-day hold from #495. And #502's two scope notes disagree about `entity_changes`: the #598 note says the table stays for merge tombstones; the #607 note says it retires with the feed.

## Audit (2026-10-12, prod read-only)

- **Feed callers: zero.** Last `/changes` request 2026-09-08 17:55 UTC, last `/subscriptions` 13:52 the same day. Every feed request in the log has `api_key_id = NULL`: the only caller's key is deleted. Nothing in the last 30 days. `api_key_entity_subscriptions` has 0 rows. No key holds `subscriptions:write`.
- **`entity_changes` readers: two.** `GET /changes` (`src/api/public/changes.py`) and the prune (`src/core/maintenance.py`). There are no other readers in `src/`, `scripts/` or infra.
- **`entity_changes` writers:** 7 row triggers (`trg_entity_changes_*`, incl. `_deleted` fired *from* `deleted_entities`), manual `INSERT`s in `src/core/ancillary_migrate.py` and two one-off scripts, and the `app.source_key_id` GUC that `stamped_transaction` sets (#491; 100% of the 42k retained rows have a NULL stamp).
- **Merge tombstones live in `deleted_entities`, not `entity_changes`.** Readers: `gone.py` (#607's 410), `merge_history.py`, `applier_pg.py`. Nothing in that table depends on `entity_changes`. The `merged_into` column on `entity_changes` (#467/#479) only ever reached consumers through `/changes`.
- **Admin coupling:** the dashboard and the request-activity page show "Changes polls / rows delivered". The org merge preview tells the curator that ids are "published to the change feed".
- **Seeds:** `seed_producer_crosswalk.py` is the only user of most of `src/core/ingestion/crosswalk.py`. Only `Supersession` and `repoint_anchors` have other users (the applier). `seed_roles.py` has no runtime user.
- **WA identifier types:** 11 `*_wa_*` types hold about 3.9k identifier rows, the newest written 2026-08-23. The import and migration scripts still name them.

**Conflict resolved: the #607 note is right.** The "internal duties" in #502 and in the design doc § Retirements mixed up the two tables. Once `/changes` is gone, `entity_changes` is write-only, so it retires with the feed. The drop is irreversible and touches ~37 test files that use outbox rows as their observable. It therefore goes in its **own sub-issue**, as #502 already asked ("file separately"). Both notes hold: the decision is retire, and the drop ships separately.

## Approach

Three PRs, in this order:

- **PR 1, under #502:** retire the feed surfaces and set retention per table. This changes the public schema.
- **PR 2, under #502:** retire the seeds and record the WA types as history. Small, and independent of PR 1.
- **PR 3, new sub-issue:** retire `entity_changes`. It starts only after PR 1 has run healthy in prod for a week, which is #502's own acceptance criterion.

Between PR 1 and PR 3 the outbox is still written and still pruned at 90 days, so nothing grows without bound.

## Tradeoffs / alternatives

- **One PR for everything:** rejected. The table drop is irreversible and test-heavy. Bundling it holds back the low-risk route removal and makes review harder.
- **Keep `entity_changes` as a forensic log:** rejected. It has no reader, and every write pays for it in triggers. Forensics already has `api_request_log`, the applier's run ledger and `deleted_entities`. Noted in case you'd rather keep it.
- **Drop the WA type rows, or remove their `INSERT`s from `schema.sql`:** rejected. About 3.9k identifiers reference these types by FK, and a schema-built test DB needs the rows. Retiring them "as contract" means comments and docs only.
- **Delete the prune unit, as #502 originally said:** rejected. The unit also bounds `api_request_log` and the bare-delete tombstones that #607's `404`/`410` contract depends on.

## Steps

**PR 1: feed surfaces + retention (#502)**

1. Delete the routes: `changes.py`, `subscriptions.py` (incl. `/discover`), their router lines and their schemas (`ChangesResponse`/`min_seq`, `Discovery*`, subscription models). Verified by the OpenAPI having no `/changes` or `/subscriptions` path.
2. Schema: `DROP TABLE IF EXISTS api_key_entity_subscriptions`. Remove its `CREATE`/index/`CHECK`-reconciliation blocks. Delete `subscriptions:write` from `api_key_scopes`, then from `api_key_scope_types`. Verified by a schema-apply test and by schema parity against `--test`.
3. Merge paths: remove `mirror_subscriptions`/`copy_subscriptions` and their three callers. Keep `record_merge_tombstones`. Remove the subscription re-home in `ancillary_migrate.py`. Retire `restore_467_committee_succession.py`, which has already run and imports `copy_subscriptions`.
4. Admin and middleware: drop the "Changes polls" stats (dashboard + activity), the `'changes'` route-group branch and the merge-preview feed sentence. Historical log rows keep `route_group='changes'` and age out by about 2026-12-07.
5. Prune: replace the single `--retention-days` with per-table windows (`entity_changes`, bare tombstones, `api_request_log`), each defaulting to 90 days and stated in one RUNBOOKS table. Drop the 104-day hold from `infra/power-map-prune.service`. The first run deletes about 1.5M request-log rows in committed batches.
6. Tests: delete the feed-only suites (`test_changes`, `test_subscriptions`, `test_discovery`, the subscription half of `test_merge_signals`, `test_restore_467_*`). Re-point the incidental references. Add a guard test that no retired path or scope appears in the OpenAPI.
7. Docs: delete `docs/CHANGE_FEED.md`. Edit `PUBLIC_API`, `OBSERVATIONS`, `CONVENTIONS`, `MERGE`, `ANCILLARY`, `SCHEMA`, `RUNBOOKS`, the `API_*` docs and AGENTS.md (the index line routing to CHANGE_FEED). The context gates must stay green.
8. Release: bump to 0.63.0 (`pyproject.toml` + `package.json`), `scripts.regenerate_client`, ship gate. After merge: pull to deploy, restart (which applies the schema), copy the prune unit and daemon-reload, push tag `v0.63.0`. Then watch Observo's key in `api_request_log`.

**PR 2: seeds + WA types (#502)**

9. Delete `seed_producer_crosswalk.py`, `seed_roles.py` and their tests. Delete the seed-only half of `crosswalk.py` (`Anchor`, `parse_anchors`, `verify_digest`, `resolve_anchor`, `load_anchors`, `SeedReport`…), keeping `Supersession` + `repoint_anchors`. Fix the RUNBOOKS/SCHEMA references. No version bump: there's no public-schema change.
10. Mark the WA identifier-type `INSERT` block in `schema.sql` and in the docs as **historical**: the rows stay, they're not a contract, and usa-wa's published crosswalk owns the scheme graph.

**PR 3: retire `entity_changes` (new sub-issue, after PR 1 + 1 week)**

11. Drop the 7 `trg_entity_changes_*` triggers, the manual inserts, the outbox prune leg, `stamped_transaction` + the GUC + the #491 sweep + its AGENTS.md rule, and the schema-parity expectations. Re-point the 37 test files to `updated_at`. Optionally export a CSV, then `DROP TABLE`. Keep the touch triggers: they bump `updated_at`, which drives the ETags (#392). Rename `prune_outbox.py` to `prune.py`.

## Open questions / risks

- **SDK consumers.** observo#671 and archiver#324 adopted `power-map-client`. Removing the feed modules breaks only code that calls them; both are recorded as read-only. Check their imports before tagging?
- **`restore_467_committee_succession.py`.** Retire it (recommended), or strip only its subscription step?
- **PR 3's export.** Archive a CSV of `entity_changes` (11 MB) before the drop, or drop outright?
- **Retention values.** All three windows default to 90 days, which matches PUBLIC_API's "~90 days" request-log statement and #607's bare-tombstone TTL. Do you want a different value for any of them?
