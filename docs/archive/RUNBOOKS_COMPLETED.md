# power-map — Completed Runbooks (archive)

One-off migrations, backfills and data fixes that have **already been run against
production**. Kept verbatim for provenance — what was done, with which script and
which flags — not because anything here still needs doing.

This file lives under `docs/archive/`, which the context tooling excludes from the
live surface, so it costs nothing on a normal session. Live procedures — recurring
imports, idempotent seeds, scheduled audits, incident triage — stay in
`docs/RUNBOOKS.md`.

Verified before archiving. Every row except one is a data check run against
production — the first three on 2026-08-11, the party-Orgs row on 2026-08-17. The
Deduplication / #265 / #266 / #314 / #319 row is the weaker evidence: one-off
migrations whose issues are closed and which carry no pending state, without a data
check that would distinguish "ran" from "nothing left to do". Classified by row, not
by position, so appending here cannot silently re-label an existing claim (#428 CR3).

| Runbook | Evidence |
|---|---|
| Person canonical-name backfill (#308) | 0 active people without a canonical name |
| Assignment-relationship backfill (#301) | 3 active RA→RA edges present |
| Org end-event backfill (#313) | 191 active org end events present |
| Deduplication, #265, #266, #314, notes → citations (#319) | one-off migrations; issues closed, no pending state |
| Seed defunct WA historical party Orgs (#442) | 6 party Orgs present; dry run reports 0 to seed (2026-08-17) |

---

## Deduplication (one-time fix)


```bash
# Build --env-file flags (see § Environment)
env_args=()
[ -f /etc/power-map/.env ] && env_args+=(--env-file /etc/power-map/.env)
[ -f .env ] && env_args+=(--env-file .env)

# Dry run — report what would be removed (no DB changes)
uv run "${env_args[@]}" python -m scripts.deduplicate_roles

# Execute — apply deduplication and commit changes
uv run "${env_args[@]}" python -m scripts.deduplicate_roles --execute
```

Run before re-applying schema on a dirty DB (see § Deploy for the schema apply).

---

## Archive legacy legislator roles (idempotent, #265)


Validates each active legacy (`role_type_id IS NULL`) legislator assignment on the WA
House / Senate / Legislature orgs against the enriched seat-assignments, migrates its
ancillary data (links + contacts + field_confidence → the matched typed assignment;
`role_wa_pdc` URLs → person-level `person_wa_pdc_filer` identifier + `wa_pdc` link),
then archives the assignment; a legacy role is archived once it has no active
assignments left. Coverage-gated: staff/leadership titles are excluded (→ #266) and
unmatched rows are kept for a later re-run (e.g. after upstream backfills more history).

```bash
# Build --env-file flags (see § Environment)
env_args=()
[ -f /etc/power-map/.env ] && env_args+=(--env-file /etc/power-map/.env)
[ -f .env ] && env_args+=(--env-file .env)

# Dry run — read-only; per-row statuses + full ancillary accounting
uv run "${env_args[@]}" python -m scripts.archive_legacy_legislator_roles

# Execute — migrate ancillary data + archive, in one transaction
uv run "${env_args[@]}" python -m scripts.archive_legacy_legislator_roles --execute
```

---

## Role-type vocabulary migration + classification (issue #266)


Two one-off migrations, **run in this order**, that move legacy free-text roles onto
the #266 role-type vocabulary. Both are idempotent, dry-run by default, and commit in
a single transaction under `--execute`. Governance rules for the vocabulary itself →
`docs/SCHEMA_INDEXES.md` §"Role-type vocabulary — governance".

`scripts/migrate_member_role_type.py` splits the retired coarse `member` classifier
into `committee_member` / `party_member` by **structural org identifier**
(`org_wa_legislature_committee_id` vs `org_wa_party`) — never display names. An org
with neither is reported `skipped` and left untouched. Once no rows reference
`member`, `apply_schema` drops it from the catalog; the script then no-ops.

`scripts/classify_legislative_roles.py` types WA committee / chamber / legislative-staff
roles in four phases: curate title collisions (two spellings of one office are **merged**,
assignments re-pointed not deleted; collision-free variants renamed), classify committee
officeholders (`committee_*`) and committee staff (`legislature_staff`), classify the
legislative staff offices, then apply the enumerated chamber backlog (retitle, re-home,
principal→notes). Titles are preserved wherever normalizing would erase a real
distinction (`Acting Chair` keeps its title *and* takes `committee_chair`). Backlog rules
are org-scoped to the WA chambers — an unscoped title match would sweep in unrelated orgs.
Federal legislative roles and caucus/floor-leadership vocab are deliberately out of scope.

```bash
# Build --env-file flags (see § Environment)
env_args=()
[ -f /etc/power-map/.env ] && env_args+=(--env-file /etc/power-map/.env)
[ -f .env ] && env_args+=(--env-file .env)

# Dry runs — read-only; every planned mutation is listed per row
uv run "${env_args[@]}" python -m scripts.migrate_member_role_type
uv run "${env_args[@]}" python -m scripts.classify_legislative_roles

# Execute, in order, then re-apply schema to drop the emptied `member` type
uv run "${env_args[@]}" python -m scripts.migrate_member_role_type --execute
uv run "${env_args[@]}" python -m scripts.classify_legislative_roles --execute
bash scripts/apply-schema.sh
```

**Dates are never invented (#307):** the classifier moves a tenure embedded in a title
(e.g. `Speaker of the House (2021-23)`) into role notes and logs a WARNING — a human sets
the assignment's dates and currency afterward.

---

## Speaker Designate / Speaker split (issue #314)


`scripts/split_speaker_designate.py` resolves the human date call the classifier deferred
above for Laurie Jinkins. It splits the single dateless `Speaker of the House` tenure into
two distinct `chamber_leader` roles on WA House — mirroring the COG `Acting Chair` / `Chair`
pattern (the coarse type aggregates, the free-text title distinguishes):

- **Speaker Designate** (new role) — assignment 2019-07-31 → 2020-01-12, `is_current=FALSE`.
- **Speaker of the House** (existing role/assignment) — start 2020-01-13, open end, still
  `is_current=TRUE`; the stale `2021-23` breadcrumb on the role is cleared.

Dates come from the WA House Democrats caucus record, cited in each assignment's `notes`
(not invented, #307). A fail-loud identity guard aborts if the hardcoded prod IDs no longer
resolve to *(Jinkins, Speaker role, WA House)*. Idempotent, dry-run by default, single
transaction under `--execute`.

```bash
env_args=()
[ -f /etc/power-map/.env ] && env_args+=(--env-file /etc/power-map/.env)
[ -f .env ] && env_args+=(--env-file .env)

uv run "${env_args[@]}" python -m scripts.split_speaker_designate            # dry run
uv run "${env_args[@]}" python -m scripts.split_speaker_designate --execute  # commit
```

---

## Notes → citations migration (issue #319)


`scripts/migrate_notes_to_citations.py` extracts bare `http(s)` URLs from
`role_assignments.notes` (the pre-#319 ad-hoc provenance store, e.g. the #314
Jinkins housedemocrats.wa.gov links) into structured **whole-assignment**
`citations` rows, via the idempotent natural-key observe path (re-running never
duplicates). The original note text is **kept**. Deliberately narrow: only bare
URLs migrate; prose provenance is left for human curation via the admin editor.

```bash
env_args=()
[ -f /etc/power-map/.env ] && env_args+=(--env-file /etc/power-map/.env)
[ -f .env ] && env_args+=(--env-file .env)

uv run "${env_args[@]}" python -m scripts.migrate_notes_to_citations                       # dry run
uv run "${env_args[@]}" python -m scripts.migrate_notes_to_citations --execute             # commit
uv run "${env_args[@]}" python -m scripts.migrate_notes_to_citations --assignment-id <id>  # one assignment
```

---

## Person canonical-name backfill (issue #308)


`scripts/backfill_person_canonical_names.py` promotes one eligible public name
for every person that has names but no canonical one — those people render
blank, since `v_person_display_names` only surfaces canonical rows. One-off
repair for drift produced before #308b gave `write_names` first-wins
auto-promotion on the person branch.

Each repairable person gets the one name PM would display, chosen by the
priority ladder shared with the observation-path heal via
`name_type_priority_sql()` (see `NO_AUTO_CANONICAL_NAME_TYPES` for what is
never eligible). People carrying several eligible names are resolved, not
deferred — the heal pass would promote the same row on their next observation
anyway. `multi_name` reports how many were decided that way.

There is no `blocked` bucket any more (#308 Option A): `uq_person_canonical_name`
is keyed on `(person_id)` alone and `chk_person_canonical_is_public` guarantees a
canonical row is public, so a non-public row can no longer occupy a person's
display slot. A person whose only names are ineligible (deadname/mrz-only, or
nothing public) is simply not a candidate and stays deliberately blank until a
human adds a displayable name.

```bash
env_args=()
[ -f /etc/power-map/.env ] && env_args+=(--env-file /etc/power-map/.env)
[ -f .env ] && env_args+=(--env-file .env)

uv run "${env_args[@]}" python -m scripts.backfill_person_canonical_names
uv run "${env_args[@]}" python -m scripts.backfill_person_canonical_names --execute
```

Idempotent — a second run finds nothing. Each promotion touches `person_names` →
`trg_touch_person_on_name_change` → an `entity_changes` `'updated'` row, so
subscribers re-fetch and pick up the newly-visible name. Run **after**
`bash scripts/apply-schema.sh`, so the #308a view change is live first.

---

## Assignment-relationship backfill (issue #301)


`scripts/backfill_assignment_relationships.py` mints the 3 staffer→principal
edges descoped from #266: each staff role's active assignment `--staff_of-->`
the principal's overlapping seat assignment. Heuristic + supervised — any
staffer/principal/seat that doesn't resolve to exactly one is reported, never
guessed. `notes` on the staff role/assignment are left untouched for operator
cleanup.

```bash
uv run "${env_args[@]}" python -m scripts.backfill_assignment_relationships            # dry-run
uv run "${env_args[@]}" python -m scripts.backfill_assignment_relationships --execute  # mint
```

---

## Org end-event backfill (issue #313)


`scripts/backfill_313_org_end_events.py` resolves the nine `missing_end_event`
orgs the #307 audit surfaced, using human-researched end dates (see issue #313).
For five defunct orgs it records a `dissolved` event and closes **all** their
open assignments at `ended_on` — including the `unknown_end_on_ended` rows the
audit's `--execute` deliberately leaves open (a human-authorized close, not an
invented end). `start_after_ended` rows are still left open (closing would
invert the window) and logged as a WARNING. Kalytera (renamed to Claritas) is
reactivated rather than dissolved; the name swap is done by hand in admin.

Org ids and dates are baked into the module (`END_EVENTS`, `KALYTERA_ID`); an
id that doesn't resolve is skipped with a WARNING, never an orphan event.

```bash
env_args=()
[ -f /etc/power-map/.env ] && env_args+=(--env-file /etc/power-map/.env)
[ -f .env ] && env_args+=(--env-file .env)

uv run "${env_args[@]}" python -m scripts.backfill_313_org_end_events            # report
uv run "${env_args[@]}" python -m scripts.backfill_313_org_end_events --execute  # apply
```

Idempotent — skips orgs that already carry a lifespan event; re-closing an
already-closed assignment is a no-op. Re-run the #307 audit afterward to confirm.

---

## Seed defunct WA historical party Orgs (idempotent, #442)


`scripts/seed_442_historical_parties.py` — mints the party Organizations that
CannObserv/usa-wa#219's pre-1991 roster backfill needs (its phase #227). Prerequisite:
`apply_schema`, so the `org_wa_party` identifier type, the `wikipedia` link type and the
`founded` event type are seeded.

**Six Orgs for seven tokens** (resolved by CannObserv/usa-wa#233):

- `Cit.` gets **no Org** — not a formally organised state party, just hyper-local
  "Citizens Party" / "Citizen Nonpartisan" ballot labels. Same rule as `Independent`: a
  label is not an organisation.
- `Prog.` is **one Org scoped to 1913–1917** (the Bull Moose formation). The roster's lone
  1927 House record under that token is Knute Hill, who is not a member of it — producers
  must not fold that record into this Org.

```bash
# Build --env-file flags (see § Environment)
env_args=()
[ -f /etc/power-map/.env ] && env_args+=(--env-file /etc/power-map/.env)
[ -f .env ] && env_args+=(--env-file .env)

# Dry run — reports would-create / already-present, no DB writes
uv run "${env_args[@]}" python -m scripts.seed_442_historical_parties

# Test DB first (always, from a worktree)
uv run "${env_args[@]}" python -m scripts.seed_442_historical_parties --test --execute

# Execute against production
uv run "${env_args[@]}" python -m scripts.seed_442_historical_parties --execute
```

Each Org gets a canonical name, the source file's own token as canonical acronym (so the
admin renders `Name (P.P.)`), an `org_wa_party` identifier, `notes`, a Wikipedia link, and
citations to the roster and to Brazier's legislative history.

Three #442 rulings the script encodes — change none of them without re-reading the issue:

- **`active = false`, never archived.** The axes are orthogonal (#240) and an archived Org
  *rejects* later `active` observations (`active_on_archived_org`), so archiving at birth
  would mint Orgs the producer cannot observe.
- **No `dissolved` / `merged_with` event.** With a year, either populates
  `v_org_lifespan.ended_on`, which gates `role_assignment` writes — a dissolution year taken
  from a party's last legislative appearance would reject the very backfill these Orgs
  exist to enable. A **year-less** `merged_with` is the escape hatch if lineage ever needs
  recording: `requires_year` is false and the view filters `event_year IS NOT NULL`.
- **`founded` only where the anchor is WA-scoped** — Silver Republican (1896), Farmer-Labor
  (1920), Socialist (1901-09). People's Party and Populist have only *national* founding
  dates, and asserting one on an Org named "Washington State …" overstates its scope.

Idempotent, and a **seeder, not an updater**: an Org already carrying the party's
`org_wa_party` value is adopted and left completely untouched, so a curated row cannot be
clobbered by a re-run.

**`blocked`** = the party's `org_wa_party` value resolves to no live Org despite identifier
rows existing, or to more than one. `identifiers` has no FK to `organizations` and
`org_delete` leaves them behind, so a hard-deleted party Org strands a live-looking row
nothing reaps (`audit_ancillary_orphans` covers only `role` / `role_assignment`). The seed
logs the reason at WARNING, names every blocked party in a summary line, and seeds the
rest. Clear the stray `identifiers` rows, then re-run.
