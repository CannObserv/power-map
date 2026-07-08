# Integration Test Suite — Duration Audit (#283)

**Date:** 2026-07-08
**Baseline:** `2131 passed, 2 skipped in 921.05s (15:21)` — `pytest -m integration`
(matches the issue's ~16 min / ~966 s prior run; 903 unit tests deselected).

> **Note (orchestrator):** this audit was run against the pre-#280/#277/#262/#281/#282
> tree (the Batch C worktree branched from an unpushed `origin/main`). The current
> suite on `main` is ~2147 integration tests, so the absolute count/wall-clock here
> are approximate. The per-test-overhead analysis and structural recommendations
> below are unaffected — they concern the fixture/pool architecture, which those
> batches did not touch.

## TL;DR

The suite is **already well-optimized** on the axes #283 lists as candidates
(seeding, fixture scope, connection reuse for test-side helpers). The only
remaining *large* lever — eliminating the ~170–190 ms per-test asyncpg pool
create/introspect cost — is **structurally coupled** to the global-singleton
pool + per-test `TestClient` lifespan model and **cannot be captured without a
redesign that this issue explicitly flags as high-risk**. An empirical attempt
at the naive version (session-scoped shared `client`) produced **1245 failures**
(see "Rejected change" below), confirming the coupling. No low-risk,
self-contained, fully-verifiable win was found, so this pass ships the audit +
recommendations only — no test-behavior change.

## Where the time goes

`--durations=50` shows the slowest *individual* test bodies are only 1–2 s
(merge/dup tests). The wall-clock is **not** dominated by any single test; it is
the **sum of per-test fixture setup** spread across ~2131 tests. Profiling the
per-test `TestClient(app)` lifespan (function-scoped `client` fixtures, ~60 of
them, byte-identical `with TestClient(app) as c: yield c`):

| Component | Cost | Notes |
|---|---|---|
| `asyncpg.create_pool` (min_size=1) | **~170–190 ms** | TCP handshake + asyncpg type introspection on the first connection. Dominant. |
| `EmbeddingRegistry.load` | ~15 ms | one DB query in lifespan |
| `apply_schema` (session start, once) | ~1.0 s | already once-per-session, negligible amortized |
| `_reset_data_tables` (session start, once) | <1 s | truncate non-reference tables once |

**Measured directly:** 5× `TestClient(app)` enter/exit + 1 request each =
0.298 s/each; 5 requests on a *shared* client = 0.069 s/each. So **~0.23 s of
pure per-test overhead**, ~170 ms of which is `create_pool`. Across the
~1700 endpoint tests that spin up their own `TestClient`, that is a projected
**~290 s** (≈ a third of the 921 s baseline).

`min_size=0` makes `create_pool` itself free (0.1 ms) but moves the identical
~188 ms handshake+introspection to the first `acquire` — **net zero**, because
every endpoint test issues at least one DB request. The introspection cost is
inherent to a fresh connection; only *sharing connections across tests* removes
it.

## What is already optimal (do not touch)

- **Session-scoped `db_pool`** (`tests/conftest.py`): one asyncpg pool for
  test-side helpers; `apply_schema` + `_reset_data_tables` run **once** at
  session start. Reference/lookup tables are preserved across the truncation.
- **All async fixtures are already `loop_scope="session"`** — zero found
  without it. No per-function/per-module async fixture re-creation to promote.
- **No redundant seeding**: `apply_schema` runs once; locale/script and
  jurisdiction-relationship-type rows live in `_REFERENCE_TABLES` and survive
  the truncation, so they are not re-seeded per test.
- **`unit_client` / mock-DB clients** already override `get_db`, and the
  `AsyncClient`-based inline clients (`test_*_search`, `*_inline`) already avoid
  the lifespan entirely (~0 pool cost).
- Pool sizing in tests is already minimal (`DB_POOL_MIN_SIZE=1`,
  `DB_POOL_MAX_SIZE=2`, set in `pytest_configure`).

## Rejected change (proves the coupling)

**Attempted:** promote the ~60 identical function-scoped `client` fixtures to a
single session-scoped `client` in the two `conftest.py` files (admin + public),
deleting the per-file overrides.

**Result:** `1245 failed, 863 passed` — `RuntimeError: Database pool is not
initialised. Call create_pool() first.`

**Root cause:** the app pool is a **module-global singleton** (`src/core/db._pool`)
managed by the FastAPI lifespan. A session-scoped `client` enters the lifespan
once (creating `_pool`). But ~8 *other* fixtures/tests still enter their own
`with TestClient(app)` lifespan (`test_role_merge`, `test_router_ordering`,
`test_merge_unit`, `test_dup_badges`, `test_dashboard`, `test_deps` line 159,
public `unit_client`), and each one's exit calls `db.close_pool()` →
`_pool = None`. Every subsequent request through the shared session client then
hits the un-initialised global and 500s. Reverted in full.

This is not an isolation *data* leak — it is a lifecycle collision on the shared
global pool. Fixing it means making the session client the **sole** lifespan
owner and converting all other lifespan-entering clients to either reuse it or
run lifespan-less — a cross-cutting change to conftest + ~8 files that also
interacts with `TestClient`'s separate portal event loop (the pool must be
created on the portal loop, not the pytest session loop — the `loop_scope`
gotcha). That is the redesign #283 flags as needing care, so it was not shipped.

## Structural options — recommendation

### 1. Shared session-scoped app pool / TestClient — RECOMMENDED, MEDIUM effort

**Payoff:** the largest available — projected ~290 s (~30%) off the wall-clock
by paying the ~170 ms pool create/introspect **once** instead of ~1700 times.

**Feasibility:** tractable but not self-contained. Requires:
1. One session-scoped `TestClient(app)` entered once (owns the lifespan → the
   app pool is created once, on the TestClient portal loop, and stays alive).
2. Rewriting the ~8 remaining lifespan-entering fixtures so they **never call
   `close_pool()`**: mock-DB ones (`test_router_ordering`, `test_merge_unit`)
   construct `TestClient(app)` *without* `with` (no lifespan needed — they
   override `get_db`); real-DB ones (`test_role_merge`, `test_dup_badges`,
   `test_dashboard`, `test_deps:159`) reuse the shared session client.
3. Preserving per-test data isolation exactly as today (unique IDs +
   fixture-level cleanup — unaffected by client scope).

**Risk:** medium. The failure mode is loud (500s, not silent data bleed), so
regressions are caught immediately by the suite. Main hazard is a straggler
lifespan slipping through and nulling `_pool`; a guard (e.g. a session-autouse
fixture asserting `_pool is not None` after each test, or making `close_pool`
refuse while a session client is active) de-risks it. **Best done as its own
focused PR with the full integration suite as the gate**, not folded into an
unrelated change.

### 2. `pytest-xdist` parallelism — CONDITIONAL, HIGH effort

**Payoff:** potentially the largest (near-linear with worker count) but bounded
by the DO DB connection cap. With `DB_POOL_MAX_SIZE=2` per worker and a session
pool per worker, `-n 4` needs ~12 connections — feasible on the current tier
only with care.

**Feasibility:** the suite shares ONE Postgres DB with **no per-test data
reset** — isolation today relies on unique ULIDs, not table cleanup, so two
workers writing concurrently to the same DB will mostly not collide on data,
BUT: (a) tests that `SELECT COUNT(*)`/list-all or assert global state (e.g.
`test_list_empty`, dashboard counts) **will** cross-contaminate; (b) the
session-start `_reset_data_tables` TRUNCATE would race across workers. Requires
**schema-per-worker or template-DB-clone-per-worker** isolation
(`PYTEST_XDIST_WORKER` → distinct DB/schema, each `apply_schema`'d).

**Risk:** high. Silent cross-worker contamination is the exact isolation
regression the issue forbids. **Do not attempt without per-worker DB isolation
and a full audit of every count/list-all assertion.** Recommend deferring until
after option 1.

### 3. Transaction-rollback / savepoint per test — NOT RECOMMENDED here

**Payoff:** would replace hand-rolled `DELETE` teardowns with a cheap rollback,
and could give true per-test isolation. But the current suite already gets
acceptable isolation from unique IDs, so the *speed* payoff is small (teardown
DELETEs are not in the hot path — pool creation is).

**Feasibility:** poor fit. The app acquires its own connections from the
app-level pool (`get_db` → `db.get_pool().acquire()`), which are **different
connections** from the test's `db_pool` connection. A `BEGIN/ROLLBACK` on the
test connection cannot wrap writes the app makes on a *pool* connection.
Achieving rollback-per-test would require forcing the app and the test to share
a single connection (single-connection "pool" pinned per test) — a significant
rework that fights `loop_scope="session"` and the pool design.

**Risk:** high complexity, low speed payoff. **Skip.**

## Recommendation summary

Ship option **1 (shared session client / app pool)** as a dedicated, fully-gated
follow-up PR — it is the highest-payoff, lowest-relative-risk lever, but it is a
lifecycle redesign, not a drop-in, so it does not belong in a "low-risk,
self-contained" pass. Options 2 and 3 should wait behind it.
