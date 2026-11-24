---
title: "#877 phase 1 — org kinds, the suggestion store, the rules source, the admin Kind panel"
date: 2026-11-24
status: draft
---

# #877 phase 1: org kinds, the suggestion store, rules, and the Kind panel

Design: [`2026-11-24-org-kind-suggestions-design.md`](2026-11-24-org-kind-suggestions-design.md), accepted 2026-11-24. This plan covers its phase 1 only. **No Jev code ships in this phase.**

## Problem

PM has no notion of an org's kind. #877's design (11 kinds; suggestions only; the operator decides) needs four pieces before Jev can be plugged in:
- somewhere to put a kind;
- somewhere to record each source's answer;
- one source that is free and structural (identifier rules);
- a way for an operator to decide.

Phase 2 (Jev) and phase 3 (bulk accept, the review queue, the public API) build on this.

## Approach

**1. Schema.** Reconciled, idempotent DDL in `schema.sql`:
- the `org_kinds` lookup, with 11 rows, seeded through `_seed_org_kinds` and `reconcile_seeded_slugs()`;
- `organizations.org_kind_id`: nullable, FK, added by `ALTER … ADD COLUMN IF NOT EXISTS`;
- `classification_suggestions`, with:
  - CHECKs on `entity_type`, `field`, `source` and `manual_action`;
  - a partial unique index on live Jev answers;
  - a lookup index on `(entity_type, entity_id, field, source, created_at DESC)`.

**2. A core module, `src/core/classification.py`, owns the logic:**
- the identifier → kind rule table;
- the "latest answer per source" read;
- the "decided" rule;
- `record_decision()`. It writes the `manual` row and sets `organizations.org_kind_id` in one transaction. Nothing else writes that column.

**3. A dry-run-by-default CLI** (`scripts/classify_suggestions.py --source rules`). It recomputes rules for all active orgs and inserts a row only when the answer changed. Phase 2 grows it into the Jev drain.

**4. A "Kind" panel on the admin org detail page.** It shows the resolved kind, a provenance badge, and each source's latest answer. Its actions are Accept, Set other, Dismiss and Clear. Each follows the admin mutation rules.

**5. Ancillary wiring,** guarded by the existing sweeps: hard delete, org merge, and the orphan audit.

## Tradeoffs / alternatives

- **Rules written on every identifier write path** (admin, the public API, observations, the applier, the importer, merges). Rejected: six paths with their own sweep tests, against one batch query in the CLI (and, in phase 2, the drainer).
- **A DB trigger to maintain the rules row.** Rejected: it hides the rule table in PL/pgSQL, ties rules versioning to DDL, and fires on bulk applier writes.
- **A separate review page instead of a panel.** It's deferred to phase 3 (the queue). Deciding one org at a time belongs where the org is read.
- **Shipping phase 1 with Jev too.** Rejected: the gold set and its score gate Jev (design D6). The store, the rules and the panel are useful without it. Rules alone suggest kinds for the ~290 orgs with identifiers, and the operator can set any org's kind.

## Steps

1. **Schema (TDD, integration).** Covered by these tests:
   - all 11 kinds are seeded;
   - re-applying the schema is a no-op;
   - the CHECKs reject a bad `source`, `field` or `manual_action`, and a `manual_action` on a non-`manual` row;
   - the partial unique allows a second Jev row only after the first is marked `ignored_at`;
   - the FK holds.

   The seed-reconciliation sweep and the schema-parity audit stay green. *Done when* `apply-schema.sh --test` applies twice cleanly and those tests pass.
2. **`classification.py` (TDD).** Tests:
   - unit tests on the rule table, including conflicting identifiers → `none` with a note;
   - integration tests on the latest-answer read;
   - the decided rule (accept, set and dismiss decide; clear un-decides);
   - `record_decision()` (writes the `manual` row and sets or clears `org_kind_id`, atomically; refuses an unknown kind).

   *Done when* the module's tests pass and no other module writes `org_kind_id`. A sweep test greps `src/` and `scripts/` to enforce that.
3. **Ancillary wiring (TDD):**
   - `delete_entity_ancillary` drops an org's suggestion rows;
   - `_execute_merge` (`orgs_merge.py`) moves the loser's `manual` rows when the survivor is undecided, and deletes them otherwise. It deletes the loser's `rules` and `jev` rows. The survivor keeps its `org_kind_id`, or takes the loser's when it has none;
   - `_ORPHAN_SCOPES` (the orphan audit) and `tests/polymorphic_seeders.py` cover the new table.

   *Done when* the existing ancillary, merge and orphan sweeps pass, along with new merge tests for both survivor states.
4. **CLI `scripts/classify_suggestions.py --source rules` (TDD):**
   - built on `build_parser` / `add_dsn_args`;
   - echoes its target and runs dry by default; `--execute` writes;
   - idempotent: a second run inserts nothing;
   - a removed identifier writes `none` once.

   *Done when* the script's tests pass, and a dry run against production (read-only, reported here) gives counts by kind.
5. **The admin Kind panel (TDD).**
   - **Routes:** a GET partial, plus POST accept, set, dismiss and clear, each with `user: AdminUser = Depends(get_admin_user)`.
   - **Responses:** an HTMX partial plus a `with_flash` fallback. DB values are escaped.
   - **Display:** the provenance badge says Rules or Reviewed (AI arrives in phase 2).
   - **Bookkeeping:** `API_ROUTE_COUNT` goes up. Any `error`-level flash gets its `test_flash_levels.py` entry.
   - **Tests:** endpoint tests through the rollback client; `test_mutation_fallback_sweep.py` and `test_js_required_policy.py` stay green; the a11y tier-1 rules from `docs/ACCESSIBILITY.md`.

   *Done when* those tests pass and the panel works on the dev server (port 8001), with a screenshot of each state.
6. **Docs:**
   - `docs/SCHEMA.md`: the two tables, the column, and the "only `record_decision` writes it" rule;
   - `docs/ADMIN_PANELS.md`: the Kind panel;
   - `docs/AUDITS.md`: the orphan scope;
   - `docs/RUNBOOKS.md`: the CLI.

   `SCHEMA_INDEXES.md` has 241 tokens of headroom left, so nothing goes there. *Done when* the context-budget check reports no OVER.
7. **Version, gate, PR, CR, pause.**
   - Bump to `0.78.0` (minor: new tables and an admin surface) and regenerate the client. The diff should be version-only, since the public schema is unchanged until phase 3.
   - Run pre-commit, then the integration tier on its own.
   - Open the PR with no closing keywords, run the code review, and fix or record each finding.
   - **Pause for authorization to merge, deploy and restart.**
8. **After deploy, supervised:** run `classify_suggestions.py --source rules` as a dry run, then `--execute` once the operator approves. Report the counts by kind on #877, and tag `v0.78.0` at the deployed `main`.

## Open questions / risks

- **A change from the design, needing the operator's OK:** design D3 has rules write `none` for every org with no rule, which would be ~1,380 rows of noise. This plan writes `none` **only** when it replaces an earlier non-`none` rules answer (an identifier removed), or when identifiers point to different kinds. Absence of a rules row means "no rule".
- **Org merges outside the admin:** if any script or API path merges orgs without calling `_execute_merge`'s ancillary step, the orphan audit catches it. Step 3 checks for such paths.
- **The admin org detail page** gains about three small queries: kind, latest answers, and the catalog.
- **The schema-parity timer** (daily) must see the new CHECKs and indexes as named, reconciled objects (#307/#312). Step 1's DDL follows the reconciliation `DO` block convention.
