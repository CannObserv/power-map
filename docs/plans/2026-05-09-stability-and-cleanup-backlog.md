# Stability & Cleanup Backlog — 7-issue parallel clearance

**Date:** 2026-05-09
**Issues:** #122, #124, #125, #132, #133, #134, #136

## Goal

Clear seven open issues that surfaced during recent #131 / #135 work — a mix of pre-existing test-suite failures, a schema-drift bug, a DOM-ID collision audit, an env-leak fix, a cache-bust strategy change, a Vitest-conventions cleanup, and trigger-coverage backfill. Restore a green integration suite and remove latent footguns before the next round of feature work.

## Approved approach

Single 6-agent parallel batch (`batch/a`). #124 is bundled into #136's first commit (same root cause). All other issues touch disjoint files, so no intra-batch gating is required. The orchestrator pre-creates `batch/a` from a freshly-synced `main`, spawns all 6 worker agents simultaneously with `isolation: "worktree"`, runs the full test suite once all merge, and hands the batch branch off to the user for review.

**Merge strategy:** regular merge commit (`git merge --no-ff batch/a`) into `main`, preserving per-agent commit history.

## Prioritization rubrics

| Dimension | 1 | 2 | 3 |
|---|---|---|---|
| **Foundation Leverage** | Standalone improvement | 1–2 other issues benefit | Multiple issues depend on or are simplified by this |
| **Correctness Risk** | Cosmetic / organizational | Edge-case incorrect behavior, runtime failure risk | Data loss, race conditions, silent failures |
| **Scope Clarity** | Requires design discovery | Clear direction, minor decisions needed | Mechanical — implementation is obvious from the issue |

**Score formula:** `(Foundation × 2) + (Correctness × 2) + Scope`, max 15.

**Blast radius** (intra-issue file count) drives sequencing decisions, not score.

**Interview agreements:**
- Quality lens: equal weighting; let blast radius decide sequencing
- Deployment context: pre-production (runway to refactor freely)
- Scope: all 7 issues in play
- Parallelism: maximize parallel agents, worktrees for isolation
- Merge: regular merge commit

## Scored backlog

| # | Issue | F | C | S | **Score** | Blast |
|---|---|:-:|:-:|:-:|:-:|---|
| **#136** | test: integration suite has 15 pre-existing failures | 3 | 3 | 1 | **13** | HIGH |
| **#133** | chore: cache-bust admin static assets with commit hash | 2 | 2 | 2 | **10** | HIGH (top-level admin templates) |
| **#134** | test: revise Vitest suite to vi.fn / vi.spyOn + STYLE.md | 2 | 2 | 2 | **10** | Med (tests/js/ + docs) |
| **#124** | bug: dedup script references non-existent `links.is_canonical` | 1 | 3 | 2 | **10** | Low (subset of #136) — **bundled into #136** |
| **#125** | audit: admin templates for combobox / DOM-ID collisions | 1 | 3 | 1 | **9** | HIGH (admin form-row partials) |
| **#132** | test: address normalizer fails when env leaks | 1 | 2 | 3 | **9** | Low |
| **#122** | test: backfill `updated_at` trigger coverage (5 tables) | 1 | 1 | 3 | **7** | Low |

## Conflict zones

**No contested files between issues.** This backlog is unusually parallelizable.

| Issue | Files touched |
|---|---|
| **#136 + #124** | `src/core/ingestion/pipeline.py`; `src/core/ingestion/sources/csv_{org,person,role}.py`; `scripts/deduplicate_roles.py`; `tests/scripts/test_deduplicate_roles.py`; `tests/core/ingestion/test_pipeline.py`; `tests/api/admin/{test_orgs,test_orgs_duplicates,test_people_duplicates,test_roles}.py`; likely `tests/api/admin/conftest.py` |
| **#133** | `src/api/main.py` (Jinja `asset_version` global); `src/templates/admin/base.html`; `src/templates/admin/people/detail.html`; `src/templates/admin/orgs/detail.html`; admin `<link>` tags for `admin.css` |
| **#134** | All 8 `tests/js/*.test.js` (refactor); `docs/STYLE.md` (new "Vitest test conventions" section) |
| **#125** | `src/templates/admin/people/partials/_{assignment,name}_form_row.html`; `src/templates/admin/orgs/partials/_{parent,child}_form*.html`; `src/templates/admin/orgs/_merge_search_modal.html`; `src/templates/admin/roles/partials/_{org,assignment}_form_row.html`; new `tests/js/typeahead-row-key-collision.test.js` |
| **#132** | `tests/core/normalizers/conftest.py` (new autouse fixture); `tests/core/normalizers/test_address.py` |
| **#122** | `tests/core/test_schema.py` |

**Why #133 and #125 don't collide on admin templates** — #133 modifies *top-level* templates (`base.html`, `detail.html` — the only places `?v=` appears); #125 modifies *partial form-row templates* (no script tags). Verified by `grep -rn "?v=" src/templates/`.

**Why #134 and #125 don't collide on `tests/js/`** — #134 modifies the 8 existing files; #125 adds a new file.

**Why #122 and #136 don't collide on tests** — #122 only touches `tests/core/test_schema.py`; #136's test work is in `tests/api/admin/` and `tests/core/ingestion/`. If #136 widens a shared rollback fixture, #122's new tests inherit it as a strict superset (no conflict).

## Dependency graph

```
(no edges — all 6 nodes are independent)

  #136+#124  ──  HIGH-blast: ingestion + scripts + admin/api tests
  #133       ──  HIGH-blast: top-level admin templates + Jinja globals
  #125       ──  HIGH-blast: admin form-row partials
  #134       ──  MED-blast: tests/js/ + STYLE.md
  #132       ──  LOW-blast: tests/core/normalizers/
  #122       ──  LOW-blast: tests/core/test_schema.py
```

The "blast" labels describe intra-issue scope, not contention with other agents.

## Batch execution plan

### Batch A — 6 parallel agents · gate: start immediately

| Slot | Branch | Issues | Files | Self-review checks |
|---|---|---|---|---|
| **A1** | `feature/batch-a-136-suite-failures` | **#136 + #124** | `src/core/ingestion/pipeline.py`; `src/core/ingestion/sources/csv_*.py`; `scripts/deduplicate_roles.py`; `tests/scripts/test_deduplicate_roles.py`; `tests/core/ingestion/test_pipeline.py`; `tests/api/admin/{test_orgs,test_orgs_duplicates,test_people_duplicates,test_roles}.py`; `tests/api/admin/conftest.py` | Full integration suite green (`uv run pytest -m integration -p no:randomly`); `is_canonical` retained on org/acronym/person_name writes, removed on `links` writes only |
| **A2** | `feature/batch-a-133-cache-bust` | **#133** | `src/api/main.py` (Jinja globals + `asset_version`); `src/templates/admin/base.html`; `src/templates/admin/people/detail.html`; `src/templates/admin/orgs/detail.html`; admin `<link rel="stylesheet">` for `admin.css` | Asset URLs use `?v={{ asset_version }}`; commit-hash injection works; timestamp fallback for no-git context; `admin.css` is now cache-busted |
| **A3** | `feature/batch-a-134-vitest-conventions` | **#134** | All 8 `tests/js/*.test.js`; `docs/STYLE.md` | `npm test` green; listener-cleanup pattern applied uniformly; STYLE.md updated alongside code |
| **A4** | `feature/batch-a-125-dom-id-collision` | **#125** | 7 partial templates (audited individually); new `tests/js/typeahead-row-key-collision.test.js` | JS test demonstrates two forms in same DOM bind independently; singleton-only forms documented in commit msg |
| **A5** | `feature/batch-a-132-env-leak` | **#132** | `tests/core/normalizers/conftest.py` (new autouse); `tests/core/normalizers/test_address.py` | Test passes both with and without `ADDRESS_VALIDATOR_RUN_VALIDATION=true` in env |
| **A6** | `feature/batch-a-122-trigger-coverage` | **#122** | `tests/core/test_schema.py` | New per-binding tests pass for `addresses`, `people`, `roles`, `role_assignments`, `app_users`; matches existing `_org`/`_person` helper style |

### Gate

- **Start immediately** after `batch/a` is created from synced `main`
- All 6 agents merge into `batch/a` as they signal (each agent's `isolation: "worktree"` auto-merges to the orchestrator's current local branch — see *Branch hygiene*)
- Once all 6 merged: orchestrator runs `uv run pytest -m integration -p no:randomly` + `npm test` against `batch/a`
- Hand off to user for review of `batch/a` as a whole
- On approval: `git checkout main && git pull --ff-only && git merge --no-ff batch/a && git push origin main`

## Key decisions

1. **#124 bundled into A1, not its own slot.** Same root cause as #136 section (A) — both fix the dropped `links.is_canonical` column. A separate agent for #124 would cause branch contention on `scripts/deduplicate_roles.py`. A1 closes #124 as part of its first commit.

2. **A1 owns all three #136 sub-fixes (schema drift / test isolation / FK violation) in one agent.** The schema-drift fix reshapes test fixtures in `tests/api/admin/conftest.py` that the isolation fix also touches. Single owner avoids intra-issue conflict.

3. **#136(A) fix is surgical, not blanket.** `is_canonical` was retained on `organizations.names`, `organization_acronyms`, `person_names` — only dropped from `links`. The agent must remove `is_canonical` from `links` writes/reads only, preserving the column on the other tables. Verified via `grep -rn "is_canonical" src/ scripts/`.

4. **#133 and #125 run in parallel despite both being "HIGH blast" admin-template work.** Their high-blast files don't overlap: #133 touches *top-level* templates (where `?v=` lives), #125 touches *partial* form-row templates (no script tags).

5. **No correctness-fix-leads-refactor sequencing required.** Unlike past batches where a bug fix had to head a refactor's commit chain (e.g. #25 leading #14's tasks.py refactor), no contested file exists here. Every agent owns its scope cleanly.

## Deferred items

None — all 7 issues are in scope for this batch.

## Out of scope

- Restoring `is_canonical` on the `links` table. The schema comment at `src/core/schema.sql:340-343` records the deliberate retirement of that column. The fix is to remove the references from product/test code, not restore the column.
- Refactoring SUT scripts in `src/static/admin/` to expose explicit init/teardown hooks for testing (would change runtime behavior; #134's listener-cleanup convention is sufficient without it).
- Singleton-form templates in #125's audit list (e.g. `_parent_form.html`) — they're guaranteed-singleton per `_parent_form` semantics, so no row-key suffix is needed. Audit must document this conclusion in the commit msg.

## Branch hygiene reminders for the orchestrator

(From the skill's branch-hygiene rules — included here so the orchestrator doesn't have to re-read the skill mid-execution.)

1. **Sync local `main` before launching the batch:** `git checkout main && git pull --ff-only`. Worktree agents branch from local `main`; if it's stale they silently base their work on the wrong commit.
2. **Check out `batch/a` *before* spawning agents.** `isolation: "worktree"` merges agent output to the orchestrator's *current local branch* — checking out `batch/a` first routes all 6 agents' work onto the batch branch instead of `main`.
3. **Never use `git push origin HEAD:main` from a feature branch** to advance main. Always push from local `main` after a fast-forward / merge / rebase.
4. **If a rebase conflict generates a verbose auto-message, fix it immediately with `git commit --amend` before continuing the rebase** — amending the wrong commit later requires a `reset --soft` recovery.
