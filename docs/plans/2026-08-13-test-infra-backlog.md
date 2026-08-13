# Test-Infrastructure Backlog Orchestration — #367, #368, #373, #426

Date: 2026-08-13
Tracking issue: TBD (opened after this doc merges)

## Goal

Clear the four open test-infrastructure follow-ups from the #300 (browser
testing tier) and #369 (weekly a11y timer) shipping cycles: archived-entity
a11y coverage (#426), axe-after-interaction states (#367), real-browser smoke
of happy-dom-simulated flows (#368), and bats tests for the a11y-sweep bash
entrypoints (#373). Two merge-safe batches, parallel within each, gated
between.

## Approved approach

- Standard equal-weight rubric: (Foundation × 2) + (Correctness × 2) + Scope.
- Deployment context: early production — careful with shared fixtures, runway
  to build right.
- Nothing deferred; #373 keeps its optional shellcheck pre-commit wiring.
- Hybrid parallelism: parallel within batches, gates between.
- Worktree ceiling: none on the git side (plain worktrees + `.env` symlink; no
  port pool or DB clone). The real constraint is the shared
  `TEST_DATABASE_URL`: the browser tier is truncate-and-seed and must run
  alone (#300). **Workers run lint + non-browser tests only; the orchestrator
  runs the browser tier once, alone, at each batch gate against the batch
  branch.**
- Batch → main merge strategy: **PR per batch branch, regular merge commit**
  (matches all prior merges in this repo). Intra-batch worker → batch
  integration stays FF/regular-merge (skill Rule: never squash/rebase inside
  a batch).

## Prioritization rubrics

Score = (Foundation × 2) + (Correctness × 2) + Scope, max 15.

| Dimension | 1 | 2 | 3 |
|---|---|---|---|
| Foundation Leverage | Standalone | 1–2 issues benefit | Multiple issues depend on it |
| Correctness Risk | Cosmetic | Edge-case / silent gap | Data loss / silent failures |
| Scope Clarity | Needs discovery | Clear, minor decisions | Mechanical |

## Scored backlog

| Issue | F | C | S | Score | Blast |
|---|---|---|---|---|---|
| #426 archived-entity seeds + enum | 3 | 2 | 3 | **13** | Med-High |
| #367 axe-after-interaction | 2 | 2 | 2 | **10** | Med |
| #373 bats tests for sweep scripts | 1 | 2 | 2 | **8** | Low |
| #368 real-browser smoke | 1 | 2 | 2 | **8** | Med |

All four verified open-in-fact (no `tests/sh/`, no bats dependency, no
archived seeds in `admin_routes.py`; `test_a11y_browser.py`'s docstring names
#367/#368 as pending follow-ups).

## Conflict zones

| File | Issues | Required order |
|---|---|---|
| `tests/api/admin/admin_routes.py` | #426 writes; #367/#368 consume | #426 merges before Batch B; read-only thereafter |
| `tests/api/admin/test_a11y_browser.py` | fixture hoist (rides with #426); #367/#368 consume | hoist merges before Batch B |
| `tests/api/admin/conftest.py` | hoist destination | read-only for Batch B |

Key discovery: `live_server` / `page` / `seeded_ids` / `browser_db` are
module-local fixtures in `test_a11y_browser.py` — invisible to sibling test
files. Any new browser-test file (both #367 and #368) requires hoisting them
to `tests/api/admin/conftest.py` first. The hoist is bundled into #426's
agent as its own mechanical commit.

## Dependency graph

```
#426 (seeds/enum + fixture hoist) ──► #367 (interaction axe)
                                  └─► #368 (browser smoke)
#373 (bats) ── independent
```

## Batch execution plan

| Batch | Issues | Agents | Files | Gate |
|---|---|---|---|---|
| A | #426 ∥ #373 | 2 | A1: `admin_routes.py`, `test_a11y_browser.py`, `conftest.py` · A2: `tests/sh/` (new), bats wiring, `.pre-commit-config.yaml` | Start immediately |
| B | #367 ∥ #368 | 2 | B1: new interaction-test file · B2: new smoke-test file | After Batch A merged to main |

- Fully file-disjoint within each batch; worker branches mergeable in any
  order into `batch/a` / `batch/b`.
- Agent A1 (#426) commit sequence: (1) archived seeds + enumeration,
  (2) mechanical fixture hoist to `conftest.py` — separate commits, one
  review.
- Batch B read-only files: `conftest.py`, `admin_routes.py`,
  `test_a11y_browser.py`. An agent needing a helper puts it in its own new
  test file; foundation tweaks go as small post-merge PRs.
- Orchestrator runs the browser tier alone at each gate:
  `uv run --env-file … pytest -m browser` (see docs/TESTING.md).

## Key decisions

- **Fixture hoist rides with #426, not #367** — both are foundation work for
  the browser follow-ups, combined well under one review sitting; makes
  Batch B fully parallel instead of forcing #368 into a third batch.
- **Foundation files read-only for Batch B** — prevents concurrent edits to
  `conftest.py` by two agents (skill "foundation shared files are read-only"
  rule).
- **Browser tier serialized at the gate** — shared managed-PG test database
  has no CREATEDB; truncate-and-seed tiers cannot overlap (#300). Workers
  self-verify with lint + non-browser tiers only.
- **#373 includes shellcheck** — user opted for full scope; it is disjoint
  from everything else in the batch.
- **PR per batch** — preserves the repo's all-PR history; regular merge
  commit keeps per-agent commits visible.

## Deferred items

None — user explicitly kept full scope for all four issues.

## Out of scope

- Promoting Vitest suites to browser-only (resolved against in the #300
  discussion; restated in #368).
- Non-a11y functional browser coverage beyond the two smoke flows named in
  #368.
- Editing `skills-vendor/` (process-log capture goes to the skills repo as an
  issue per project policy).
