# power-map — Testing the ingestion tiers (#497, #499)

Moved whole out of `docs/TESTING.md` for its token budget (the #444 rule) when
#890 added the commit hook's routing there. The mapping project's dbt-duckdb
fixture tier and the diff-applier's database-free engine tier; the runbook for
the chain they test is `docs/RUNBOOK_DESIRED_STATE.md`.

---

## Mapping-project tier (#497)


`tests/core/ingestion/mapping/` builds the dbt-duckdb project against a fixture
store (`fixtures/store/`, the puller's layout) through a `build` fixture that
writes PM's tables as Parquet from tuples and runs `dbt build`, once per distinct
input for the session. Builds share one duckdb path and target, so all but the
first partial-parse; each keeps its own store, which its staging views read
(#890, `test_build_cache.py`). The tier is unit-tier, carries `pytest.mark.dbt`
(#890), and needs the `mapping` group —
`tests/optional_groups.py` announces its absence rather than letting the skip
pass as coverage. `EXPECTED_WARNINGS` pins exactly which dbt warnings a build
may raise (the blank-name case); a new warning fails the test.

`test_real_snapshot.py` is marked `integration`: it builds against the landed
store plus the read-only crosswalk export and asserts measured counts (seeded
by the design doc, re-measured by #655). It never opens a database, and it **skips by name** when the
store or the export is absent in that checkout — provision with
`scripts/pull_datasets.py` and `scripts/export_pm_tables.py`.

## Applier tier (#499)


The diff-applier reads the database only through a `LiveStore` protocol, so
its engine tests (`tests/core/ingestion/test_applier_*.py` — count them with
`uv run pytest --collect-only -q tests/core/ingestion/test_applier_*.py`) run
with no database against `FakeLiveStore` in `tests/core/ingestion/applier_fakes.py`,
which serves rows from dicts and records every request — how "a row outside
the crosswalk is never read" and "a column outside the owned set is never in
an UPDATE" are asserted on the emitted SQL (`FakeConn` records statements and
the transaction outcome). `write_desired(dir, **tables)` builds a loadable
desired-state directory from dicts.

Every module here calls `pytest.importorskip("duckdb")` before its first
`src.core.ingestion` import — integration modules included, since collection
imports a module before its marker deselects it. `applier_pg` reaches dbt
through `mapping`, and the main checkout's venv is production's, pruned of the
`mapping` group on every restart: an unguarded module is a collection *error*
there, and every commit made in main fails the unit hook (#545).
`tests/test_optional_groups.py` collects the suite with each optional group's
package blocked and fails on any such error.

`tests/scripts/test_apply_desired_state_db.py` is the integration tier for
what the fake cannot prove — the asyncpg store's SQL, apply-twice-is-a-no-op,
a gated create writing nothing, an execute writing exactly the predicted
entries and touching their parents, a curator column surviving, a cyclic parent
rolling back, a crosswalk change being stale, and an entity archived since the
export being stale — the `LiveStore` contract that `entity_rows` returns
`archived_at` whatever columns it was asked for — on the rollback connection.
#498's acceptance sits beside it in `tests/scripts/test_curation_overlay_seam.py`:
a pin through the real admin route, exported through `export_pm_tables.run`
from the same rollback connection, built with dbt over the fixture store, and
diffed — a noop while pinned, the update back once unpinned. It needs both the
`mapping` group and `TEST_DATABASE_URL`, and skips by name without dbt.
#514 adds the Heck tombstone there end to end: the real person merge folded
once through the CLI, `Denny Heck` held, and a second run a no-op. The unit
tier proves the merge phase's routing and verification with a recording
primitive injected into `apply_diff(..., merges=…)`; `FakeLiveStore(previews=…)`
answers `merge_preview`.
