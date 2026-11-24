---
title: Organization role-type vocabulary (org_* prefix) + backlog classification (#303)
date: 2026-11-24
status: accepted (D1–D3, D6, D7); D4–D5 revised 2026-11-24, detail follows the org-kind design
---

# Organization role-type vocabulary (#303)

> **Revision, 2026-11-24 (operator).**
> - The seven `org_*` slugs are confirmed.
> - Compound titles will be split, not typed once by precedence (#876).
> - Classification is **suggestion-based**, following the WSLCB Licensing Tracker's Jev pattern (Approach B): rules and TypeSafe Jev *suggest*, and only an operator's acceptance sets `role_type_id`.
> - Suggestions are requested **when a role is created**.
> - **Org kind is classified first** (Approach C, #877), so role suggestions can use a settled org kind.
>
> D4 and D5 below are rewritten to match. Order of work:
> 1. seed the vocabulary (this PR);
> 2. org kinds and the shared suggestion machinery (#877);
> 3. role suggestions and the backlog (the rest of #303);
> 4. #876, the compound-title split.

## Intent

Most untyped roles are corporate and advocacy roles: owners, chief executives, lobbyists, board seats. They can't be aggregated today ("all lobbyists", "every board seat held by person X") without matching free text across 1,045 distinct titles. #266 wrote the rubric and reserved the `org_*` prefix for this space. This design applies that rubric: a small vocabulary, seeded and deployed before anything emits it, then a reviewed, dry-run-by-default classification of the backlog.

**Goals**
- Seed a coarse `org_*` vocabulary that passes #266's aggregation test.
- Suggest types for the backlog from rules and Jev; an operator accepts them — the unambiguous part in validated bulk, the ambiguous part row by row.
- Keep `org_*` clear of the legislative `committee_` / `chamber_` / `legislature_` / `party_` types.

**Non-goals:** typing every role. Under rule 1, the long tail (892 rows whose title occurs once) stays free text. Government and elected offices (Governor, Mayor, Council Member, Commissioner, tribal Chair), federal legislative roles, splitting compound roles, and the noise/typo sweep are also out (#304).

**Anti-goals:** `org_*` turning into a catch-all for "any role on any org". A classifier that guesses a role's domain from the org's display name, which the rubric forbids. Rewriting a title a producer owns.

## Measured state (prod, read-only, 2026-11-24, `origin/main` @ `12d9d415`)

`12d9d415` is only #873's skills-submodule bump on top of the addendum's `ddf353b7`. It touches nothing in `schema.sql`, the role routes or the role docs.

| | count |
|---|---|
| Active roles | 2,173 |
| Untyped | 1,816 (all non-jurisdictional) |
| `role_types` | 12; no `org_*` or `corp_*` slug |
| Distinct untyped titles (case-folded) | 1,045; 892 rows carry a title that occurs once |
| Untyped roles on an archived org | 0 |
| Overlay rows on roles | 0 |

**Where the backlog came from.** `roles` has no provenance column, so this was traced through its assignments:
- **1,791** roles are reached by `import_provenance` from one CSV batch: `2026_03_17-cannabis_observer-power_map-{organizations,people,roles}-v2.csv`. Its columns are Name, Organization, Title, Current Role?, Notes and contact fields. There is no type column.
- **25** have no provenance, so they were created in the admin. 20 of them are `Lobbyist` rows created on 2026-07-15.
- **0** are in `producer_crosswalk` (usa-wa), and **0** have an assignment carrying `source_key_id` (observations).

**So no producer owns any backlog row.** PM classifies the whole backlog itself. No overlay is needed, and there's no producer to wait for.

**Org kind can't be recovered structurally.** There is no org-kind column. By org identifier, the 1,816 roles break down as:

| org identifier | roles |
|---|---|
| none | 1,611 |
| `org_wa_pdc` | 193 |
| `org_ubi` / `org_wslcb` | 5 |
| `org_wa_pdc_committee` | 5 |
| `org_wa_legislature*` | 1 |
| `org_wa_party` | 1 |

The ambiguous titles really do mix domains. Sampled:
- **Chair:** US House Appropriations, a Seattle City Council committee, the Spokane Transit Authority, and NCIA's Science Advisory Committee.
- **Chairman:** five tribes and WAGOP.
- **Member:** city councils, 43rd District Democrats, trade associations (NCIA, CANNRA, CORE), unions and prevention coalitions.
- **Director:** state agency heads, and department heads in companies.

**Titles by concept** (untyped, exact title in parentheses):
- Owner 71, Co-Owner 22 (+4 `Co-owner`, 1 `owner`), Managing Member 4
- Founder 41, Co-Founder 19
- Executive Director 55, CEO 43, President 31, Chief Executive Officer 1
- Lobbyist 45 (+1 `lobbyist`), Contract Lobbyist 3
- Board Member 22 (+3 `Board Memeber`), Board Chair 3, Board President 2
- Member 58, Director 22, Principal 20, Chair 17, Chairman 7, Partner 6, Treasurer 6
- Employee 47
- 73 compound titles over 83 rows (`Founder and CEO`, `Co-Owner and CEO`, `Executive Director and Lobbyist`, …)
- `Principle` appears only as `Principle Officer` ×1.
- Several titles contain embedded newlines. That is #304's noise sweep.

## Approaches

**A. Coarse function vocabulary, two-tier classifier (recommended).** Seven `org_*` slugs, one per function a power map would aggregate. The org's own label stays in `title` (rule 3). The classifier types exact unambiguous titles automatically and types everything else from a reviewed decisions file. *(Superseded 2026-11-24: the operator chose suggestions with operator acceptance, D4.)*

**B. One slug per title** (`org_ceo`, `org_president`, `org_executive_director`, `org_chair`, `org_co_owner`, …). Rejected:
- CEO, President and Executive Director are one concept, the chief executive, under labels that differ by org form (company, association, nonprofit). That is exactly rule 3's "concept in type, label in title", like `chamber_leader`.
- A slug per label proliferates (rule 4), and every query for "who runs X" would need a union.

**C. Minimal: `org_lobbyist`, `org_owner`, `org_board_member` only.** Cheapest, and it covers the highest-value signals. Rejected as the default because it leaves the ~104 chief-executive rows and the founders untyped, and it doesn't answer the issue's coarse-versus-specific question. It's the fallback if the operator wants a smaller first step.

**`org_` over `corp_`:** most Executive Director, Board Member, Lobbyist and Member rows sit on nonprofits, associations, coalitions and unions, not corporations. #266 also already reserved `org_*` (`docs/SCHEMA_INDEXES.md` rule 2).

## Decisions

### D1. The vocabulary: seven slugs, seeded ahead of the classification

All seven are `expects_jurisdiction=FALSE`, `requires_qualifier=FALSE`, `forbids_qualifier=FALSE` and `occupancy='shared'`. Co-owners, co-CEOs and boards all have several holders, so none makes a single-holder claim.

| id | slug | display_name | concept | exact titles the rules source suggests |
|---|---|---|---|---|
| `01KX000000000000000000000E` | `org_owner` | Organization Owner | holds equity or ownership | Owner, Co-Owner, Managing Member |
| `01KX000000000000000000000F` | `org_founder` | Organization Founder | founded the org | Founder, Co-Founder |
| `01KX000000000000000000000G` | `org_executive` | Organization Executive | the chief executive | CEO, Chief Executive Officer, Executive Director, Interim Executive Director, President and CEO, President & CEO |
| `01KX000000000000000000000H` | `org_officer` | Organization Officer | a named officer below the chief executive | COO, Chief Operating Officer, CFO, Chief Financial Officer, General Counsel, Vice President, other `Chief … Officer` |
| `01KX000000000000000000000J` | `org_board_member` | Organization Board Member | a seat on the governing board, including its chair | Board Member, Board Chair, Board President, Executive Board Member, Founding Board Member, Board of Directors, BOD … |
| `01KX000000000000000000000K` | `org_lobbyist` | Organization Lobbyist | lobbies for or on behalf of the org | Lobbyist, Contract Lobbyist, Associate Lobbyist, State Lobbyist |
| `01KX000000000000000000000M` | `org_member` | Organization Member | a member of a membership org (association, coalition, union, co-op, network) | none: always reviewed |

The ids follow the seed's Crockford sequence after `…0D`. There is no I, L, O or U.

- **`Owner` and `Co-Owner` share `org_owner`.** The co- prefix stays in the title. "All owners" is the query, and co-ownership doesn't change the concept.
- **Board chair takes `org_board_member`**, not a type of its own. The power-map query is board interlock ("every board seat X holds"), and chair is the label. `org_board_chair` is **reserved, not seeded**, until something needs "all board chairs".
- **`Director` never gets a rules suggestion.** It means a board seat, an agency head and a department head. The prefix-safe name for the board concept is `org_board_member`, not `org_director`.
- **`org_member` is seeded, and it is review-only.** It passes rule 1 (coalition and association membership is a power relation). Its "Member" rows mix in seats that are not `org_member`: city councils are public offices, party district orgs are `party_member`, and advisory committees are public bodies. So no title ever gets a rules suggestion for it.
- **No `org_staff`. It is reserved, not seeded.** "All employees across orgs" fails rule 1, and seeding it would invite typing the long tail. `Employee` (47) stays free text.

**What `org_*` means, recorded in rule 2:** it covers an organization's ownership, governance, management, membership and representation, for any org without a more specific prefix. It never goes on an org carrying a legislature or party identifier, which belongs to `committee_` / `chamber_` / `legislature_` / `party_`. It never goes on a public office: elected or appointed seats are a future government vocabulary. A state agency's executive director is `org_executive`: the concept is the same, and the seat isn't elected.

**Rollout:** the seed ships and deploys first. `role_types` has no remote write path (`resolve_role` → `role_type_not_found`), so any producer must find the slugs already live.

### D2. Collision with the legislative types: prefix plus a structural guard

The prefix already keeps the slugs apart. `org_member` ≠ `committee_member` ≠ `party_member`, and `org_board_member` doesn't overlap `committee_chair`.

The risk is in the data. Two backlog rows sit on legislature and party orgs, and "Chair" and "Member" rows sit on US House and city-council committees with no identifier. Two guards cover it:
1. **No source suggests** an `org_*` type for a role whose org carries an `org_wa_legislature%` or `org_wa_party` identifier (`legislative_or_party_org`).
2. The domain-ambiguous titles (Member, Chair, Chairman, Co-Chair, Vice Chair, Director, President, Principal, Partner, Managing Partner, Managing Director, Treasurer, Secretary, Founding Member, Advisory Board Member, Principal Officer) **never** get a rules suggestion. Jev may suggest one; a human decides each.

A new sweep test checks that every seeded non-jurisdictional slug starts with a registered prefix (`committee_`, `chamber_`, `legislature_`, `party_`, `org_`). That makes rule 2 executable.

### D3. Titles: the type aggregates, so fix only errors

A row's type is what aggregates, so variants don't need collapsing. `Chairman`, `Co-Owner` and `Co-Founder` keep their titles: they are the org's own labels (rule 3).

The bulk-accept script (D4) fixes only clear errors, and only on the rows it accepts:
- `Board Memeber` → `Board Member`
- `Principle Officer` → `Principal Officer`
- lowercase `owner` / `lobbyist` / `Co-owner` → `Owner` / `Lobbyist` / `Co-Owner`

These rows are PM-native, so PM owns their titles. A case-only change can't collide, because `uq_role_org_title` keys on `lower(title)`. A spelling fix that would collide with an existing same-org title skips **the whole row**: it stays untyped and is reported as `title_collision`. Typing it under the misspelled title would count one seat twice. Merging is a human's admin role merge, never the script's.

### D4. The backlog: suggestions, then operator acceptance (revised)

**Principle: suggestive, never definitive.** No automated source writes `roles.role_type_id`. Three sources each record an answer per role, kept as append-only history in a shared suggestion store that step 2 (#877) designs for orgs and roles alike:
- **rules:** the exact-title auto tier below;
- **jev:** TypeSafe Jev, with its confidence, per-option probabilities, model and tokens;
- **manual:** an operator's decision, which always wins.

The type changes only when an operator acts: Accept, Set other, or Dismiss on one role, or a **bulk accept** of a slice that the gold set has shown to be accurate. Every bulk accept goes through a script that runs dry by default and needs `--execute`, and it only ever fills a NULL `role_type_id`. Nothing overwrites a manual decision. This is the tracker's #1 anti-goal.

**Rules source.** Kept from the first draft:
- An exact case-folded, trimmed title in D1's table suggests that slug.
- The domain-ambiguous titles (D2) get no rules suggestion.
- A role on a legislature or party org, an archived org, or one named in `producer_crosswalk` gets no suggestion from any source.
- The estimated split stands: ~382 rules suggestions, ~193 ambiguous-title rows, ~83 compound rows (split under #876, never typed by precedence), and ~1,150 rows in the long tail.

**Jev source.**
- **One Choice question per role.** The options are the seven `org_*` slugs plus `none`. `none` covers a party, committee or public-office role, plain staff, and anything the vocabulary doesn't name.
- **State:** the role title, the org's display name, the org's kind once settled (step 2), and the org's identifier kinds. **No person names are sent.**
- Batched, with criteria per question, following the tracker's `jev_client.py` and its research notes.
- The model version is pinned, and each role is classified once. A re-query happens only by explicit refresh.
- An `org_*` answer on an org whose settled kind is a legislature, party or public body is recorded but never offered for acceptance.

**Gold set and evaluation (before any bulk accept).**
- About 300 roles, sampled by stratum: the auto tier, the ambiguous titles, and the long tail.
- I draft the labels from the vocabulary's definitions *before* any Jev call. The operator reviews the unsure rows plus a seeded random sample. Then the labels are committed.
- The scoring harness refuses to send while the label file has uncommitted changes, and it caps spend.
- Both sources are scored: rules against the gold set, and Jev per slug at its threshold.
- **Go-live bar per slug** (the tracker's, from #210): **≥ 90 % precision at the threshold, on ≥ 20 answers.** A slug short of answers fails closed. A slug that misses the bar is never bulk-accepted from Jev; its rows go to individual review.
- The vendored `typesafe-eval` tooling (`skills-vendor/gregoryfoster-skills/scripts/typesafe-eval.py`) is the starting point for the harness.
- The report answers the operator's question directly. Measured against the gold set:
  - how often Jev agrees with the rules tier;
  - how much of the ambiguous tier it decides correctly;
  - how many correct suggestions it finds in the long tail.
- Measured spend is pennies: 1,816 roles at a few hundred tokens each is about 1 M tokens, at $0.042/Mtok.

**Who runs it:** the operator, as supervised steps. Score the gold set, bulk-accept the slices that clear the bar (`--execute`), then work the review queue.

### D5. Ingest: suggestions at creation time (revised)

- **No path ever sets a type from a suggestion.** Typed producers keep setting `role_type` themselves: observations through `resolve_role`, including upgrade-on-match, and usa-wa through its mapping. Those paths don't change.
- **When an untyped, non-jurisdictional role is created,** by an admin door, an observation's `NEW` disposition, or the importer, PM requests suggestions **after the transaction commits**. That is a background task, so the write never waits on Jev or fails because of it. The rules suggestion is computed inline; it's free.
- **A sweep timer** catches anything the background task missed (a restart, Jev unreachable, a dataset or import path) under the same classify-once rule. It also checks in to co-status, and it is the dead-man signal, as in the tracker.
- **An enable flag gates Jev calls only.** Suggestions already stored are still shown when it's off.
- **Volume:** 6 roles and 6 orgs were created in the last 90 days, so the ongoing cost is negligible. The backlog is where the value is.
- **The admin role page** shows each source's suggestion with Accept / Set other / Dismiss. A review queue lists open suggestions that no operator has acted on.

The detailed design — the table shape, the queue, health and spend, the enable flag — is shared with org kinds and lands in step 2's design doc. This section is updated to point at it.

### D6. Archived orgs (#835)

This design adds **no** path that creates roles.
- The observation path already refuses an archived org (`resolve_role` → `org_not_found`).
- No suggestion is made for a role on an archived org.
- **Found, outside this design's scope:** the CSV importer's org dedup (`pipeline.py`, `existing_org` lookup) matches canonical names without `archived_at IS NULL`. A future import could attach a role to an archived org. This is recorded for #835. It is not fixed here.

### D7. The wire, the version and the relays

- **Wire:** the new rows serve through `GET /api/v1/role-types`. There is no schema or OpenAPI change, and the route count stays at 415. The conditional-GET pins are unchanged, though the endpoint's ETag changes with its content, as intended. `role_type` and `role_type_slug` stay `str` (#857's note), so no client breaks on the new values.
- **Version: minor bump**, `0.76.1` → `0.77.0`. #302's seed precedent was `0.37.0` → `0.38.0`, and new producer-emittable vocabulary is a capability. Bump `pyproject.toml` and `package.json` together. `regenerate_client` then changes only the version. Push an annotated tag at the deployed `main`.
- **Relays, with the operator's consent, after deploy:**
  - **Observo:** yes. It identifies people speaking in meetings, and a speaker who testifies for a company or advocacy group is an `org_*` role it could emit. Send an FYI naming the seven slugs and the "seeded only; unknown slugs bounce" rule.
  - **usa-wa:** no relay. Nothing in its domain changes.

## Constraints carried from the brief

- **No unique index or constraint is added**, so #816's parity-exit-3 hazard doesn't apply.
- The seed goes through `_seed_role_types` and `reconcile_seeded_slugs()` (#458). Tests read the catalog and never commit `role_types` rows (#819).
- **No admin route, flash or pinnable slot changes**, so #853 and #838 don't apply.
- There is no `name_type IN (` list, so the #857 CHECK-parity window doesn't apply.
- Work in a worktree. Use `apply-schema.sh --test` only. Run the integration tier alone. No closing keywords in any PR body. Pause before shipping.

## Steps

**Step 1 — this PR (TDD).**
- Seed the seven slugs: the catalog test on `GET /api/v1/role-types`, plus a `resolve_role` test showing an observation's `org_lobbyist` fills an untyped role in place.
- Add the prefix sweep, `tests/core/test_role_type_prefix_sweep.py`.
- Update the docs (`SCHEMA_INDEXES.md` rule 2, the reserved list, the catalog; `API_ROLES.md`).
- Bump to `0.77.0` and regenerate the client (a version-only diff).
- Ship gate, PR, code review, then **pause** for authorization to merge, deploy and restart. At deploy, push the annotated `v0.77.0` tag on the deployed `main`.

**Step 2 — #877:** its own design doc. It covers the list of org kinds (the operator's decision), the shared suggestion store, Jev with an org gold set, suggestions at creation time, review screens, health and spend.

**Step 3 — the rest of #303,** per D4 and D5: the Jev role question, the role gold set and its scoring, the rules source, the bulk-accept script, creation-time hooks on roles, and the review queue.

**Step 4 — #876:** split compound titles.

## Calls I made (confirmed or still open)

- **Confirmed by the operator:**
  - the seven slugs;
  - suggestion-based classification (B), with suggestions at creation time;
  - org kind first (C);
  - compound titles split (#876).
- **Mine, still open to correction:**
  - `Employee` stays untyped;
  - an agency head counts as `org_executive`, while elected and public seats stay out of `org_*`;
  - Jev's options include `none`;
  - no person names are sent to Jev;
  - the go-live bar is ≥ 90 % precision on ≥ 20 answers;
  - the minor bump;
  - a relay to Observo only.

## Left to the builder

- The exact rules-tier title list beyond D1's core, as long as it adds no D2-ambiguous title.
- The CLI flag spelling of the bulk-accept script.
- Settled in step 1: the prefix sweep lives in its own file, `tests/core/test_role_type_prefix_sweep.py`.

## Out of scope / follow-ups

- **Government and elected-office vocabulary:** Governor, Mayor, Council Member, Commissioner, tribal Chair, Attorney General. These are seats, possibly jurisdictional. Propose a new issue.
- **Federal legislative roles on US House committees.** Deferred by #266 already.
- **Splitting compound roles into separate roles:** #876.
- **#304:** embedded-newline titles and the typo sweep beyond D3.
- **#835:** the importer's archived-org match (D6).
