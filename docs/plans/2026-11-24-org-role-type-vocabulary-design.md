---
title: Organization role-type vocabulary (org_* prefix) + backlog classification (#303)
date: 2026-11-24
status: proposed
---

# Organization role-type vocabulary (#303)

## Intent

Most untyped roles are corporate and advocacy roles: owners, chief executives, lobbyists, board seats. They can't be aggregated today ("all lobbyists", "every board seat held by person X") without matching free text across 1,045 distinct titles. #266 wrote the rubric and reserved the `org_*` prefix for this space. This design applies that rubric: a small vocabulary, seeded and deployed before anything emits it, then a reviewed, dry-run-by-default classification of the backlog.

**Goals**
- Seed a coarse `org_*` vocabulary that passes #266's aggregation test.
- Type the unambiguous part of the backlog automatically, and type the ambiguous part only after a human has reviewed it.
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

**A. Coarse function vocabulary, two-tier classifier (recommended).** Seven `org_*` slugs, one per function a power map would aggregate. The org's own label stays in `title` (rule 3). The classifier types exact unambiguous titles automatically and types everything else from a reviewed decisions file.

**B. One slug per title** (`org_ceo`, `org_president`, `org_executive_director`, `org_chair`, `org_co_owner`, …). Rejected:
- CEO, President and Executive Director are one concept, the chief executive, under labels that differ by org form (company, association, nonprofit). That is exactly rule 3's "concept in type, label in title", like `chamber_leader`.
- A slug per label proliferates (rule 4), and every query for "who runs X" would need a union.

**C. Minimal: `org_lobbyist`, `org_owner`, `org_board_member` only.** Cheapest, and it covers the highest-value signals. Rejected as the default because it leaves the ~104 chief-executive rows and the founders untyped, and it doesn't answer the issue's coarse-versus-specific question. It's the fallback if the operator wants a smaller first step.

**`org_` over `corp_`:** most Executive Director, Board Member, Lobbyist and Member rows sit on nonprofits, associations, coalitions and unions, not corporations. #266 also already reserved `org_*` (`docs/SCHEMA_INDEXES.md` rule 2).

## Decisions

### D1. The vocabulary: seven slugs, seeded ahead of the classification

All seven are `expects_jurisdiction=FALSE`, `requires_qualifier=FALSE`, `forbids_qualifier=FALSE` and `occupancy='shared'`. Co-owners, co-CEOs and boards all have several holders, so none makes a single-holder claim.

| id | slug | display_name | concept | exact titles typed automatically |
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
- **`Director` never maps automatically.** It means a board seat, an agency head and a department head. The prefix-safe name for the board concept is `org_board_member`, not `org_director`.
- **`org_member` is seeded, and it is review-only.** It passes rule 1 (coalition and association membership is a power relation). Its "Member" rows mix in seats that are not `org_member`: city councils are public offices, party district orgs are `party_member`, and advisory committees are public bodies. So no title ever maps to it automatically.
- **No `org_staff`. It is reserved, not seeded.** "All employees across orgs" fails rule 1, and seeding it would invite typing the long tail. `Employee` (47) stays free text.

**What `org_*` means, recorded in rule 2:** it covers an organization's ownership, governance, management, membership and representation, for any org without a more specific prefix. It never goes on an org carrying a legislature or party identifier, which belongs to `committee_` / `chamber_` / `legislature_` / `party_`. It never goes on a public office: elected or appointed seats are a future government vocabulary. A state agency's executive director is `org_executive`: the concept is the same, and the seat isn't elected.

**Rollout:** the seed ships and deploys first. `role_types` has no remote write path (`resolve_role` → `role_type_not_found`), so any producer must find the slugs already live.

### D2. Collision with the legislative types: prefix plus a structural guard

The prefix already keeps the slugs apart. `org_member` ≠ `committee_member` ≠ `party_member`, and `org_board_member` doesn't overlap `committee_chair`.

The risk is in the data. Two backlog rows sit on legislature and party orgs, and "Chair" and "Member" rows sit on US House and city-council committees with no identifier. Two guards cover it:
1. The classifier **refuses** any role whose org carries an `org_wa_legislature%` or `org_wa_party` identifier (reason `legislative_or_party_org`).
2. The domain-ambiguous titles (Member, Chair, Chairman, Co-Chair, Vice Chair, Director, President, Principal, Partner, Managing Partner, Managing Director, Treasurer, Secretary, Founding Member, Advisory Board Member, Principal Officer) **never** map automatically. A human decides each one.

A new sweep test checks that every seeded non-jurisdictional slug starts with a registered prefix (`committee_`, `chamber_`, `legislature_`, `party_`, `org_`). That makes rule 2 executable.

### D3. Titles: the type aggregates, so fix only errors

A row's type is what aggregates, so variants don't need collapsing. `Chairman`, `Co-Owner` and `Co-Founder` keep their titles: they are the org's own labels (rule 3).

The classifier fixes only clear errors, on the rows it types:
- `Board Memeber` → `Board Member`
- `Principle Officer` → `Principal Officer`
- lowercase `owner` / `lobbyist` / `Co-owner` → `Owner` / `Lobbyist` / `Co-Owner`

These rows are PM-native, so PM owns their titles. A case-only change can't collide, because `uq_role_org_title` keys on `lower(title)`. A spelling fix that would collide with an existing same-org title skips **the whole row**: it stays untyped and is reported as `title_collision`. Typing it under the misspelled title would count one seat twice. Merging is a human's admin role merge, never the script's.

### D4. The backlog: one script, dry run by default, auto tier plus reviewed decisions

`scripts/classify_org_role_types.py` follows `scripts/classify_legislative_roles.py`: `add_dsn_args` / `resolve_dsn`, target echo, dry run unless `--execute`, idempotent.

**Candidates:** active, untyped, non-jurisdictional roles. The script skips, with a reported reason:
- a role on an archived org (`org_archived`; none today);
- a role on a legislature or party org (`legislative_or_party_org`, D2);
- a role named by a `producer_crosswalk` row (`dataset_owned`; none today). A dataset role is typed by its producer through usa-wa, never by PM's script.

**Auto tier:** an exact case-folded, trimmed title in D1's table. It needs no review.

**Review tier:** the D2 ambiguous titles and every compound title that contains an auto-tier title.
- The dry run writes `--review-out <path>` as a CSV with these columns: `role_id`, `org_display_name`, org identifier kind, `title`, `suggested_slug`, `reason`.
- For a compound title, the suggested slug follows the precedence `org_lobbyist` > `org_owner` > `org_executive` > `org_board_member` > `org_founder` > `org_officer`. The rarer, more power-relevant signal wins, and the title keeps the rest.
- The operator fills a `decision` column: an `org_*` slug, or blank to skip. The script accepts only the seven `org_*` slugs. A row a reviewer judges to be a party, committee or public-office role stays untyped and is reported as `not_org_domain`. This script never writes `party_*` or `committee_*`, so rule 2 holds by construction, and those rows feed the follow-ups below.
- The reviewed file is committed as `scripts/data/303_org_role_decisions.csv` and read with `--decisions`.
- A row whose current title or `role_type_id` no longer matches what the file recorded is skipped (`drifted`).

**Writes:** `UPDATE roles SET role_type_id=$1 [, title=$2] WHERE id=$3 AND role_type_id IS NULL`. It only fills a NULL and never reclassifies, the same rule as `resolve_role`'s upgrade-on-match. It touches no assignment.

**Expected yield (estimated by exact title):**
- auto ≈ 382: executive 104, owner 102, founder 60, lobbyist 51, board 42, officer 23;
- review ≈ 193 ambiguous-title rows plus ≈ 83 compound rows;
- about 1,150 rows stay free text by design.

The dry run reports the real figures.

**Who runs it:** the operator. Run the dry run, review the CSV, commit the decisions, then run `--execute` under supervision. That is three supervised steps after the seed deploys, each in its own operator-approved run.

### D5. Ingest: no change, because the existing paths already self-classify

- **Observations:** `resolve_role` takes any seeded slug. A typed observation that lands on an untyped role fills its type in place (upgrade-on-match, #266). A producer that emits `org_lobbyist` self-classifies with no code change.
- **Admin:** the role-type `<select>` reads the catalog, so the new slugs appear in it. The `Organization …` display names keep them grouped in its `ORDER BY display_name`.
- **Importer:** dormant (one batch, 2026-03-17), with no type column. It is not taught the title map, which would be a second copy of D1. If a new CSV ever arrives, a `Role Type` column is a follow-up.
- **usa-wa:** legislative only. Its mapping looks slugs up in PM's catalog, so the new rows are inert for it.

### D6. Archived orgs (#835)

This design adds **no** path that creates roles.
- The observation path already refuses an archived org (`resolve_role` → `org_not_found`).
- The classifier skips a role on an archived org.
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

## Steps (TDD)

1. **Seed.** Red: a catalog test on `GET /api/v1/role-types` asserts the seven slugs with D1's flags, and the prefix sweep test (D2) runs over the seed. Green: append to `_seed_role_types`.
2. **Rubric docs.**
   - `docs/SCHEMA_INDEXES.md`: rule 2 gets the `org_*` scope. Reserved-not-seeded gains `org_board_chair` and `org_staff`. Update the current-catalog line.
   - Update the `schema.sql` seed comment and the vocabulary list in `docs/API_ROLES.md`.
3. **Classifier (integration tests, run alone).** Test cases:
   - auto tier;
   - never reclassifies;
   - skips jurisdictional roles, legislative or party orgs, archived orgs and crosswalk-owned roles;
   - title fixes, with a collision skip;
   - a dry run writes nothing;
   - the review CSV's shape and compound precedence;
   - `--decisions` applies, and skips a drifted row;
   - a second run is a no-op.
4. **Version, client and tag.** Minor bump, `regenerate_client`. The tag goes on at deploy.
5. **Ship gate, PR, code review, then pause** for merge, deploy and restart authorization (the sentence that names all three).
6. **After deploy, supervised:** dry run, then the review CSV goes to the operator, then a follow-up PR commits `scripts/data/303_org_role_decisions.csv`, then `--execute`. Re-take the counts.

## Calls I made (correct any)

- Seven slugs (Approach A), including `org_officer` and `org_member`; `org_board_chair` and `org_staff` reserved.
- `Employee` stays untyped.
- Precedence for compound titles; no role splitting.
- An agency head is `org_executive`, but elected and public seats stay out.
- The decisions file is committed and keyed by role id. It accepts `org_*` slugs only.
- A title collision skips the whole row rather than typing a duplicate.
- A minor bump.
- A relay to Observo only.

## Left to the builder

- The exact auto-tier title list beyond D1's core, as long as it adds no D2-ambiguous title.
- The CSV column order and the CLI flag spelling.
- Whether the prefix sweep lives in the existing seed-reconciliation sweep or in a new test file.

## Out of scope / follow-ups

- **Government and elected-office vocabulary:** Governor, Mayor, Council Member, Commissioner, tribal Chair, Attorney General. These are seats, possibly jurisdictional. Propose a new issue.
- **Federal legislative roles on US House committees.** Deferred by #266 already.
- **Splitting compound roles into two roles.**
- **#304:** embedded-newline titles and the typo sweep beyond D3.
- **#835:** the importer's archived-org match (D6).
