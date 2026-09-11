# Runbook — the desired state: build it, apply it (#497, #499)

Moved out of `docs/RUNBOOKS.md` for its token budget (the #444 rule: a section
that grows a doc past 10k moves whole, it is not shortened). The two steps of
the #490 PM-side chain after the puller (#496): the mapping project's build and
the diff-applier's dry run / execute. `docs/COMMANDS.md` § Scheduled timers
names the nightly unit that runs both.

---

## Build the desired state (mapping project, #497)


The dbt-duckdb project at `src/core/ingestion/mapping/` turns usa-wa's snapshots
into PM-shaped **desired-state** tables — the diffable artifact the applier
(#499) reads. It is the only place usa-wa's ontology lives in PM
(`tests/test_src_core_wa_free.py` keeps the rest of `src/core` free of it).

Three steps, each file-to-file except the export:

```bash
uv run "${env_args[@]}" python -m scripts.pull_datasets                       # 1. snapshots (#496)
uv run --group mapping "${env_args[@]}" python -m scripts.export_pm_tables    # 2. producer_crosswalk + curation_overlay → _pm/*.parquet (read-only; DSN echoed)
uv run --group mapping "${env_args[@]}" python -m scripts.build_desired_state # 3. dbt build → data/desired_state/*.parquet
```

- **Models never open a database.** PM's two tables cross the seam as Parquet
  (step 2), so `dbt build` is hermetic and its tests run in the unit tier on
  fixtures. Neither step 2 nor 3 carries `--execute`: nothing writes a database.
- **`manifest.yml` is the contract #499 reads** — per table: key, retraction
  policy (`none` / `report` / `archive`), owned columns, and for events the
  owned types. `desired_entity_events` owns `dissolved` only; the 315 other org
  events in PM have no producer column and are never diffed.
- **Persons:** identity plus one legal name; pronouns, notes and every non-legal
  name stay PM's. **Organizations:** identity, legal name
  (`coalesce(long_name, name)` — no dba), acronym, a row-scoped parent claim
  (House/Senate/Joint/chambers only), and `dissolved` from `last_biennium`
  (year = first year + 1; none at the dataset's newest biennium).
- **The overlay wins by presence.** A `curation_overlay` row overrides the
  mapped value even when its value is null (the row then drops out). Its
  vocabulary is enforced per entity type by the project's `overlay_field_unmapped`
  test, so a row naming a field no model maps for its type is warned by name
  and applied nowhere. #498 builds the write path.
- **Five persons are published with a blank name** (usa-wa#364). Staging trims
  and nullifies; the build **warns** rather than halts; identity lands and no
  name is asserted, so PM's own legal name stands.
- **Every warning is a named no-claim, never a halt** — one bad row, producer's
  or curator's, costs only its own claim. `not_null_stg_usa_wa__persons_name_full`
  / `…organizations_name`: blank name, identity lands, no name asserted.
  `accepted_values_stg_usa_wa__organizations_org_type…`: an org_type the
  vocabulary has not met, no parent determined. `malformed_bienniums`: not
  `YYYY-YY`, dropped, no `dissolved` claimed. `unresolved_org_parents`: a
  determined parent PM cannot resolve, or its chamber/legislature anchor missing
  from the dataset, no claim. `not_null_desired_*_merges_survivor_pm_id`: a
  tombstone whose survivor is out of scope, report, never act.
  `overlay_field_unmapped`: a curator row naming a field no model maps, applied
  nowhere. `build_desired_state` prints each WARN node and exits 0.
- **The real-snapshot check** (`tests/core/ingestion/mapping/test_real_snapshot.py`,
  `-m integration`) asserts the design doc's measurements against the landed
  store and skips by name when steps 1–2 have not been run in that checkout.

Design and measurements: `docs/plans/2026-09-10-mapping-project-design.md`.

---

## Apply the desired state (the diff-applier, #499)


`scripts/apply_desired_state.py` diffs `data/desired_state/` against live
Postgres and, by default, only reports. It is generic and domain-free: every
PM table and column it touches is named by `manifest.yml`'s `target` bindings
(four shapes: `entity`, `column`, `child`, `merge`), never by the Python. Row
scope is the **live** `producer_crosswalk` (`resolution IN ('live','merged')`,
`source = PRODUCER_SOURCE` — the seed's value, one constant), read at run time —
never the `pm_id` a desired row carries. A run that reports every anchored row
`stale` with "no crosswalk row" is that constant disagreeing with the table.

```bash
# The nightly chain does exactly this at 09:30 UTC (power-map-desired-state.timer)
uv run --group mapping "${env_args[@]}" python -m scripts.export_pm_tables
uv run --group mapping "${env_args[@]}" python -m scripts.build_desired_state
uv run --group mapping "${env_args[@]}" python -m scripts.apply_desired_state        # dry run
uv run --group mapping "${env_args[@]}" python -m scripts.apply_desired_state --execute
```

Every run writes `data/applier/<run-id>/` — `diff.jsonl` (one actionable entry
per line, sorted by `entry_id`, never a noop), `summary.json`, `summary.md` —
and appends one line to `data/applier/ledger.jsonl`. The run's provenance is
`BUILD.json` from the build, copied into the summary.

- **Entry kinds.** `create` (an entity PM lacks; carries a hint naming PM rows
  already holding the asserted name — a probable twin), `insert` (a child row),
  `update` (owned columns differ), `retract` (an in-scope row the snapshot
  dropped — report-only), `stale` (the desired state disagrees with the live
  crosswalk — rebuild), `conflict` (more than one live row matches a keyed
  child — a person decides), `merge` (a producer tombstone — acted on when the
  manifest binds a merge primitive, i.e. persons; report-only for organizations
  until #520). A producer id a tombstone accounts for is never also a `retract`.
- **Merges (#514).** A tombstone is `noop` once the live crosswalk resolves the
  loser's producer id to the survivor. Otherwise it is an actionable `merge`
  whose `effects` carry `person_merge.preview_person_merge` — each loser name
  `move`/`dedup`, each assignment `move`/`drop`, the identifiers that move, all
  by id (an unmerge is a script over these ids; the merge itself is one-way).
  Or it is `already_merged` when a curator folded the pair first and only the
  anchors lag, or `stale` when the survivor or loser is missing or archived —
  or when the loser's live anchor no longer names the row the build exported
  (rebuild, as for any drifted row).
  **A diff holding an actionable merge is a merge phase:** only the merges,
  conflicts and stale thresholds decide its verdict, every other count is
  reported as *deferred*, and its execute folds the merges through
  `merge_person_into` — the admin's own merge — then re-points every anchor
  naming a retired row (`repoint_anchors`: the loser, and each assignment
  dropped as a duplicate), and writes **no row entry**. Those were computed
  against the pre-merge state and wait for the next diff. It commits only if
  every merge it acted on is a noop in the re-diff and nothing new appeared; a
  merge may only make entries go away. The Heck tombstone (#515) is the case
  that proves it: its merge and a pending name write, in one plan, destroyed
  the canonical name in either order and passed the ordinary re-diff check.
  Open a merge phase with one dry run and the execute it opens:

  ```bash
  uv run --group mapping "${env_args[@]}" python -m scripts.apply_desired_state --allow-merges 1 --streak 1
  uv run --group mapping "${env_args[@]}" python -m scripts.apply_desired_state --execute --allow-merges 1 --streak 1
  ```

  `--allow-merges` raises the whole `merges` threshold, so in a rows phase it
  also waves through **report-only** merges. Beside a null-survivor merge that
  is safe — its survivor is unanchored, a create or archived, and gets no row
  writes. Beside an unbound organization merge (#520) hold it: the survivor's
  rows would land before the merge that later folds it — the Heck ordering
  spread over two nights, where an in-place name update overwrites the name the
  merge would have kept.
- **Child rows: present anywhere satisfies; else the canonical row is in
  dispute.** Any row of the type carrying the value is a noop (PM's short and
  long org names both stay); absent everywhere, the canonical row becomes an
  `update` for #501 to decide apply-or-overlay; no row of the type, an `insert`.
  An insert takes the parent's canonical flag only if the parent has no canonical
  row *and* no earlier insert of the same run claimed it — the flag is unique per
  parent, so a second claim aborts the transaction. "Earlier" is key order, not
  file order, so a rebuild cannot move the flag; the winner is stable but
  arbitrary, and an overlay is how a person picks otherwise.
- **Verdicts and exit codes.** A dry run always completes: `clean` or `blocked`
  (a threshold exceeded) exit **0** — seventeen pending creates must not fail the
  timer every night — and `stale` exits **3**. `--execute` refuses (exit **1**)
  unless the ledger's last `streak` (manifest: 3) **dry** runs are all clean and
  carry this run's diff digest; it then writes in **one transaction**, re-diffs
  inside it, and rolls back — verdict `rolled_back`, exit 1, the streak restarts
  — unless nothing is left to write. A trigger or constraint firing (the
  org-cycle guard) is the same rollback. A refused `--execute` is recorded as
  mode `refused`, which is not a dry run and so restarts the streak: attempts at
  the gate never add up to opening it, and only the nightly chain builds it.
- **Thresholds** live in `manifest.yml` (`creates 0`, `merges 0`, `conflicts 0`,
  `stale 0`, `updates` unlimited). The flip (#501) passes `--allow-creates N`,
  `--allow-merges N`, `--max-updates N`, `--streak N` for one run. Each has a floor: the thresholds
  refuse a negative, `--streak` refuses anything below 1, and the gate refuses a
  non-positive streak whoever asks it — `--streak 0` used to answer yes on an
  empty ledger.
- **A null owned value is silence.** A desired row carrying a null claims
  nothing for that column — the same as no row at all — so the applier never
  clears a PM value by writing NULL over it.
- **Column scope is exact:** an `UPDATE` names only the changed owned columns;
  an `INSERT` names the parent, the changed columns and the manifest's insert
  defaults. `updated_at`, the touch triggers and the outbox fire as for any
  writer; no `source_key_id` is stamped. That makes the applier a **second door**
  to `organizations.parent_id`, beside the observation API's authoritative
  reparent: the `source_key_mismatch` gate (#334) guards that API, not the
  column, so it neither blocks nor records an applier reparent.
- **What the runs find**, in shape rather than tally: person-name updates
  (punctuation and nickname forms), acronym updates, parent updates on the
  subcommittees PM parents under their committee, and creates of which most are
  probable twins — so the verdict stays `blocked` until #501 works the diff. The
  numbers are re-derived from the artifact, never restated here (`docs/CONTEXT.md`
  Rule 2 — a doc that repeats a count owns a second copy of it, and the runbook's
  first copy was already one update behind the run beside it):

  ```bash
  jq '{verdict, counts, by_table}' "data/applier/$(ls data/applier | grep 'Z$' | tail -1)/summary.json"
  jq -r '[.run_id, .mode, .verdict] | @tsv' data/applier/ledger.jsonl   # the streak
  ```

Install the nightly chain once (`docs/COMMANDS.md` § Scheduled timers lists it):

```bash
sudo cp infra/power-map-desired-state.service infra/power-map-desired-state.timer /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now power-map-desired-state.timer
```

Design: `docs/plans/2026-09-10-diff-applier-design.md`.

---
