# Commit-hook test routing and dbt partial parse — design (#890)

## Goal

Cut the pre-commit unit hook from ~12 min to seconds for a docs-only commit
and to a fraction for a typical code change, without weakening the ship gate:
`scripts/pre-ship.sh` keeps running the whole unit tier before every push.

## Measured (2026-11-26, profile on #890)

- Unit tier 707–805 s wall, CPU-bound. dbt-duckdb builds are 87% of test time:
  `tests/core/ingestion/mapping/` 527 s, `test_build_desired_state` +
  `test_apply_desired_state` 79 s; the other ~3,600 tests ~86 s.
- A fixture build is ~10–14 s, of which `dbt parse` is ~6 s. Every build gets
  fresh tmp paths, which `sources.yml` renders from `env_var()` at parse time,
  so dbt never partial-parses. Stable paths bring the parse under 1 s.
- The test modules that read `docs/` or a `.md` file run in ~25 s.

## Approach

1. **`dbt` marker.** Every test that invokes dbt carries `pytest.mark.dbt`. An
   autouse guard in the root conftest fails any test that runs dbt without the
   marker, so a new dbt test cannot leak into the fast path unmarked.
2. **Partial parse in the fixture build.** `_build_cache` builds every key at one
   session-stable store path, duckdb path and target path, then copies the
   built file to its per-key location. dbt partial-parses after the first build.
3. **Path-routed commit hook.** `scripts/commit_tests.py` reads the staged set
   (`git diff --cached --name-only --no-renames -z`, deletions included) and
   runs one pytest:
   - nothing staged (a manual `pre-commit run`) → the whole unit tier;
   - every path a doc (`docs/**` or `*.md`) → only the test modules that read
     docs, found by text match (over-matching only grows the subset);
   - otherwise the unit tier without `dbt`, plus `dbt` when a staged path can
     reach the dbt tier: the dbt modules' import closure, anything under
     `src/core/ingestion/` or `tests/core/ingestion/`, or a dependency or
     shared-config file (`pyproject.toml`, `uv.lock`, any `conftest.py`).
   The hook id stays `pytest`, so the refresh hook's skip knob (#755) holds.
4. **Integration is opt-in.** TESTING.md states the full `-m integration` suite
   runs only when asked for; the default is the integration modules that mirror
   the touched code. AGENTS.md's TESTING.md index line says so.

## Key decisions

- **Route, don't skip.** A docs-only commit still runs the doc guards
  (`test_context_surface`, the SocratiCode doc-parity tests, …), which exist to
  catch doc edits. Skipping pytest outright would have moved them to pre-ship.
- **Text match over a marker for doc readers.** No list to keep current; a
  comment that names a doc over-selects, which costs seconds, never coverage.
- **No pytest-testmon.** It tracks Python coverage only; templates, SQL, the
  dbt project and docs would still need path rules, and it adds per-worktree
  state.
- **No xdist on this host.** 2 vCPUs, shared by the agent and parallel
  worktrees; the vendored gate's `gate-workers.sh` already resolves `auto` to
  serial at ≤2 cores and uses xdist wherever it is installed on a larger host.
- **pre-ship.sh is the full-tier backstop.** A routed-out failure is caught
  before push, not at commit.

## Out of scope

- Profiling and re-tiering `-m integration` (shares the test DB with parallel
  work; left open on #890).
- Faster dbt execution (threads, narrower `select`s per test family).
- `test_build_desired_state` / `test_apply_desired_state` partial parse: they
  run the build script end to end, whose paths are its own.
