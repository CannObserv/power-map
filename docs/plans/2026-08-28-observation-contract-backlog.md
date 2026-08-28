# Observation-contract backlog clearance (#473, #476–#479)

## Goal

Clear the five open issues that came out of the #474 exchange with usa-wa plus the standing
context-budget item, in three merge-safe batches. Four are producer-facing defects in the observation
and change-feed contracts — each one a case where power-map is silently wrong or silently silent
toward the one consumer that polls it nightly. The fifth is the AGENTS.md budget breach, which must
run last because it measures a doc tree the other four change.

## Approved approach

- **Rubric:** correctness-led, standard weights — `(Foundation × 2) + (Correctness × 2) + Scope`, max 15.
- **Deployment context:** active production. usa-wa polls the live API nightly; contract changes are
  additive-only, and no destructive migration ships without a gate.
- **Parallelism:** hybrid — parallel within a batch, a merge + test gate between batches.
- **Worktrees:** `isolation: "worktree"` for every worker. After creation each needs
  `bash scripts/worktree-setup.sh <path>` (#450) — never share the main checkout's venv.
- **Concurrency ceiling:** *not* set by worktree provisioning (no port pool, no custom create script)
  but by the **shared test database**. `tests/conftest.py::db_pool` is session-scoped and calls
  `reset_data_tables()` at session start, so two concurrent integration runs against
  `co_pm_db_test` truncate each other mid-flight. `co_pm_db_test_user` has neither CREATEDB nor
  superuser, so per-agent slots would need a one-time doadmin action.
  **Resolution chosen: serialize the verification gate** (ladder rung 2). Workers run the hermetic
  default suite; the orchestrator runs the integration tier once per batch on the batch branch.
  Per-batch agent count is therefore bounded by host CPU/RAM, not by the DB.
- **Batch → main merge:** regular merge commit (preserves the per-agent red-phase TDD commits).
  Intra-batch worker → batch is fast-forward/regular-merge regardless, so
  `worktree-destroy.sh --base batch/<X>` can verify ancestry.

## Prioritization rubrics

| Dimension | 1 | 2 | 3 |
|---|---|---|---|
| **Foundation Leverage** | Standalone improvement | 1–2 other issues benefit | Multiple issues depend on or are simplified by this |
| **Correctness Risk** | Cosmetic / organizational | Edge-case incorrect behaviour, runtime failure risk | Data loss, race conditions, silent failures |
| **Scope Clarity** | Requires design discovery | Clear direction, minor decisions needed | Mechanical — implementation obvious from the issue |

`Score = (Foundation × 2) + (Correctness × 2) + Scope`, max 15.
Blast radius drives *sequencing*, not score.

## Scored backlog

Scores below are **post-decision**: three issues carried a named decision that was resolved at the
approval gate (see Key decisions), which moved every Scope Clarity to 3 and #477 from 12 to 15.

| # | Issue | F | C | S | Score | Blast | Note |
|---|---|---|---|---|---|---|---|
| 477 | Anti-resurrection attach unlabeled — `attached_archived` on `ObservationResponse` | 3 | 3 | 3 | **15** | High | Silent failure; cost usa-wa a month anchored to a dead row. Now spans all four anti-resurrection paths |
| 478 | Provenance unclaimable on an identical id-addressed observation | 2 | 3 | 3 | **13** | Med | Silent no-op. Gates usa-wa's 2,468-row provenance backfill — the only item another party waits on |
| 476 | Duplicate-assignment audit: `deepened_start` auto-merges without proving coverage | 1 | 3 | 3 | **11** | Low | Discards a span with no record. `subsumed` already carries the coverage proof to copy |
| 479 | A restored entity's tombstone stays live in the change feed | 1 | 2 | 3 | **9** | Low-Med | Edge case — only on a restore, ~1/month. PM's own data stays correct; the defect is unobservability |
| 473 | AGENTS.md over budget (6045/6000) + degraded-variant note | 1 | 1 | 2 | **6** | High | **Rescoped 2026-08-28** — the seam-judging half shipped in `d2f5ebb` (PR #475) |

**Closed-in-fact check: none.** All four code anchors re-verified in the tree on 2026-08-28
(`audit_assignment_duplicates.py:61`, `schemas.py:974-992`, `observation.py:2102`, `changes.py:20-35`).
#473's residual confirmed live: `AGENTS.md` untouched since the measurement commit `6a919407`, and the
degraded-variant note absent from `docs/SOCRATICODE.md`'s `## Repo-specific notes`.

## Conflict zones

| File | Issues | Required order / resolution |
|---|---|---|
| `src/api/public/schemas.py` (`ObservationResponse`) | 477, 478 | #477 first. #478 adds its `claimed` signal to the same class |
| `src/api/public/assignments.py` | 477, 478 | Same gate — 2 sites for #478, 4 for #477 |
| `tests/core/test_observation_writers.py` | 477, 478 | Same gate |
| `docs/API_ASSIGNMENTS.md` | 476, 477 | **Line-window split inside Batch A.** #476 owns *only* the `**Duplicate cleanup:**` bullet (L106 at time of writing — re-derive at launch); #477 owns §Observation write, §Retraction and the other three bullets of §Write semantics & provenance. Separate lines in one list; **no restructuring of the list by either agent** |

**Read-only for Batches A and B:**

- `AGENTS.md` — #473's target and already 45 tokens over. Policy-level rules go into the detail docs
  (the #428 index-cannot-grow rule); #473 re-measures at its start and absorbs whatever landed.
- `docs/CONVENTIONS.md:37` — one bullet covering both retract semantics and dup cleanup. Neither
  agent needs it: #476 changes what `deepened_start` *does*, and that line only carries a pointer.

**A grep that was a surface, not a footprint:** `merged_into` for #479 returns 40+ files because it
appears throughout the merge subsystem. #479's real set is `docs/CHANGE_FEED.md`,
`scripts/restore_467_committee_succession.py`, `src/core/merge_signals.py` and two tests. Sized down
before batching so #479 was not falsely treated as batch-isolating.

## Dependency graph

```
Batch A (parallel, 3)          Batch B         Batch C
  #477 ──────────────────────▶ #478 ─────────▶ #473
  #476 ──┐                                  ▲
  #479 ──┴──────────────────────────────────┘
```

- **#477 → #478** — contention. Three shared files, verified.
- **everything → #473** — epistemic, not contention. `curate context` measures a token count and
  relocates content into indexed docs. #476/#477/#478/#479 all add doc content, so a curation run
  scheduled earlier curates a tree that does not exist at merge time.
- **#476 ⟂ #479 ⟂ #477** on source, tests and docs, except the one doc bullet above.

## Batch execution plan

### Batch A — 3 parallel agents · branch `batch/a`

| Agent | Issue | Owns | Must not touch |
|---|---|---|---|
| A1 | #477 | `schemas.py`, `assignments.py`, the `ObservationResponse` sites in `people.py`/`orgs.py`/`roles.py`/`jurisdictions.py`, `core/{observation,citations,assignment_relationships}.py`, `docs/OBSERVATIONS.md`, `docs/API_ASSIGNMENTS.md` **except the Duplicate cleanup bullet**, `tests/api/public/test_observation_events.py`, `test_observations_assignments.py`, `tests/core/test_{citations,assignment_relationships,observation_writers}.py` | `AGENTS.md`, `CONVENTIONS.md`, the dup-cleanup bullet |
| A2 | #476 | `scripts/audit_assignment_duplicates.py`, `tests/scripts/test_audit_assignment_duplicates.py`, `docs/AUDITS.md`, **only** the `**Duplicate cleanup:**` bullet in `docs/API_ASSIGNMENTS.md` | everything else in `API_ASSIGNMENTS.md`, `AGENTS.md`, `CONVENTIONS.md` |
| A3 | #479 | `docs/CHANGE_FEED.md`, `scripts/restore_467_committee_succession.py`, `src/core/merge_signals.py`, `tests/core/test_merge_signals.py`, `tests/scripts/test_restore_467_committee_succession.py` | `AGENTS.md`, `schemas.py` (the feed contract is explicitly not changing) |

**Merge order into `batch/a`: A1 → A2 → A3.** A1 is widest and owns most of the shared doc; A2's
single bullet merges behind it; A3 has zero overlap and can land at any point.

**Gate to start:** push local `main` to `origin` first. It is 1 commit ahead (`a3a7d09`, the skills
submodule refresh), and worktrees are cut from `origin/main` — not from the orchestrator's checkout.

### Batch B — 1 agent · branch `batch/b`

| Agent | Issue | Gate |
|---|---|---|
| B1 | #478 | After Batch A is merged to `main` |

Shape B (split, not bundled): #477 dwarfs #478 and they differ in kind — a four-subsystem contract
addition versus a single-function provenance fix. Two clean review surfaces beat one bundle, and the
gate is cheap because Batch B is single-agent anyway.

### Batch C — 1 agent · branch not required (feature branch serves)

| Agent | Issue | Gate |
|---|---|---|
| C1 | #473 | After Batch B is merged to `main` |

Runs `curate context`. Re-measures `AGENTS.md` at start — the number will not be 6045, because
Batches A and B add doc content. Also adds the degraded-variant note **below** the
`END socraticode-doc` marker in `docs/SOCRATICODE.md` (an edit at line 94 is reverted by the next
`init-socraticode` run and reds `test_socraticode_doc_parity.py`).

## Key decisions

1. **#477 covers all four anti-resurrection paths**, not assignments alone (decided at the scoring
   gate). Raises blast to High and Scope Clarity to 3. Rationale: the contract is what makes
   `auto-attached` honest; shipping it on one path leaves the other three lying in the same way.
2. **#478 reports the claim on the wire.** This is what put #478 on `ObservationResponse` and created
   the #477 → #478 edge. Chosen because usa-wa cannot otherwise verify a 2,468-row backfill without a
   read-back per row.
3. **#479 approximates the lost audience via winner subscriptions** rather than recording the
   delete-time audience. This is what kept a schema change — and `schemas.py` — out of #479, leaving
   it fully disjoint from A1. A `restored` `change_kind` was explicitly declined: `change_kind` is a
   parsed contract string for every consumer and the event is ~monthly.
4. **#473 sequences last on epistemics, not contention.** It is the lowest-scoring item in the set and
   the last to run. Blast ≠ priority.
5. **`AGENTS.md` is read-only until Batch C.** Prevents three agents editing a file that is already
   over budget, and gives C1 a stable measurement target.
6. **Verification-mode asymmetry.** Workers run `uv run pytest` only — the default `addopts` deselect
   integration and browser. Workers must **not** run `-m integration`: `db_pool` truncates
   `co_pm_db_test` at session start, so a second concurrent run corrupts the first. Every batch's
   integration coverage is therefore first exercised by the orchestrator *after* merge, not by the
   agent that wrote it. A red integration test at the batch gate is a normal outcome, not evidence
   the worker skipped verification.
7. **No chain-appending artifact in this set** — no migration, ADR or sequence-numbered file. The
   one-chain-agent-per-batch rule does not bind here; decision 3 is part of why.

## Runtime note on issue-body decay

This backlog is three sequential mutations of the tree the issue bodies describe. Every worker must
treat its issue body as a **proposal, not a specification** and verify each file:line against the
current tree before acting — reporting corrections rather than implementing around them silently.
The risk rises with batch depth:

- **Batch A** bodies were written 2026-08-28 against the tree as it stands. Low decay.
- **Batch B** (#478) quotes `update_assignment_fields` and proposes reusing #477's response work.
  #477 will have changed `ObservationResponse` by then — the quoted snippet is expected to be stale.
- **Batch C** (#473) quotes a token count that is guaranteed wrong by the time it runs.

Baseline for the hermetic suite on `main` at planning time: **1642 passed** (37 skipped, 3312
deselected). 33 skips are structural; 3 are browser-group guards that may behave differently in a
fully-synced worktree, so briefs pin the **passed** count only. A worker whose tree does not show
1642 passed before it starts should stop and report rather than reconcile to it.

## Deferred items

None. All five issues named in the request are scheduled.

## Out of scope

- **A `restored` change kind or a tombstone-clearing feed event** (#479 option 3) — declined; see
  Key decision 3. Revisit only if the docs + restore-duty fix proves insufficient.
- **Recording the delete-time subscriber audience** (#479 option 3 variant) — declined for a
  ~monthly event.
- **Per-agent test-database slots** — available (the browser guard is a substring check, so
  `co_pm_db_test_a1` would pass) but needs a one-time doadmin action; the serialized gate was chosen
  instead. Revisit if a future backlog has more DB-bound parallel work.
- **The live-sibling pointer on the archived attach** — #477 flags it as possibly its own issue; not
  scheduled here.
- **The other nine open issues** (#448, #303, #291, #190, #173, #171, #167, #23, and any later
  arrivals) — outside the requested set.
