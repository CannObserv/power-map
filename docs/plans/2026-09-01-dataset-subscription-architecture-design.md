---
title: Dataset-subscription architecture — usa-wa → power-map
date: 2026-09-01
status: approved (amended 2026-09-08 — see § Review addendum)
---

# Dataset-subscription architecture — usa-wa → power-map

## Goal

Replace the bidirectional observation/change-feed sync between usa-wa and power-map with a
unidirectional, snapshot-based dataset subscription: usa-wa publishes versioned dataset
artifacts it alone defines; power-map pulls, maps, and applies them through its own layered
pipeline. Eliminates the dual-master feedback loop, moves WA domain knowledge out of
`src/core`, and reduces the cross-repo contract from a runtime protocol to a versioned
data schema.

## Why — findings from the history review

- **The original selection (P3, `2026-05-29-p3-observation-endpoint-design.md`) never named
  decoupling as a goal.** Its load-bearing property — "never let a robot overwrite a human" —
  is retracted by this design; it is also the direct cause of the WA leak into `src/core`
  (`role_title.py` synthesizes titles precisely so a producer "can never drift PM's curated
  form").
- **The real defect is dual-master replication.** Both systems hold a mutable copy of the same
  row with independent clocks. usa-wa's `docs/LWW-NOOP-GATE.md` documents the consequence: the
  change feed "carries what PM changed, never what we did"; identical re-observations no-op
  without moving PM's clock, so skew never resolves. Observed: 26,990 assignment deliveries in
  10 days (429'd PM); the inverse failure (#247) left 397 corrected spans invisible for six
  cycles. ≥11 usa-wa issues and ≥10 PM issues share this one root cause.
- **Coupling, quantified.** usa-wa: 698 Python files across four PM-facing packages (566 of
  them a generated client of PM's entire OpenAPI surface, admin routes included) vs 24 files
  for its own API. PM: 2,355-line `observation.py` (81 commits), 18 reason slugs, four
  hand-built ping-pong no-op gates, ~14 WA identifier types seeded in `schema.sql`.
- **Transport substitution fixes none of this.** PM's `entity_changes` outbox already *is* a
  replayable, offset-addressed, at-least-once log; a broker (Redis/Kafka) re-implements it on
  new infra without touching the payload semantics where the coupling lives. Direction and
  contract *kind* are the variables that matter: a runtime protocol (dispositions, reason
  slugs, retract verbs, clocks, delivery state) becomes a versioned static schema.
- **Feed semantics are unnecessary at this scale.** ~10⁴ rows nightly: full-snapshot transfer
  costs nothing, so "what changed since X?" — the only question a feed answers — never needs
  asking. The consumer diffs snapshots if it ever cares.
- **Prod request log (2026-09-01):** usa-wa is the *only* consumer `/changes` has ever had
  (66,152 reqs / 90 days; 12,559 subscription rows; no other key). Its reconcile loop makes
  ~49k requests/day to sync those ~10⁴ rows. Observo reads only.
- **PM started with the right shape.** `src/core/ingestion/sources/csv_*.py` — the pre-P3 CSV
  importer. P3's mistake was replacing the file with a conversation; this design automates the
  file.

## Approved architecture

```
usa-wa (producer; single-master for WA sources)
  raw/            per-run, as-fetched, immutable, replayable
  staging/        one cleaning regime per source (dbt-duckdb) — no cross-source joins
  conformed/      cross-source reconciliation: person_crosswalk, seats, assignments, …
  published/      /datasets/catalog.json + /datasets/<name>/<version>/ (immutable)
        │  static files off usa-wa's existing service; pull, nightly; either side may be down
        ▼
power-map (subscriber)
  raw/            pulled snapshot versions, verbatim (last-applied + N recent)
  mapping/        PM-side dbt-duckdb project: usa-wa schema → PM-shaped desired-state
                  tables, curation overlay joined declaratively
  applier         generic diff-applier: desired-state vs live Postgres → minimal
                  INSERT/UPDATE/archive, row- and column-scoped
  PM entities     normal SQL writes — triggers, audit, entity_changes fire as usual
```

usa-wa never reads PM. PM never writes usa-wa. The contract is the datapackage schema.

### Catalog & publication (usa-wa side; layout owned by usa-wa)

- Two tiers in one catalog: **staging** datasets (one per source adapter — e.g. the historical
  WSL roster PDF) and **conformed** products (persons, orgs, roles/seats, assignments,
  person_crosswalk). Catalog entries carry name, layer, `derived_from` sources, latest
  version, schema URL, content hashes.
- Format: Frictionless `datapackage.json` per dataset-version under an immutable versioned
  directory; thin top-level `catalog.json`. **Generated from the dbt manifest/exposures** — a
  build artifact, never hand-maintained. (DCAT is the interop path if catalogs ever go
  public-facing; mechanical mapping later.)
- The reconciliation crosswalk (WSL member id ↔ roster `name:year` id ↔ usa-wa ULID) is a
  **first-class published dataset** — inspectable and diffable between runs, replacing
  implicit sync-engine state.
- PM's "subscription" = a config list of dataset names + pinned schema major version. Default:
  conformed products only. Staging datasets are the triage/lineage surface and the escape
  hatch for source-granular consumption — no per-consumer logic in usa-wa, ever.

### PM-side pipeline (this repo)

1. **Puller** — fetch `catalog.json`; skip when content hash unchanged; store snapshot
   verbatim under a versioned local directory; prune to last-applied + N.
2. **Mapping models** (dbt-duckdb, under the ingestion tree) — the *only* place WA/usa-wa
   knowledge lives in PM. usa-wa ontology → PM ontology here (spans → roles+assignments,
   crosswalk → identifier rows); non-1:1 logic is expected and lives in SQL, testable per
   layer. `role_title.py` moves here or becomes overlay defaults; `src/core` goes WA-free.
3. **Curation overlay** — table `(entity_type, entity_id, field, value, note, created_by)`
   joined declaratively in the mapping models (`COALESCE(overlay.value, mapped.value)`).
   Admin edits to producer-owned fields write overlay rows (UI says so); everything else
   remains direct curation. PM state for the slice = f(snapshot, overlay) — idempotent,
   replayable, and the desired state is a diffable artifact *before any write*.
4. **Diff-applier** — generic, schema-driven, domain-free Python. Diffs desired-state vs live
   Postgres and issues minimal writes. **Row-scoped** (only usa-wa-owned rows — scope key is
   **crosswalk membership**, not `source_key_id`; see addendum gap A) and **column-scoped**
   (only columns the dataset asserts; curator-only columns never enter the diff). Dry-run by
   default, `--execute` gated (house rule #402), abort thresholds (see Transition).
5. **Retraction = absence**, per-dataset policy: assignments/roles auto-archive when absent
   from the snapshot; persons/orgs report-only (absence from WA data ≠ nonexistence; rows may
   be claimed by other producers/curators). Kills `op="retract"`, anti-resurrection, and
   `attached_archived` for this producer.
6. **Identifiers** — one per entity kind: `usa_wa_person` / `usa_wa_organization` = usa-wa
   ULIDs. The ~14 WA scheme types stop being contract (rows retained as historical data); the
   scheme graph lives in usa-wa's published crosswalk. Roles and assignments get **no**
   public identifier under this collapse — their per-row producer handle lives in the
   crosswalk table instead (addendum gap B), which is what makes archive-on-absence
   addressable for the only two datasets that auto-archive.

### Retirements

- usa-wa: `clearinghouse-sync-powermap` (28), `usa-wa-sync-powermap` (69), `powermap-client`
  (566) — **663 files across three packages**, not the 698 first counted: `clearinghouse-core`
  (35) is usa-wa's own Layer-1 framework and shrinks rather than dying. Plus the sync outbox,
  redrive, `/health/sync`, anchor columns, and the entire LWW-NOOP-GATE apparatus
  (#65/#85/#102/#104/#109/#112/#132/#160/#247 machinery).
- power-map, post-cutover: `/changes`, all five `/subscriptions` routes, discovery, the
  outbox prune timer, `min_seq` — usa-wa is their only consumer in the feature's life
  (request-log verified). The `entity_changes` *table* has internal duties (merge tombstones,
  #467/#479) — separate audit before any drop; own follow-up issue.
- **Kept:** `POST /observations` and its semantics for other producers (Observo); the admin
  dashboard and merge workflow (still the editorial surface); exact-match identity; no review
  queue.

### Tooling (right-sized: ~10⁴ rows, nightly, single VMs, Python)

| Need | Tool |
|---|---|
| Layered transforms, tests, lineage (both repos) | dbt-core + dbt-duckdb; every layer materializes as CSV/Parquet |
| Publication contract | Frictionless datapackage + catalog.json, generated from dbt |
| PM-side validation | Pydantic models materialized from the datapackage schema |
| Fuzzy reconciliation tail (roster names vs WSL) | Splink (optional, usa-wa side) |
| Orchestration | existing systemd timers + run ledgers — no Dagster/Airflow |
| Explicitly not | Kafka/Redis, Airbyte/Meltano, lakeFS/Iceberg, Great Expectations |

## Transition plan (safeguards are the design)

| # | Safeguard | Guards against |
|---|---|---|
| 1 | Crosswalk seed through merge history: export usa-wa anchors; resolve each PM ULID through `merged_into` chains to the live survivor; unresolvable → blocking report | duplicate minting from stale anchors |
| 2 | Dry-run gate with abort thresholds: report-only until creates ≈ 0 on anchored cohorts, unexpected archives = 0, N consecutive clean runs | mass-mint / mass-archive |
| 3 | One supervised triage pass: the first dry-run diff is the complete "PM disagrees with producer" audit; each diff resolved once — producer-wins (apply) or PM-wins (overlay row) | silently reverting curator corrections |
| 4 | Column-scoped writes | loss of curator enrichment on producer-owned rows |
| 5 | Row scope + per-dataset retraction policy | non-usa-wa data loss |
| 6 | Single-writer freeze: revoke usa-wa write scopes before first `--execute` — **already true in fact since 2026-08-27**, formalise it early (addendum) | dual-writer races |
| 7 | Archived-anchor sweep (#481 / usa-wa#288) resolved in the triage pass | writing onto soft-deleted rows |
| 8 | Pre-cutover DB backup; schema-parity timer green throughout | everything else |

Sequence: ~~(0, optional bridge) `source_key_id` on `entity_changes` rows to stop the
churn while building~~ — shipped as #491 but overtaken: the churn ended when the producer
stopped writing. (1) usa-wa publishes datasets in parallel with the old sync — **done**.
(2) Crosswalk seed. (3) PM applier dry-runs until clean; triage pass. (4) Freeze usa-wa
writes; flip to execute. (5) Delete sync packages; retire `/changes` surface.

The freeze moved to the front: it costs nothing now (see addendum) and the triage pass needs
a still producer anyway.

## Review addendum — 2026-09-08 (producer side deployed)

usa-wa's epic (usa-wa#302) is built and deployed: #303–#313 closed, catalog live,
`/api/v1` flipped to the serving tier, its deployment now registers no mutating route
(usa-wa#349). Only usa-wa#314 (cutover + deletion sweep) is open, gated on this repo. PM-side
implementation has not started.

### The sync is already one-way dead

Measured on prod (`api_request_log`, usa-wa key `01KV6T7RTS5PVF1HB94T5X23HY`), 7 days to
2026-09-08:

| Signal | Count |
|---|---|
| Reads (`/assignments/{id}` 88,619 in 3d, `/people/{id}`, …) | 254,653 |
| `/changes` polls | 7,405 (2,707 of 3,167 empty over 3d; 45,586 rows delivered) |
| **Writes (observations)** | **4** — one weekly org re-observation batch, all `auto-attached` no-ops |

Last substantive write: an assignment observation on **2026-08-27**; `entity_changes` has not
grown since **2026-08-29**, yet the feed re-delivered 45,586 rows in three days. Two
consequences the design predates:

- **Safeguard 6 has already happened in fact.** PM's WA slice is frozen as of 2026-08-27 and
  going stale. Staleness, not sync churn, is the clock on this work.
- **What remains is pure cost** — ~36k reads/day and 1,270 polls/day replaying a dead outbox.
  Stop and mask the sidecar and revoke the key's write scopes now, ahead of the applier: it
  decouples usa-wa#314's freeze step from PM readiness and fixes the "as-of" baseline the
  triage pass compares against. usa-wa#315 (adopt #491 echo suppression) is overtaken.

### Gap A — row scope has no key

The applier cannot scope by `source_key_id`. On prod: `people` and `roles` have no such
column; `organizations` has one but only **4 of 1,666** rows carry it (the #334 reparenting
gate); `role_assignments` has **4,420 stamped against 8,812 assignment anchors** usa-wa
exported — about half of producer-owned assignments predate stamping (seed/backfill rows).
Row scope is therefore **membership in the seeded crosswalk**, and `source_key_id` stays what
it is elsewhere in the schema.

### Gap B — assignments and roles lose their handle

The identifier collapse leaves the two auto-archiving datasets with no stable per-row
producer key. Decision: persist usa-wa's `anchors.csv` as a PM-side crosswalk table under the
ingestion tree, keyed `(kind, usa_wa_id, pm_id)`, and let the applier join on it;
`identifiers` keeps `usa_wa_person` / `usa_wa_organization` as public-facing identity only.

### Gap C — seed sizing: persons clean, assignments are the work

Against the 2026-09-03 export (person 3,118 · organization 219 · role 312 · assignment 8,812):

| Kind | Producer anchors | PM side | Note |
|---|---|---|---|
| person | 3,118 | **3,118** distinct people carrying `person_wa_legislature_roster` (2,477) + `member_id` (641) | exact match; **0 archived** — safeguard 7 / #481 is likely a non-issue here |
| organization | 219 | 211 with `org_wa_legislature*` | 8 to resolve in triage |
| assignment | 8,812 | 11,787 total RAs, 4,420 stamped | the dominant triage surface; size the pass for it |

### Gap D — the catalog is not reachable from PM

`GET https://usa-wa.exe.xyz:8000/datasets/catalog.json` returns an exe.dev login redirect —
the proxy is private, so the puller cannot fetch it as designed. Decide the transport before
step 1: `ssh exe.dev share set-public` on that VM's port (publishes the whole app), an
exe.dev-authenticated fetch, or SSH/rsync VM-to-VM. Integrity is transport-independent — the
catalog entries carry sha256.

### Gap E — crosswalk tombstones

`person_crosswalk` carries `merged_into` tombstones, and with persons on a report-only
retraction policy they are the *only* signal the mapping layer gets to re-point a merged-away
`usa_wa_person`'s assignments at the survivor. Load-bearing for the mapping models; not
optional.

### Also

#481 folds into the triage pass. #485 / #486 were written against usa-wa's write path and now
scope to Observo only — retitle or they will be worked at the wrong priority.

## Out of scope

- Observo and other producers (keep `/observations` unchanged).
- Any broker; any event/feed mechanism on either side.
- Fuzzy identity matching in PM (#167 stays open).
- Multi-jurisdiction generalization beyond keeping all usa-wa knowledge inside the mapping
  models.
- Dropping the `entity_changes` table (separate internal-consumer audit first).
