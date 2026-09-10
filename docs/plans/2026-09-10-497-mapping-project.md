---
title: "#497 mapping project — build plan"
date: 2026-09-10
status: approved 2026-09-10
design: 2026-09-10-mapping-project-design.md
---

# #497 mapping project — build plan

Executes the approved design in `2026-09-10-mapping-project-design.md`. That
doc holds the *why* and the measurements; this one holds the order of work and
what "done" looks like at each step.

## Problem

#499 (the diff-applier) has nothing to diff. The puller (#496) lands usa-wa's
snapshots nightly and the crosswalk (#495) maps producer ids to PM ids, but no
artifact yet says what PM *should* hold — and the one place that could say it,
`src/core`, still carries WA vocabulary (`role_title.py`) that the #490 design
names as the leak to close. Every downstream issue in the epic waits on this.

## Approach

Add a dbt-duckdb project at `src/core/ingestion/mapping/` that reads the
snapshot store plus a Parquet export of `producer_crosswalk` and
`curation_overlay`, and materialises six desired-state Parquet tables for
persons and organizations. Models are file-to-file — no database connection —
so dbt tests run in the unit tier on fixtures. Ownership is declared per
event type in a manifest the applier will read. Delete `role_title.py` and its
seed generator, make `title` required again on seat observations, and land a
no-allowlist gate that keeps `src/core` WA-free outside `ingestion/`.

## Tradeoffs / alternatives

Settled in the design doc; restated so a reviewer of this plan need not open it.

- **duckdb reads Postgres live** — rejected: a dbt profile holding
  `DATABASE_URL` (production from any directory, #402) is a door the
  `scripts/`-only DSN sweep cannot see, and dbt tests would need a database.
- **Applier does the crosswalk join** — rejected: the artifact would not be
  PM-keyed, so a diff cannot be reviewed without the crosswalk in hand.
- **`founded` from `first_biennium`** — rejected: 35 committees share the
  `1991-92` data horizon; `v_org_lifespan` derives only `ended_on` anyway.
- **`role_types.title_template`** — rejected: its only remaining consumer was
  the admin suggestion, which is not wanted.
- **Defer the WA-free gate to #500** — rejected: `role_title.py`'s last real
  consumer is bootstrap tooling for seats usa-wa now publishes.

## Steps

Each step ends green on the full gate (`ruff`, unit, integration) and is its
own commit or small run of commits. Test-first throughout.

1. **Toolchain.** Add a `mapping` dependency group (`dbt-core`, `dbt-duckdb`,
   `duckdb`, pinned). Scaffold `src/core/ingestion/mapping/` — `dbt_project.yml`,
   a `profiles.yml` targeting `data/mapping.duckdb`, `sources.yml` declaring the
   snapshot store's `data.csv` paths. Gitignore `data/desired_state/` and
   `data/mapping.duckdb`. Extend `scripts/worktree-setup.sh` to sync the group
   (#450: a worktree short of a group is a silently narrower suite).
   *Done when:* `dbt debug` passes from the worktree; `uv sync --group mapping`
   is what `worktree-setup.sh` runs; the shell test for that script covers it.

2. **`curation_overlay` table.** `schema.sql`: `(id, entity_type, entity_id,
   field, value, note, created_by, created_at, updated_at)`, unique on
   `(entity_type, entity_id, field)`, no FK on `entity_id` (polymorphic, like
   the ancillary tables), `set_updated_at` trigger, placed per the #307/#312
   reconciliation rule. `docs/SCHEMA.md` section. Applied to the **test** DB via
   `apply-schema.sh --test` only.
   *Done when:* schema test green; the parity timer's reference side carries
   the table; **production is not touched** — that is a deploy step at ship.

3. **Export step.** `scripts/export_pm_tables.py` dumps `producer_crosswalk`
   and `curation_overlay` to `data/usa_wa_snapshots/_pm/<table>.parquet`.
   Read-only: `add_dsn_args` / `echo_target()`, no `--execute`. Writes to a
   staging path and `os.replace`s, same contract as the puller.
   *Done when:* `test_dsn_sweep.py` accepts it unchanged; a dry run against
   production echoes the target and writes 12,427 crosswalk rows; unit tests
   cover the staging/replace and an empty overlay.

4. **Persons models.** `stg_usa_wa__persons`, `stg_usa_wa__person_crosswalk`,
   `int_person_identity` (producer id → `pm_id` via the export; follows
   `merged_into` to the survivor), `desired_people`, `desired_person_names`
   (one `legal` row per person, `COALESCE(overlay, mapped)` on `name`). Fixture
   CSVs under `tests/core/ingestion/mapping/fixtures/`; a pytest wrapper runs
   `dbt build --target test` against them.
   *Done when:* dbt schema tests (unique, not_null, relationships) pass on
   fixtures; **the gap-E test passes** — a fixture crosswalk row with
   `merged_into` set resolves to the survivor's `pm_id`, and a model that
   ignored the tombstone would fail it.

5. **Organizations models.** `stg_usa_wa__organizations`,
   `stg_usa_wa__org_crosswalk`, `int_org_identity`, `desired_organizations`
   (identity), `desired_organization_parents` (row-scoped, from `agency` /
   `org_type`), `desired_organization_names` (`coalesce(long_name, name)` →
   legal — no dba; see the design doc's 2026-09-10 revision),
   `desired_organization_acronyms`, `desired_organization_merges`. Overlay
   joined on every producer-owned field.
   *Done when:* fixture build green; a fixture org with an overlay row for
   `parent_id` emits the overlay value, one without emits the mapped value, and
   an org whose agency determines no parent emits no row.

6. **`desired_entity_events` + the ownership manifest.** `dissolved` only, from
   `last_biennium`, year precision, none when it equals the current biennium.
   `desired_state/manifest.yml` declares, per output table, its row scope
   (crosswalk membership, `resolution IN ('live','merged')` per `SCHEMA.md`) and,
   for events, `owned_event_types: [dissolved]` — the contract #499 reads.
   *Done when:* fixture build green; a fixture org at `2025-26` emits no event;
   the manifest is validated by a unit test against the models that exist.

7. **Real-snapshot check.** A marked test (`integration`, since it needs the
   export from step 3) runs `dbt build` against the live store and asserts the
   design doc's measurements: 3,118 anchored persons, 219 orgs, 186 acronyms,
   152 `dissolved`, 34 orgs with no event. Any divergence is recorded on #497 as
   #501 triage material, not fixed here.
   *Done when:* the test passes or every divergence is written down with a
   number.

8. **`src/core` goes WA-free.** Delete `role_title.py`,
   `scripts/generate_wa_roles.py`, `tests/core/test_role_title.py`.
   `resolve_role`: drop synthesis; `title_required` applies to every create.
   `schemas.py` `_check_resolution_mode`: `title` required regardless of
   `jurisdiction_id`. `admin/roles.py`, `admin/roles_detail.py`: drop the
   suggestion. Reword the WA docstrings in `observation.py` and
   `normalizers/identifier.py`. `docs/API_ROLES.md` lines 89 / 99 / 116 and
   `OBSERVATIONS.md`: `title` **required**, `role_title_unavailable` retired,
   marked **breaking**. New `tests/test_src_core_wa_free.py` with detector
   self-tests, in the `test_dsn_sweep.py` idiom.
   *Done when:* the gate passes with no allowlist; `test_resolve_role_structural.py`
   is re-anchored on the new contract; a seat observation without `title` is
   `rejected: title_required`.

9. **Docs, version, ship.** `docs/RUNBOOKS.md` (export step, `dbt build`, the
   fixture tier), `docs/COMMANDS.md` (dev loop for the mapping project),
   `AGENTS.md` layout line for `src/core/ingestion/mapping/`, `docs/CONTEXT.md`
   budget check. Version bump in lockstep (`pyproject.toml` + `package.json`).
   `CR --fix`, then `shipping-work`.
   *Done when:* PR merged; #497 closed against its three acceptance criteria.

## Open questions / risks

- **Version bump size for a breaking API change.** Step 8 re-tightens #267's
  loosening. No live producer sends seat observations, so the blast radius is
  documentation — but it is a contract change. **Decided: minor.**
- **Where the mapping group runs in production.** The API service does not need
  dbt. #499's timer will. Proposal: `mapping` is a group like `seed`, synced by
  `worktree-setup.sh` now and by #499's unit's `ExecStartPre` later — nothing
  changes in `power-map.service` here. **Agreed.**
- **The 152 `dissolved` agreement is unproven.** Step 7 is the first time the
  producer's implied events meet PM's stored ones. Expected to match; if not,
  the numbers go to #501.
- **Puller concurrency.** The nightly puller writes at 09:00 UTC; a `dbt build`
  reading mid-landing is safe because landing is atomic (a version directory
  exists only when complete, #496 CR 24) and models read a named version, not
  "latest". The export step gets the same staging/replace contract.
- **`field` vocabulary on the overlay.** Free text in the table; the mapping
  models' source tests enforce `accepted_values` per `entity_type`, so an
  overlay row naming a column no model maps fails the build loudly instead of
  being silently ignored. #498's UI constrains it further.
- **`worktree-setup.sh` must gain the group before any worktree runs step 4's
  tests**, or the wrapper skips at module scope and the suite stays green while
  running less — the exact #482 failure. Step 1 lands it first for that reason.
