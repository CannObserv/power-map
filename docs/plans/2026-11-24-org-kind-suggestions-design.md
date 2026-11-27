---
title: Org-kind classification with Jev suggestions + a shared suggestion store (#877)
date: 2026-11-24
status: accepted (operator, 2026-11-24)
---

# Org kinds, suggested by rules and Jev, decided by an operator (#877)

## Intent

PM can't tell what kind of organization an org is, and only 292 of 1,670 orgs carry an identifier that hints at it. The gap blocks #303's role classification. "Member" of a city council isn't `org_member`; "Member" of a trade association is. 193 roles with ambiguous titles (on 173 orgs) wait on it.

This issue adds:
1. a list of org kinds;
2. **suggestions** for each org's kind, from identifier-derived rules and from TypeSafe Jev;
3. an operator decision that sets the kind;
4. the **shared suggestion store** that #303's role-type suggestions reuse.

Step 2 of #303's approved order (operator, 2026-11-24). Precedent: the WSLCB Licensing Tracker's Jev entity classification (its `docs/specs/2026-10-22-jev-entity-classification-design.md`, `jev_client.py`, `docs/research/person-vs-org-classification.md`).

**Goals**
- Every org can carry a kind, and every kind shows where it came from.
- Suggestions arrive within minutes of an org's creation, with no effect on the write path.
- Bulk acceptance happens only for slices that a gold set has shown to be accurate.

**Non-goals**
- Role-type suggestions themselves: the rest of #303, which reuses this store.
- Enforcing which role types are allowed on which org kinds (#266's `applies_to_org_type`). Revisit once kinds are settled.
- Org-to-org membership.

**Anti-goals** (the tracker's ranking, adapted)
1. **An automated pass sets or changes an org's kind.** Only an operator does, one org at a time or by a bulk-accept script they run.
2. Jev stops working and nobody notices.
3. Nobody can tell where a kind came from.
4. The review queue grows past trusting.
5. Jev ends up on a write path, or spend creeps.

## Feasibility spike (2026-11-24, throwaway, scratchpad only)

61 orgs, deterministically sampled across these strata:
- untyped orgs with no identifier;
- orgs holding ambiguous titles;
- the structural strata: legislature, party, WSLCB, PDC committee and PDC.

I labeled them against the draft list of kinds below (the structural strata labeled themselves), then scored them through the vendored `typesafe-eval` harness. Settings: `jev-1.14.0`, unbatched, one Choice question, `TYPESAFE_OPT_IN=on` for the run only. No repo opt-in was set, and nothing was written inside a checkout.

| state sent | correct | at confidence ≥ 0.9 | tokens/org |
|---|---|---|---|
| name only | 57/61 (93 %) | 47/48 | ~740 |
| name + the titles of roles held there | **58/61 (95 %)** | **50/51** | ~755 |

**The errors, and what each means for the design:**
- **A PAC that shares its name with a trade guild** (Jev said `association` at 0.93). Its PDC-committee identifier gets it right. So identifier rules sit beside Jev, and **a disagreement between them goes to review**.
- **A state "commission" that is an executive agency** (`public_body` at 0.62–0.66). The line between `government_agency` and `public_body` is fuzzy, **so the two are merged into `government`** (D1).
- **A brand-like business name** (`nonprofit` at 0.37–0.43). It's genuinely ambiguous and lands below the threshold, so it goes to review.
- **A think tank** (`coalition` at 0.75 from the name alone) is fixed once role titles are sent. **So the state includes role titles.**

**Limits:** 61 rows, labeled by me, one run, unbatched. This shows Jev is viable; it isn't the gold-set validation (D6).

## Approaches

**A. A kind column, an append-only suggestion store, operator acceptance (recommended).**
- The org's resolved kind is set only by an operator action.
- Every source's answer is a row in one store shared with roles.
- This is the tracker's model minus its auto-resolve, as the operator chose on #303.

**B. A suggestion column on `organizations`** (`suggested_kind`, `suggested_confidence`). Rejected:
- one source per column, no history;
- no comparing rules with Jev;
- nothing for roles to reuse.

**C. Suggestions in files only** (a CSV per run). Rejected: the operator asked for suggestions at creation time and for operator overrides in the app.

## Decisions

### D1. The list of kinds: a seeded `org_kinds` lookup (11 kinds, decided 2026-11-24)

`org_kinds(id, slug UNIQUE, display_name, description, created_at)`. It is seeded through `_seed_org_kinds` and `reconcile_seeded_slugs()` (#458), and the slugs are flat.

**The list (operator, 2026-11-24).** `association` and `coalition` stay separate. The spike's `government_agency` and `public_body` are merged into **`government`**: the two overlap too much to label consistently (an executive department whose head is appointed, a commission that runs programs). Telling a public seat from staff is settled at the role, not the org: a commission's "Member" is a public seat, and an agency's "Director" is `org_executive` (#303). **One kind per org.** When kinds overlap, **choose the most specific**: an association organized as a nonprofit is `association`, and a coalition run by a nonprofit is `coalition`.

| slug | covers |
|---|---|
| `business` | for-profit company: store, farm, brand, licensee, law/consulting/lobbying firm, lab |
| `nonprofit` | charity, foundation, advocacy group, think tank, civil-rights or community organization |
| `association` | trade, industry or professional association; members are businesses, practitioners, or governments or their officials |
| `coalition` | coalition, alliance, network or campaign of several organizations or community members |
| `labor_union` | union, union local, or federation of unions |
| `government` | any government entity other than a legislature: an executive department, agency, office, regulator, public utility, prosecutor or military unit, and an appointed or elected board, commission, council, task force or advisory committee, including a city or county council and its committees (a legislative task force is a legislature) |
| `legislature` | a state or national legislature, its chambers, committees, caucuses, task forces and work groups |
| `political_party` | a party or its state, county or district organization |
| `campaign_committee` | a PAC, or a candidate or ballot-measure committee |
| `tribe` | a tribal nation, band or government, or a tribal agency |
| `academic` | a university, college, school, or academic research center or lab |

There is no `unknown` slug: a NULL kind means "not decided". The Jev criteria state the most-specific rule. The Jev criteria text is the `description` column. Any change to it means re-scoring the gold set (D6). Amended 2026-11-27 with the gold set's class rulings (`docs/research/org-kind/README.md`).

### D2. Resolved value: `organizations.org_kind_id`

- **The column:** nullable, with an FK to `org_kinds`.
- **Who writes it:** **only** an operator action (D5) or the bulk-accept script (D7). Each write records a `manual` row in the store (D3).
- **No other path sets it:** not observations, the usa-wa applier, or the importer. The kind belongs to PM, so the curation overlay doesn't apply.
- **Merges and lifecycle:**
  - **Merge:** the survivor keeps its own kind. If it has none, it takes the loser's. A merge never overwrites a kind (MERGE.md data contract).
  - **Archive:** an archived org keeps its kind.
- **Schema change:** the column arrives by `ALTER TABLE … ADD COLUMN IF NOT EXISTS` with its FK. It adds no unique index, so the #816 parity hazard doesn't apply.

### D3. The shared suggestion store: `classification_suggestions`

One append-only table for every classified field, organizations first and roles under #303:

| column | meaning |
|---|---|
| `id` | ULID |
| `entity_type`, `entity_id` | `organization` / `role`. Polymorphic, with no FK (the ancillary convention). |
| `field` | `org_kind` / `role_type` (CHECK) |
| `source` | `rules` / `jev` / `manual` (CHECK) |
| `value` | a slug in the field's vocabulary; `none` when a `rules` or `jev` source finds no kind (rules: no identifier rule, or identifiers that point to different kinds, with the conflict in `note`); NULL on a `manual` dismiss or clear |
| `manual_action` | `manual` rows only: `accept` / `set` / `dismiss` / `clear` (CHECK; NULL for other sources). An entity is **decided** when its latest `manual` row is accept, set or dismiss. `clear` returns it to undecided and back to the queue. |
| `confidence`, `probabilities` | Jev only: the chosen option's confidence, and all option probabilities as JSONB (to re-threshold later without re-asking) |
| `model` | `jev-1.14.0`; the rules version (`rules-1`); NULL for `manual` |
| `input_tokens` | Jev only: this entity's share of its batch |
| `ignored_at` | rollback or re-ask marker. A rollback marks every row of one model or rules version. A re-ask marks the entity's current `jev` row before inserting the new one. Only a merge or a hard delete removes rows. |
| `created_at`, `created_by`, `note` | `created_by` is the admin's email on a `manual` row |

- **Latest row per `(entity, field, source)`** is that source's current answer.
- **Classify once:** a partial unique index on `(entity_type, entity_id, field, model) WHERE source='jev' AND ignored_at IS NULL` allows one live Jev answer per model. A re-ask marks the old row `ignored_at` first, in the same transaction. It's a new table, so there are no existing rows to collide with.
- **The `manual` row is the audit record:** who, when and what, with an optional `note`. There's no separate audit log.
- **Ancillary rules:**
  - joins `delete_entity_ancillary` (#605);
  - on an org or role merge, the loser's `manual` rows **move** to the survivor when the survivor has no decision, and are deleted otherwise. The loser's `rules` and `jev` rows are **deleted**: they describe an identity that no longer exists. The survivor's rules are recomputed on the next drain, because its identifiers changed;
  - covered by the daily ancillary-orphans audit.
- **Wire:** never exposed on the public API.

### D4. Sources

- **`rules` (inline, free).** Derived from identifiers:
  - `org_wa_legislature*` → `legislature`
  - `org_wa_party` → `political_party`
  - `org_wa_pdc_committee` → `campaign_committee`
  - `org_wslcb` → `business`

  `org_ubi` and `org_wa_pdc` say nothing. A UBI is held by nonprofits too, and PDC lists lobbyist employers of every kind.

  **Where rules run:** in the drainer and the CLI (D8), never on the identifier write paths (admin, the public API, observations, the applier, the importer, merges). Those paths stay untouched. Each drain recomputes rules for every active org in one query and inserts a row only when the answer differs from the latest `rules` row. So an identifier change shows up within one tick.
- **`jev` (asynchronous).** One Choice question over D1's slugs.
  - **State:** a context line, the org's display name, the kinds (not the values) of its identifiers, and up to ~300 characters of the titles of active roles held there. **No person names are sent.**
  - **Requests:** 25 orgs per request, criteria per question, and a pinned model (the `JEV_MODEL` default is `jev-1.14.0`). 429 and 529 responses are retried with backoff. A batch that fails stores nothing and is retried on the next run.
  - **Transport:** in `src/core/jev_client.py`, modelled on the tracker's (plain `httpx`, the key never logged).
  - **Re-asking:** only by explicit refresh (D8).

### D5. Operator control (admin)

- **Org detail: a "Kind" panel.**
  - It shows the resolved kind, a provenance badge (Rules / AI · NN % / Reviewed), and each source's latest answer side by side.
  - **Actions:**
    - **Accept** (one of the sources' answers): writes a `manual` row (`accept`) and sets `org_kind_id`.
    - **Set other…:** the same as Accept, with a free choice of kind.
    - **Dismiss:** a `manual` row (`dismiss`, value NULL), which closes the item and leaves the kind unset.
    - **Clear:** a `manual` row (`clear`, value NULL), which sets `org_kind_id` to NULL and returns the org to the queue.
    - **Re-ask Jev:** one synchronous call. A second opinion that can never win over a `manual` decision. It respects `JEV_SUGGESTIONS_ENABLED`: with the flag off, the button is disabled and says why.
  - Every mutation follows the admin rules: an HTMX partial plus a `with_flash` fallback, `get_admin_user`, and escaped DB values. An `error`-level flash needs its entry in `test_flash_levels.py`. If the panel is pinnable, it declares `labels=` and `editable=`, and its label isn't "Current" (#838).
- **Review queue** at `/admin/classification/review`, filterable by `field`. It lists entities with no `manual` decision that have any of:
  - a disagreement between `rules` and `jev`;
  - a `jev` answer below the threshold (default 0.7);
  - a `jev` `none`.

  Sorted with disagreements first, then by confidence ascending. Each item has the panel's one-click actions, and the count shows on the dashboard.
- **Health panel** on the dashboard: enabled or disabled, last successful call, the backlog awaiting Jev, failures in the last 7 days, and tokens and spend (all time and this month).

### D6. Gold set and the go-live bar

- **Sample:** ~300 orgs, stratified (no identifier, ambiguous-title orgs, each structural stratum, recently created), by `md5(id)` order.
- **Labels:**
  - drafted by me from D1's definitions **before any Jev call on these orgs**;
  - the operator reviews the unsure rows plus a seeded random 30, against public sources;
  - then the labels are committed.
- **Storage:** because the repo is **public**, only `(org_id, kind)` is committed, at `docs/research/org-kind/gold.csv` (operator-confirmed 2026-11-24). A builder script joins names and state at run time into a set file **outside any checkout**, and the vendored `typesafe-eval` harness scores it. Sending uses `TYPESAFE_OPT_IN=on` per run, which is the operator's standing consent. No repo opt-in file is committed.
- **Bar, per kind (the tracker's #210 rule):** ≥ 90 % precision at the threshold, on ≥ 20 answers. Fewer than 20 answers means insufficient evidence, and the kind fails closed. A failed kind is never bulk-accepted from Jev; its orgs go to individual review.
- **Rules are scored too.** A rule that misses the bar is dropped.
- **The result:** a committed trust set (`JEV_TRUSTED_ORG_KINDS`), plus the report on #877.

### D7. Bulk accept (the backlog, supervised)

`scripts/accept_classification_suggestions.py --field org_kind`:
- runs dry unless given `--execute`, and echoes its target (`_dsn`);
- **accepts** a suggestion only when one of these holds:
  - a `rules` answer from a rule that passed the bar;
  - a `jev` answer ≥ the threshold on a kind in the trust set, with no `rules` answer disagreeing;
- **fills only NULL kinds**, and writes the `manual` row with `created_by='bulk:<operator>'`;
- skips archived orgs;
- reports counts by kind and by source, plus what's left for the queue.

The same script serves `--field role_type` under #303.

### D8. When Jev runs: at creation, plus a sweep

- **A creation hook.** After an org-creating transaction commits (the admin create doors, an observation's `NEW`, the usa-wa applier, the importer), the code calls `classification.wake()`. That sets an in-process event and returns immediately. It never touches Jev and never fails the write.
- **A drainer** in each worker waits on the event or a 15-minute tick, then:
  1. takes `pg_try_advisory_lock`, so only one worker classifies at a time;
  2. selects the orgs with no current `jev` row for the pinned model (archived excluded);
  3. classifies them in batches of 25, capped at `JEV_MAX_PER_RUN`;
  4. writes their rows.

  It acquires its own pool connection, like the `api_request_log` writer (#262). **This is a documented exception to the "routes use `Depends(get_db)` only" rule**: the drainer is not a route handler. Tests run with the flag off, and the drainer is driven directly with a fake transport.
- **The same drain as a CLI** (`scripts/classify_suggestions.py`, run dry by default) and as a **daily timer** (`power-map-classification.timer`). The timer is the **dead-man signal**: it alone checks in to a new co-status monitor (#566 pattern, `SetCredential=status-checkin:\n`), `ok` or `alert`. The in-process drainer never checks in. A failure there logs a WARNING and shows on the health panel, and the daily timer's alert carries it to a person. The API unit needs no credential (`docs/ALERTING.md`).
- **CLI refresh flags** (`--refresh-model`, `--ids-file`) are the only re-asks, and they skip any entity with a `manual` decision.
- **Configuration:**
  - `JEV_SUGGESTIONS_ENABLED` gates calls only. Answers already stored are always shown.
  - `TYPESAFE_API_KEY`: already in the repo `.env`. It must also go in `/etc/power-map/.env` for the service.
  - Also `JEV_MODEL`, `JEV_CONFIDENCE_THRESHOLD` (0.7) and `JEV_MAX_PER_RUN` (2000).
  - With the flag on and no key, the health panel shows a failure and the timer alerts.
- **Volume:** 6 orgs created in the last 90 days. The backlog of 1,670 orgs is ~1.3 M tokens unbatched (less batched), **≈ $0.05**.

### D9. Public API: phase 3, additive (operator-confirmed 2026-11-24)

- `org_kind_slug: str | None` (a `str`, never a `Literal`, per #857's note) and `org_kind_source` on the org list and detail responses.
- `GET /api/v1/org-kinds`, the catalog, with conditional GET. That means `API_ROUTE_COUNT` +1, the conditional-GET pin +1, and an entry in `_CONDITIONAL_GETS`.
- A minor version bump and a client regen.
- Shipped last, so the field goes public only once kinds are populated.

## Phases (each its own PR, TDD)

1. **Schema plus the rules source:**
   - `org_kinds` seed, `organizations.org_kind_id`, `classification_suggestions` and its ancillary wiring;
   - the rules source;
   - the admin Kind panel (read, accept, set, dismiss, clear).
   - No Jev yet.
2. **Jev:**
   - the client, the drainer and wake hook, the CLI and timer, and the health panel and check-in;
   - the gold set and harness, then the scoring report on #877 and the trust set;
   - flag **off** until the report is approved.
3. **The backlog:** bulk accept (dry run, then `--execute` with operator approval), the review queue, and the public API (D9).

Each phase pauses for merge/deploy authorization. A phase that bumps the version tags at the deployed `main`.

## Decided by the operator (2026-11-24)

1. **List of kinds:** 11. `association` and `coalition` stay separate.
2. **`government_agency` and `public_body` are merged into `government`.** There's one kind per org, and the most specific kind wins (D1). Several kinds per org was considered and rejected: it needs a join table, a yes/no question per kind, and gold labels as sets, and it gives up a single answer for routing roles.
3. **Gold set:** only `(org_id, kind)` is committed to the public repo, and names never are.
4. **Public API:** org kinds are exposed in phase 3 (D9).

## Calls I made (correct any)

- No `unknown` slug; NULL means undecided.
- Role titles go in Jev's state, and person names never do.
- `rules` cover four identifier kinds only, and UBI/PDC say nothing.
- Rules/Jev disagreement goes to review, never to bulk accept.
- In-process wake plus an advisory-locked drainer, with a daily timer as the dead-man.
- The threshold is 0.7, and the bar is the tracker's (≥ 90 % on ≥ 20).
- Merge: `manual` decisions follow the survivor when it has none.
- Per-run `TYPESAFE_OPT_IN=on` for the harness, with no committed opt-in.
- From the builder check:
  - a re-ask marks the old Jev answer ignored before storing the new one;
  - rules are computed in the drainer, not on write paths, and identifiers that conflict store `none` and go to review;
  - a `manual_action` column tells Dismiss from Clear;
  - the drainer alerts by log and panel only, and the timer is the only check-in;
  - a merge deletes the loser's derived rows and moves its `manual` rows to the survivor if the survivor has no decision;
  - the `manual` row is the audit record.

## Left to the builder

The panel layout within `docs/UI.md`, the exact drainer tick, index choices beyond D3's unique, the health panel's queries, and the CLI flag spellings.

## Out of scope

Role-type suggestions (#303 step 3), compound-title splits (#876), and enforcing role types by org kind.
