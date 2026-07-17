---
title: Role-type strategy (rubric + prefix convention) + bounded legislative classification (#266)
date: 2026-07-17
status: accepted
---

# Role-type strategy + legislative classification (#266)

## Problem

Legislative leadership, committee, and staff roles are modeled as free-text titles with `role_type_id IS NULL`, so they can't aggregate reliably. The naive fix — mint a `role_type` per office — has no guardrails: the classification surface is hundreds of untyped non-jurisdictional roles across three unrelated domains (legislative, corporate/advocacy, noise), and the existing coarse `member` type (157 rows) already means two different things (committee membership on 155 rows, party membership on 2). Without a strategy, #266 would proliferate WA-flavored, domain-ambiguous slugs. We need a rubric that gates additions, then a *bounded* classification of the legislative slice.

## Approach

Land a **rubric** as durable governance in `CONVENTIONS.md`, then apply it to the legislative domain only. Rubric: (1) **aggregation test** — a slug exists only if you'd query "all of them across orgs/jurisdictions," else it stays free-text; (2) **domain-prefix convention** — every new slug prefixed by the org-kind it lives on (`committee_`, `chamber_`, `legislature_`, `party_`), which also yields coarse rollups for free (`slug LIKE 'legislature_%'`); (3) **concept-in-type, jurisdiction-label-in-renderer** — types stay jurisdiction-neutral, WA labels live in `src/core/role_title.py`; (4) **coarse where a long tail exists**. Seed the refined vocab (coarse `chamber_leader`/`chamber_officer`/`legislature_staff`/`party_member`; specific `committee_*`), migrate `member` → `committee_member` (155) + `party_member` (2) then drop it, and classify the legislative backlog via an idempotent script that sets `role_type_id`, normalizes titles, and re-homes staff-office rows. Add `resolve_role` upgrade-on-match so ongoing ingest self-classifies. The `state_representative`/`state_senator` seat types are grandfathered unprefixed — renaming a public-API-visible slug is breaking and buys nothing.

## Tradeoffs / alternatives

- **Add `role_type_id` to the non-districted match key (the issue body's original ask)** — withdrawn: stale post-#269, and `uq_role_org_title` already makes the feared collision structurally impossible. Upgrade-on-match is the useful change instead.
- **Specific types per office (`chamber_speaker`, `chamber_majority_leader`, …; `staff_aide`, `staff_counsel`, …)** — rejected: the committee-staff long tail (~18 count-1 titles) proves this proliferates without aggregation value. Coarse type + specific free-text title, with the prefix giving rollups, is simpler and reversible.
- **Enforced `applies_to_org_type` scoping column** — rejected (for now): needs an org-kind taxonomy we don't have. Prefix convention is zero-schema and self-documenting; revisit if collisions appear.
- **Structured office field for `chamber_leader` (lift qualifier-needs-jurisdiction)** — deferred: out of proportion for the one leadership row. Title carries office+chamber today; lifting the constraint is a deliberate future step if cross-office aggregation is ever needed.
- **Classify everything the sweep surfaced** — rejected: corporate/advocacy vocab (#303) and the noise/typo sweep (#304) are separate follow-ons; person→person staff is #301.

## Steps

1. **Rubric → `CONVENTIONS.md`.** Document the aggregation test, prefix convention, concept/label rule, coarse-vs-specific guidance, and the reserved-not-seeded policy. No code. (Verify: section present, references #266.)
2. **Seed the vocab (TDD).** Add to the `role_types` seed in `schema.sql`: `chamber_leader`, `chamber_officer`, `committee_chair`, `committee_vice_chair`, `committee_ranking_member`, `committee_assistant_ranking_member`, `committee_member`, `legislature_staff`, `party_member` (all `expects_jurisdiction=FALSE`, `requires_qualifier=FALSE`). Test via the public `GET /api/v1/role-types` catalog. (Verify: new test asserts each slug present; existing role-type tests green.)
3. **`resolve_role` upgrade-on-match (TDD).** When a typed observation matches an untyped non-jurisdictional row, fill `role_type_id` in place (only when existing is NULL — never reclassify). Red test first. (Verify: new test in `test_resolve_role_structural.py`; existing behavior unchanged when both typed or both untyped.)
4. **`member` migration script (TDD).** Idempotent, dry-run/`--execute`, mirroring `scripts/archive_legacy_legislator_roles.py`: 155 committee-org `member` → `committee_member`, 2 party-org `member` → `party_member`, then verify `member` has 0 rows. (Verify: script test on seeded fixtures; dry-run reports the 155/2 split.)
5. **Legislative classification script (TDD).** — **DEFERRED, blocked on the org-dedup + committee-tagging precursor (#305).** The classification surface can't be resolved structurally today: only 5 of ~85 orgs bearing committee-officeholder titles carry `org_wa_legislature_committee_id`, and committee orgs are duplicated (COG ×3; the roles live on an *untagged* dup). The rubric forbids the display-name heuristic that would otherwise be needed. After #305 dedups + tags WA committee orgs, resume here: set `role_type_id` + normalize titles + re-home for the backlog (committee officeholders `Chairman`/`Acting Chair`→`Chair`, `Ranking Minority/Democratic Member`→`Ranking Member`; the 9 chamber/staff rows Speaker→`chamber_leader` with `(2021-23)`→assignment dates, Secretary of the Senate→`chamber_officer`, 8 staff→`legislature_staff`; all committee-staff→`legislature_staff` keeping titles; re-home OPR Director→WA OPR, SCS Director→WA SCS, COG analyst→the *canonical* COG org confirmed post-#305). **Deferred out of #266 entirely:** federal legislative roles (US House/Senate committees + congressional seats) and caucus/floor-leadership vocab (whip/floor-leader) — WA-only scope here. `resolve_role` upgrade-on-match (step 3) means ongoing ingest self-classifies in the meantime.
6. **Drop `member` type.** After step 4's split leaves it at 0 rows, remove the seed row + assert absence. Independent of step 5 (untyped rows don't reference `member`). (Verify: catalog no longer lists `member`.)
7. **Docs sync.** Update `AGENTS.md` DB Key Rules + `docs/CONVENTIONS.md` role sections to reference the new vocab and the withdrawn match-key ask. Version bump `pyproject.toml` + `package.json`. (Verify: `check-version-sync` hook passes.)
8. **Full suite + ship.** `uv run pytest`, ruff, apply schema on the dev server, run the migration/classification scripts against a copy, `verification-before-completion` before any completion claim.

## Open questions / risks

- **Re-home targets need confirmed org IDs at execution:** WA OPR `01KV6PQGAAR5SDJH6H6BXSYYQT`, WA SCS `01KXRJQBBD2RCBJXZJ6P5DG726` (user-supplied — verify + confirm neither already has a `Director` role, else AUTO_ATTACH is fine but confirm). House COG committee org `01KWJP0WVH7PR7E77TZN60TXCJ` (user-supplied) for the COG-analyst re-home.
- **Migration ordering:** steps 4 and 5 both mutate roles; the `member` migration (4) and classification (5) can run in either order but `member` must be dropped (6) only after both complete. Run migrations against a DB copy first; production data touches ~190 rows.
- **`chamber_leader` aggregation loss:** "all Speakers cross-state" is not queryable (office is free-text, not structured). Accepted per the coarse-type decision; documented in the rubric.
- **Ambiguous committee `Secretary`/`Assistant Secretary`:** default to `legislature_staff` under the simplify mandate — flag any that look like committee officers during the dry-run for a human call.
