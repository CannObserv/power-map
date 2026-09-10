# Mapping project: dbt-duckdb scaffold, persons + organizations, `src/core` goes WA-free

**Issue:** #497 · **Epic:** #490 (design § PM-side pipeline step 2, § Review addendum gap E)
**Status:** approved 2026-09-10 · supersedes nothing

## Goal

Stand up PM's dbt-duckdb mapping layer and land the first two desired-state
products — persons and organizations — so #499 has an artifact to diff. Remove
the last WA knowledge from `src/core`.

The mapping models are the **only** place usa-wa/WA ontology lives in PM.
Everything below follows from that and from one measured fact: the producer and
PM already agree about the data in scope, so this is a convergence exercise, not
a migration.

## Measurements this design rests on

Taken 2026-09-09/10 against the landed snapshots and production.

| Fact | Value | Consequence |
|---|---|---|
| Anchored people / their name rows | 3,118 / 3,121 | One legal name each; name curation is not the risk |
| Non-legal names on anchored people | 1 (a maiden name) | Producer-owned legal name is nearly total in practice |
| Restricted-visibility names | 0 | Nothing to protect via visibility |
| Pronouns / notes on anchored people | 72 / 51 | The real curator enrichment — and usa-wa publishes neither |
| usa-wa persons vs anchored | 3,135 vs 3,118 | ~17 creates for #499's dry-run gate |
| usa-wa organizations vs anchored orgs | 219 vs 219 | Exact match; no org creates expected |
| Org types | committee 186, other 22, party 8, chamber 2, legislature 1 | Clean vocabulary |
| Orgs with bienniums | 186 | All committees |
| Orgs at the `1991-92` left edge | 35 | A data horizon, not 35 foundings |
| Orgs whose `last_biennium` is current (`2025-26`) | 34 | Still live — no `dissolved` |
| Existing org events on anchored orgs | founded 154, dissolved 152, succeeded_by 104, merged_with 34, split_from 23 | The sidecar wrote these pre-freeze |
| Producer titles vs PM stored titles | **312 / 312 identical** | Nothing to reconcile |
| Producer titles vs PM's synthesizer | 147 agree, **0 differ**, 165 not synthesizable | `role_title.py` never covered more than 47% |

## Pipeline

```
data/usa_wa_snapshots/<name>/<version>/data.csv        ← #496 puller
data/usa_wa_snapshots/_pm/producer_crosswalk.parquet   ← new export step
        │
        ▼  src/core/ingestion/mapping/   (dbt-duckdb)
   staging/       stg_usa_wa__persons, stg_usa_wa__organizations
   intermediate/  int_person_identity, int_org_identity      crosswalk join → pm_id
   marts/         desired_people, desired_person_names,
                  desired_organizations, desired_organization_names,
                  desired_organization_acronyms, desired_entity_events
        │
        ▼  data/desired_state/*.parquet   ← the diffable artifact #499 consumes
```

### Decision: the crosswalk crosses the seam as a file

`producer_crosswalk` lives in Postgres; the models run in duckdb.
`scripts/export_producer_crosswalk.py` dumps it to Parquet — a read-only
`scripts/` reader that calls `echo_target()` and needs no `--execute`, because
it writes only a file.

Rejected: duckdb's postgres extension reading the table live. It would make the
model layer depend on a database (so dbt tests need one), and a dbt profile
holding `DATABASE_URL` becomes a door to production that
`tests/scripts/test_dsn_sweep.py` cannot see — it walks `scripts/*.py` only.
`DATABASE_URL` resolves to **production** from any directory (#402).

Rejected: emitting producer-keyed desired state and joining in the applier. The
diffable artifact would not be PM-keyed, so reviewing a diff would mean holding
the crosswalk in your head — and #497 states the join is on the seeded table.

### Decision: the project lives under the ingestion tree

`src/core/ingestion/mapping/`, per the design doc's "under the ingestion tree".
The WA-free gate exempts `src/core/ingestion/` — the subscriber seam is allowed
to name its producer; the domain layer is not.

## What each model asserts

### Persons — legal name and identity

Desired state: the person exists, carries its usa-wa identifier, and has one
`person_names` row with `name_type='legal'` equal to `name_full`.

PM keeps the canonical pointer (#308), `locale`, `script`, `sort_as`,
`visibility`, `reading_of_id`, structured parts, every non-legal name, and both
`people` columns usa-wa never publishes — `personal_pronouns` and `notes`, which
is where all 123 pieces of real curator work live. Column-scoping (safeguard 4)
protects them by construction.

Rejected: identity only — the model would emit nothing, since usa-wa fills no
column on `people`, and name drift would never reconcile. Rejected: full name
authority — that would make the #308 canonical pointer producer-owned, and a
curator's deliberate display choice would revert nightly.

### Organizations — four row surfaces and one event type

| Model | Source column | Note |
|---|---|---|
| `desired_organizations` | identity | like persons: `(pm_id, producer_id)` |
| `desired_organization_parents` | `agency` / `org_type` → parent | row-scoped; see revision below |
| `desired_organization_names` | `coalesce(long_name, name)` → `legal` | see revision below |
| `desired_organization_acronyms` | `acronym` | 186 rows |
| `desired_organization_merges` | `org_crosswalk.merged_into` | gap E for orgs; zero live today |
| `desired_entity_events` | `last_biennium` → `dissolved`, year precision | see below |

**Revision 2026-09-10 (step 5 measurements).** Two of the rows above changed
from the approved draft once PM's side was measured, and both change in the
direction of convergence:

- *No `dba` from `name`.* PM holds 310 `legal` and 13 `former` name rows on the
  219 anchored orgs and **no** `dba` rows — `long_name` is the canonical legal
  name and the short `name` was never stored. Asserting 186 `dba` rows would
  fail safeguard 2's "creates ≈ 0 on anchored cohorts" by construction. Legal
  is `coalesce(long_name, name)`; `name` stays in staging for a later decision.
- *Parents are row-scoped, not a column.* `agency` reproduces PM for House
  (98), Senate (84) and Joint/Other/chambers → Legislature (24), but four orgs
  are parented to **other committees** (subcommittees), which `agency='Other'`
  cannot express, and three House-agency orgs are parented elsewhere too. A
  producer-owned `parent_id` column would clobber all seven. So
  `desired_organization_parents(pm_id, parent_pm_id)` asserts a parent only
  where the producer determines one; an absent row is no claim. The seven are
  expected step-7 divergences for #501, and overlay material if PM wins.

### Decision: `dissolved` is the only event type the model owns

**`founded` is never asserted.** 35 committees share `first_biennium = 1991-92`,
the earliest biennium in the dataset — that is where usa-wa's records begin, not
when those committees started. Asserting it would mint 35 false founding claims.
The right edge is not censored the same way (the data reaches the present), so a
`last_biennium` in the past is a real signal. `v_org_lifespan` derives only
`ended_on` in any case; the 154 existing `founded` events feed nothing.

**Ownership is declared per event type, not per entity.** The model owns
`{'dissolved'}`. `founded`, `succeeded_by`, `merged_with` and `split_from` — 315
events, 161 of them with no producer column at all — are out of scope, never
diffed, never deleted. Without that scoping, retraction-as-absence would delete
every one of them. This is safeguard 5 (row scope + per-dataset retraction
policy) applied at event-type granularity.

The 34 orgs whose `last_biennium` is the current `2025-26` get no event.

## Curation overlay seam

`curation_overlay(entity_type, entity_id, field, value, note, created_by)`.

**#497 creates the table and joins it** as `COALESCE(overlay.value, mapped.value)`
on every producer-owned field. **#498 builds the admin write path and UI.**

Splitting it this way keeps the join where the design doc puts it — "joined
declaratively in the mapping models" — and means the models are overlay-shaped
from the first commit rather than rewritten one issue later. #497 does not block
on #498: an empty overlay is a no-op join, and the 312/312 title agreement shows
there is nothing needing an override on day one.

## `src/core` goes WA-free

### Why `role_title.py` goes rather than moves

It exists because of #267, whose stated principle was that "PM is the single
authority for seat-title curation and upstream observers never nudge it" — the
"never let a robot overwrite a human" premise this epic **retracts** (design doc
lines 20–24, which name this module as the cause of the WA leak).

Three facts close it:

1. The producer publishes the title. usa-wa's `roles` dataset carries
   `name: "Washington State Senator, LD-34"` — byte-identical to what PM
   synthesizes, on all 312 anchored roles.
2. PM's synthesizer never covered more than 147 of those 312. `committee_member`
   and `party_member` titles have no LD jurisdiction, so PM has stored
   producer-supplied titles for the other 165 all along.
3. The resolver's WA branch has no live producer. It fires only for
   `usa-wa-ld-N` jurisdictions; usa-wa's key was last used at the freeze
   (2026-09-08 17:55:19Z), and Observo — the one live `observations:write`
   holder — carries cannabis-industry data that never reaches that branch.

It also drifted past its own design: #267 chose *fill-when-absent* and put
"always-synthesize" explicitly out of scope, but `observation.py:1157` reads
`synthesize_role_title(...) or title`, preferring the synthesized form.

### Changes

Deleted: `src/core/role_title.py`, `scripts/generate_wa_roles.py`,
`tests/core/test_role_title.py`.

`generate_wa_roles.py` goes with it: it produced the bootstrap seed for the 147
WA legislative seats, which usa-wa now publishes. Its output was never committed
(it lands in the gitignored `data/cannabis_observer/`). Between #497 and #500
nothing can mint a WA seat automatically — accepted, since all 147 exist and a
curator can create one by hand if redistricting ever demands it. Retiring
`scripts/seed_roles.py` itself belongs to #502.

Changed: `observation.py` stops synthesizing, so `title` is required again on
seat observations — a **breaking** re-tightening of #267's loosening, documented
in `PUBLIC_API.md` and `CONVENTIONS.md`. `admin/roles.py` and
`admin/roles_detail.py` drop the suggestion (confirmed not needed). Two
docstrings — in `observation.py` and `normalizers/identifier.py` — reworded to
describe a per-position office and a districted jurisdiction generically.

### The gate

`tests/test_src_core_wa_free.py` walks `src/core/**/*.py`, excluding
`src/core/ingestion/`, asserting no `usa[-_]wa`, `wa_pdc`, `Washington` or bare
`WA` token survives. Like the #399 DSN sweep it carries **no allowlist**.

`schema.sql` is out of scope: its 34 WA mentions are PM's own reference data —
the `state_senator` / `state_representative_at_large` role types (#302
established these as PM-owned with no remote write path) and the `wa_pdc`
identifier type — with the same status as every other seeded vocabulary. The
line the gate draws is **reference data versus branching logic**, and it is
drawn over `.py` files only.

## Testing

- dbt `schema.yml` tests (unique, not_null, accepted_values, relationships) run
  against **fixture CSVs** in the unit tier via a pytest wrapper. Hermetic and
  fast precisely because the crosswalk is a file.
- One marked test runs `dbt build` against the **real landed snapshot**, to
  catch producer-shape drift that fixtures cannot.
- **Gap E gets an explicit fixture.** `person_crosswalk` carries zero
  `merged_into` tombstones today, so the merge-following path has no live data
  to exercise it; a fixture with a tombstone asserts the survivor resolves, with
  a test that crosses that seam.
- The WA-free gate, per above.

## Out of scope

Roles and assignments models (#500) · the diff-applier (#499) · the overlay
write path and admin UI (#498) · `founded` / `succeeded_by` / `merged_with` /
`split_from` · retiring `seed_roles.py` and the change-feed surface (#502) ·
seat-occupancy conflicts (#506).

## Risks

- **dbt-duckdb is a new toolchain in this repo.** First non-service dependency
  with its own project layout and test idiom. Mitigated by keeping models
  file-to-file so the tests stay in the unit tier.
- **The `dissolved` diff will not be empty.** PM holds 152 dissolved events and
  the producer implies 152; agreement is expected but unproven until the first
  dry run. Any divergence is triage material for #501, not a blocker here.
- **`title` becoming required again is breaking.** No live producer sends seat
  observations, so the blast radius is documentation, but it is a real contract
  change and ships as one.
