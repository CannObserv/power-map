---
title: "#877 phase 2 — Jev suggestions: client, drainer, timer, health panel, gold set"
date: 2026-11-26
status: draft
---

# #877 phase 2: Jev org-kind suggestions and the gold set

Design: [`2026-11-24-org-kind-suggestions-design.md`](2026-11-24-org-kind-suggestions-design.md) (accepted), D4, D5, D6 and D8. Phase 1 (v0.78.0) shipped the store, the rules source and the Kind panel. **Jev ships with its flag off, and stays off until the gold-set report is approved.**

## Problem

Rules suggest a kind for 235 orgs. The other ~1,430 have no suggestion, and #303's 193 ambiguous role titles wait on them. Phase 2 adds Jev as a second source, then measures it before anyone acts on it.

## Approach

Two PRs, because the gold set needs operator time and the code doesn't need the gold set:

**PR 2a: the plumbing (flag off).**
- **One question builder,** shared by production and the gold set, so what is scored is exactly what runs. It builds the context line, the org's state and the criteria (`org_kinds.description`).
  - **State:** the display name, the display names of its identifier types, and up to ~300 characters of its active roles' titles. No person names.
- **A transport client,** modelled on the tracker's `jev_client.py`: plain `httpx`, 25 orgs per request, 429/529 retried with backoff, 401/403 not retried, a malformed reply stores nothing, the key never logged.
- **A drain** in `classification.py`: rules first, then Jev for active orgs with no live answer for the pinned model, capped per run, under an advisory lock.
- **Three ways to run the drain:** an in-process drainer (woken on org creation, plus a tick), the CLI, and a daily timer that checks in to co-status.
- **A health panel** on the dashboard, plus an "AI · NN %" badge and a Re-ask button on the Kind panel.

**PR 2b: the gold set and its score.** About 300 orgs, sampled and labelled by me without Jev, then reviewed by you. Only `(org_id, kind)` is committed. The vendored harness scores it, and the report goes on #877 with a proposed trust set.

## Tradeoffs / alternatives

- **One PR for all of phase 2.** Rejected: the label review would hold up code that can ship dark.
- **The gold set first.** Rejected: scoring has to use the production question builder, or it scores a different question.
- **A timer only, no in-process drainer.** Simpler, but the design asks for suggestions within minutes of creation. Kept as the fallback: the drainer is a small task, and the timer covers it if it dies.
- **Recording health by scraping logs.** Rejected for a small `classification_runs` table (see the open questions). The panel needs "last success" and "failures in 7 days" from both processes.

## Steps

1. **Question builder (TDD).** `src/core/org_kind_question.py`: `build_question(kinds)`, `build_state(row)` and `load_states(db, org_ids)`.
   - **Tests:** the role-title cap; no person-name columns are read; the state is stable for the same input; the criteria are exactly `org_kinds.description`.
   - It stays WA-free: the context line is generic, and identifier type names come from data.
   - *Done when* these tests and `test_src_core_wa_free` pass.
2. **Jev client (TDD).** `src/core/jev_client.py`, tested against `httpx.MockTransport` only; no test calls the API.
   - **Config,** read at call time: `JEV_SUGGESTIONS_ENABLED`, `TYPESAFE_API_KEY`, `JEV_MODEL` (default `jev-1.14.0`), `JEV_CONFIDENCE_THRESHOLD` (0.7), `JEV_MAX_PER_RUN` (2000).
   - **Tests:**
     - batching;
     - retry on 429/529 and not on 401/403;
     - a choice that isn't an option fails the batch;
     - tokens are split across the batch;
     - the key appears in no log line or error.
3. **Runs table and the drain (TDD, integration).**
   - **Schema:** a reconciled `classification_runs` table (trigger, status, orgs asked, rows written, tokens, a short error, timestamps).
   - **`drain(db, client, *, trigger, limit)`:**
     1. run rules;
     2. take `pg_try_advisory_lock`, or return "busy";
     3. select the backlog;
     4. classify in batches;
     5. write the `jev` rows (`value`, `confidence`, `probabilities`, `model`, `input_tokens`);
     6. record one run row whenever it called Jev or failed. An idle tick writes nothing.
   - **Re-ask:** marks the live row `ignored_at` and inserts the new one in one transaction (the partial unique holds).
   - **Tests:**
     - flag off → rules only, no calls;
     - a failed batch stores nothing and the next drain retries it;
     - the lock blocks a second drainer;
     - a re-ask on a decided org never changes `org_kind_id`.
   - The write sweep still finds only `record_decision` and `carry_kind_on_merge` setting it.
4. **In-process drainer and wake hook (TDD).**
   - `wake()` sets an event and returns. The drainer task is started in the lifespan when a pool exists, and cancelled before the pool closes.
   - **Timing:** a ~5 s debounce after a wake, so the creating transaction has committed; otherwise a 15-minute tick.
   - **Where `wake()` is called:** after the transaction in the admin org create route, and in each public route whose `resolve_entity` can create an organization. A sweep test finds those routes, so a new one can't be missed.
   - **Out-of-process creators** (the applier, the importer, scripts) rely on the tick and the timer.
   - **Exception:** the drainer acquires its own pool connection, the documented exception to the `Depends(get_db)` rule. `docs/SCHEMA.md` records it, beside the request-log writer.
   - **Tests:** drive the task directly with a fake client. Endpoint tests stay lifespan-less.
5. **CLI and timer.**
   - `scripts/classify_suggestions.py --source rules|jev|all`, plus `--refresh-model` and `--ids-file`. Refreshes skip decided orgs.
   - **Dry run** prints the backlog and estimated tokens; `--execute` drains. It respects the flag.
   - **Units:** `infra/power-map-classification.{service,timer}`, daily, `--source all --execute`. It writes suggestions only, never a kind.
     - Exit 3 (`jev_failed`) when the flag is on and a batch, the key or the API failed.
     - Check-in per #566: a new co-status monitor, its id in the unit, `SetCredential=status-checkin:\n`.
   - **Registry and docs:** `installed.toml` and the infra-drift test; the timer roster in `docs/COMMANDS.md`, `docs/AUDITS.md` and `docs/ALERTING.md`.
6. **Admin (TDD).**
   - **Dashboard health card:**
     - whether Jev is enabled and the key present (shown as yes/no, never the key);
     - the last successful run;
     - the backlog;
     - failures in the last 7 days;
     - tokens, all time and this month.
   - **Kind panel:**
     - the AI row shows `NN %`, and the badge reads "AI" when a decision accepted the `jev` source;
     - **Re-ask Jev** is a POST route: one attempt, a 20 s timeout, and the HTMX partial plus a `with_flash` fallback. It is disabled with its reason shown when the flag is off.
   - **Bookkeeping:** `API_ROUTE_COUNT` +1, the mutation and JS-required sweeps, the flash-levels entries, axe on the panel, and a screenshot of each new state.
7. **Ship PR 2a.**
   - Bump to `0.83.0` and regenerate the client; the diff should be version-only.
   - Run pre-commit, then the targeted integration modules alone.
   - Open the PR with no closing keywords, run the code review, and pause for authorization to merge, deploy and restart.
   - **Your steps at deploy:** `TYPESAFE_API_KEY` goes in `/etc/power-map/.env` with the flag **off**, and the timer is installed. Its first run is rules-only; I report it.
8. **Gold sample and labels (PR 2b; no Jev calls).**
   - **The sample:** `scripts/org_kind_gold.py sample` draws ~300 orgs read-only, in strata (no identifier, ambiguous-title, each identifier stratum, recent). It orders them by `md5('gold-877:' || id)`, so the salt keeps it away from the spike's 61.
   - **The worksheet:** names, identifiers and titles, written **outside any checkout**. I draft a kind for each row from D1's definitions and flag the ones I'm unsure of.
   - **Your review:** the unsure rows plus a seeded random 30, in that private worksheet.
   - **Committed:** `docs/research/org-kind/gold.csv` (`org_id,kind`) and a README describing the method.
9. **Score (PR 2b).**
   - `scripts/org_kind_gold.py eval-set` joins the gold set to live state through step 1's builder, into a JSONL set outside the checkout. `typesafe-eval.py` scores it with `TYPESAFE_OPT_IN=on` for that run only, at about 0.4 M tokens.
   - The same script scores rules against the labels.
   - **The report on #877:** aggregates only. Per-kind precision at 0.7, answer counts, the confusion matrix, and rules accuracy, plus a **proposed trust set** (kinds clearing ≥ 90 % on ≥ 20 answers).
   - **Pause** for your approval of the report and of turning the flag on.
10. **Flag on, supervised.** Your edit to `/etc/power-map/.env`, and a restart you authorize. Then a CLI dry run (the backlog and estimated tokens), and `--execute` with your OK (~1,430 orgs, ≈ $0.05). I report the counts by kind and the confidence spread on #877. Phase 3 (bulk accept, the queue, the public API) follows.

## Open questions / risks

- **A `none` option in the Jev question (my call, correct it):** D1 has no `unknown` kind, but D5's review queue lists "a jev `none`". I'd add an option, `none` ("not an organization, or no kind fits"), for junk rows such as person names filed as orgs. The gold set can then label `none` too. The cost is one more option for Jev to confuse.
- **A new `classification_runs` table (a small schema addition the design didn't name).** It is the health panel's source, written by both the API process and the timer. Its rows are never pruned at this volume (only runs that call or fail).
- **Spend on the health card:** TypeSafe's per-token price isn't in the repo. I'd show tokens only, unless you want a `JEV_USD_PER_MTOK` knob.
- **Gold-set bias:** I labelled the spike's 61 orgs after seeing Jev's answers. The salted ordering avoids most of them but can't guarantee it, because the spike's ids are gone with the scratchpad. The report will say so.
- **Small kinds** (`tribe`, `labor_union`, `academic`, `political_party`) may have fewer than 20 orgs in all of PM. They then fail closed for Jev by the bar, and their orgs go to review. That matches decision C for rules.
- **Your time:** roughly the unsure rows (my guess, 40–60) plus 30 random ones, checked against public sources.
- **A blocking call in an async handler:** the Re-ask route awaits `httpx.AsyncClient`, never a sync client. Guarded by ruff `ASYNC`.
