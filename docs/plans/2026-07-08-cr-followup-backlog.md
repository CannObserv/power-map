# CR-Followup Backlog Orchestration — 2026-07-08

Tracking issue: #285

## Goal

Clear a six-issue backlog of code-review followups (surfaced mostly by the #275
Phase 2 CR, plus #260 and #276) as a prioritized, parallel-safe batch plan. The
backlog is a classic CR-surfaced shape: naturally disjoint, one concern per
surface. Sequencing is driven by production-stability (correctness first) and one
soft test-infrastructure race, not by any hard file dependency.

## Approved approach

- **Rubric:** standard three-dimension, equal weight —
  `Score = (Foundation × 2) + (Correctness × 2) + Scope`, max 15.
- **Deploy context:** active production, stability-critical → correctness issues
  lead; cleanups gated behind them.
- **Parallelism:** hybrid — parallel within a batch, gate between batches.
- **Worktree ceiling:** **3** concurrent worker agents (generic
  `using-git-worktrees` scripts, no custom port pool; dev server on 8001 is
  manual). Batch A sits exactly at the ceiling.
- **Merge strategy:** batch→main is a **regular merge commit** (`--no-ff`,
  preserves per-agent history, matches the repo's `Merge branch feature/...`
  pattern). Intra-batch worker→batch integration is fixed at FF/regular-merge.
- **Scope:** all six in scope, none deferred. #20 was **closed before planning**
  (see Deferred items).

## Prioritization rubrics

| Dimension | 1 | 2 | 3 |
|---|---|---|---|
| **Foundation Leverage** | Standalone | 1–2 others benefit | Many depend on / simplified by it |
| **Correctness Risk** | Cosmetic / organizational | Edge-case incorrect / runtime-failure risk | Data loss, races, silent failures |
| **Scope Clarity** | Needs design discovery | Clear direction, minor decisions | Mechanical / obvious |

`Score = (Foundation × 2) + (Correctness × 2) + Scope` (max 15). Blast radius
drives sequencing, not score.

## Scored backlog

| Rank | Issue | Title | F | C | S | Score | Blast |
|---|---|---|---|---|---|---|---|
| 1 | #280 | Non-HTMX confirm-mode silently drops address (data loss) | 1 | 3 | 2 | **10** | Low |
| =2 | #277 | Role/role_assignment deletion-propagation gap | 1 | 2 | 1 | **7** | Low |
| =2 | #262 | api_request_log async/buffered writer (off hot path) | 1 | 2 | 1 | **7** | Low |
| =2 | #282 | Remove dead hidden `addr_id` input | 1 | 1 | 3 | **7** | Low |
| =2 | #283 | Audit + optimize integration test suite | 2 | 1 | 1 | **7** | Low |
| 6 | #281 | Normalize Danger Zone interaction model | 1 | 1 | 2 | **6** | Low |

**Rationale for non-obvious scores:**
- **#280 = 10, sole leader.** Only Correctness-3: silent, success-looking data
  loss on a JS-disabled client. In stability-critical prod this is the one that
  risks real user harm.
- **#277 / #262 = Correctness 2.** Neither corrupts data today, but both carry
  runtime/silent-failure *risk* — #277 is a missing retraction signal (subscribers
  never learn of a role deletion; schema self-inconsistency), #262 risks
  lost-writes/ordering if the hot-path move is careless. Scope 1: each needs a
  real decision (#277: extend CHECK vs. document-as-intentional; #262:
  fire-and-forget vs. buffered + gather before/after load evidence).
- **#282 = Scope 3.** Mechanical one-line deletion × 3 partials.
- **#283 = Foundation 2.** Faster suite benefits every future cycle; open-ended
  audit → Scope 1.
- **#281 = 6.** Self-declared "cosmetic/UX, not a correctness bug."

## Conflict zones

**Contested source files: NONE.** Grep-confirmed disjoint footprints:

| Issue | Files edited |
|---|---|
| #262 | `src/api/public/middleware.py` (+ its test) |
| #277 | `src/core/schema.sql` (`deleted_entities` CHECK), `src/api/admin/roles.py`, `src/api/admin/role_assignments.py` |
| #280 | `src/api/admin/{orgs,people,jurisdictions}_addresses.py` (`_maybe_confirm`) |
| #281 | `src/templates/admin/{orgs,people,jurisdictions}/detail.html` + archive/unarchive routes in `src/api/admin/{orgs,people,jurisdictions}.py` |
| #282 | `src/templates/admin/{orgs,people,jurisdictions}/partials/_address_form_row.html` |
| #283 | test infrastructure: `conftest.py`, session-scoped fixtures, isolation strategy |

**Near-misses checked & cleared:**
- **#277 vs #281** — both touch the "roles"/entity surface but in different files
  (#277 = Python delete routes; #281 = detail templates + archive routes). No
  collision. #281 stays scoped to orgs/people/jurisdictions per the issue;
  roles/role_assignments danger zones are out of #281's scope.
- **#280 vs #281** — both cross-entity admin, but `*_addresses.py` vs.
  `*/detail.html` + entity archive routes. Disjoint.

**Soft edge — #283.** The only issue whose real work touches shared test
infrastructure (`conftest.py`, session fixtures). Not a source conflict but a
test-infra race: every other worker adds TDD tests against those fixtures. #283
therefore runs **last and solo**, optimizing the fully-merged suite.

## Dependency graph

```
#280 (data-loss bug) ─┐
#277 (deletion propagation) ─┤
#262 (async log writer) ─┼─ all mutually independent ──▶ #283 (optimize merged suite, solo)
#281 (danger-zone UX) ─┤
#282 (dead addr_id) ─┘
   └─ only soft ordering: correctness batch reviewed before cleanups
      (stability-critical / hybrid preference)
```

No hard file dependencies. Sequencing = (a) correctness-first, (b) #283 last & solo.

## Batch execution plan

| Batch | Issues | Agents | Branch | Gate |
|---|---|---|---|---|
| **A** | #280, #277, #262 | 3 (parallel) | `batch/a` | Start immediately |
| **B** | #281, #282 | 2 (parallel) | `batch/b` | After A merged to `main` |
| **C** | #283 | 1 (solo) | feature branch (no batch branch) | After B merged to `main` |

**Per-batch file coverage (all disjoint within each batch):**
- **Batch A:** #280 → `*_addresses.py`; #277 → `schema.sql` + `roles.py` + `role_assignments.py`; #262 → `middleware.py`. No intra-batch ordering.
- **Batch B:** #281 → `*/detail.html` + entity archive routes; #282 → `*/partials/_address_form_row.html`. No intra-batch ordering.
- **Batch C:** #283 solo.

## Key decisions

- **#262 rides in the correctness batch (A), not with cleanups.** It's a
  production request-path change with lost-write/ordering risk (C2) — grouped for
  careful review, and it keeps Batch A exactly at the 3-agent ceiling.
- **#283 is deliberately last & solo.** It's the only issue that mutates shared
  test infrastructure; running it after every other batch lands avoids a
  `conftest.py`/fixture race and lets it optimize the complete suite. Batch A/B
  workers' TDD tests are the read-only baseline it tunes.
- **No bundling (no Shape A/B pairs).** Every issue is file-disjoint from every
  other, so each is its own agent — no same-file prerequisite/dependent pairs to
  bundle or split.
- **Batch C skips a batch branch** — single agent, so its feature branch serves
  as the review surface directly.

## Deferred items

- **#20** (dup-count cache stale under multi-worker) — **closed before planning**
  as resolved by #210 (commit `07473bb`, "move dup count cache to DB so all
  workers share it"). The module-level dict it described was replaced with a
  DB-backed `dup_count_cache` table explicitly documented as shared across
  workers. No batch slot.

## Out of scope

- Roles / role_assignments **Danger Zone** template normalization — #281 is
  scoped by its issue to orgs/people/jurisdictions only.
- Any Redis / external cache introduction (#20's rejected option) — the DB-backed
  cache already covers it.
