# Legislator-Role backfill — decision record (audit + defer)

- **Date:** 2026-07-04
- **Issue:** #265 (follow-on to #261 seat model, #263 seat seed)
- **Status:** Implemented — enrichment landed 2026-07-16/17 (House 2019+, Senate 1991+;
  usa-wa#97); validator/archiver is `scripts/archive_legacy_legislator_roles.py`
  (see `docs/COMMANDS.md`). PDC open question resolved: filer IDs are campaign identity →
  rescued to person-level `person_wa_pdc_filer` + `wa_pdc` link. Pre-2019 House rows
  (4 ex-reps) stay unarchived pending an upstream House-depth follow-up; re-run then.
- **Blocked on:** ~~USA-WA sister-repo automated observations (position-bearing enrichment)~~ landed

## Goal

Tie the existing WA legislator `role_assignments` (person→tenure records that predate
the #261 seat model) to the 147 canonical seat-Roles seeded in #263, so that
aggregation over seats reflects who has held each district's House/Senate seats.

## Context: what the legacy data actually contains

Audited 2026-07-04 against production: 81 legacy assignments on the three relevant
orgs (Washington State House of Representatives, Washington State Senate, Washington
State Legislature), all with `role_type_id` / `jurisdiction_id` / `qualifier` = NULL.

| Bucket | Count | Shape |
|---|---|---|
| Senate district roles | 15 | `"Senator, District N"` — one occupant, one senator/district → **clean 1:1** to the seeded Senate LD-N seat |
| House district roles | ~29 | `"Representative, District N"` (+ variants `"District 47 Representative"`, `"34th District State Representative"`) — district derivable, **position NOT present** |
| Generic no-district | 20 | House `"Representative"` ×7, Senate `"Senator"` ×12, Legislature `"Representative"` ×1 — district not derivable from the row |
| Staff / leadership | ~9 | `Legislative Assistant/Aide`, `Speaker of the House`, `Secretary of the Senate`, directors, analysts — **not seats**, out of scope |

Two facts kill any attempt to parse-and-merge with fidelity:

1. **House position is absent everywhere.** The seat model has two House seats per
   district (Position 1 / Position 2). No legacy House role carries a position, and
   the profile links encode none.
2. **All assignment dates are NULL** (`start_date` / `end_date`), so occupants of a
   two-holder district cannot even be split temporally. Position would be a coin flip.

## Decision

**Do not parse/normalize the legacy roles. Audit + document now; defer all
migration/archival to a post-enrichment follow-up.**

The sister USA-WA repo's automated observations will imminently write full-fidelity
seat-assignments (person→seat, including House position) through the observation
endpoint onto the seeded seats. Once that lands, the legacy free-text roles and their
assignments become redundant and are archived — not reconciled by hand.

Under this framing the district-vs-generic distinction dissolves: both become
redundant the same way once enrichment writes proper seat-assignments. There is no
title parser, no position inference, and no seat-aware legacy dedup to build.

### Why not archive now

Archiving today is **lossy**. The following ancillary data has its only copy on the
legacy assignment (nothing equivalent exists at the person level):

| Ancillary data | Count | Notes |
|---|---|---|
| `role_wa_pdc` identifiers | 13 | PDC campaign-finance filer IDs (e.g. Cody `CODYE 126`, Ramel `RAMEA 109`) |
| profile links | 69 | `housedemocrats.wa.gov/…`, `*.src.wastateleg.org`, … |
| contact_methods | 25 | official `@leg.wa.gov` emails + a few phones |
| field_confidence | 107 | provenance / confidence rows |
| notes | 0 | — |

The enrichment may reproduce profile URLs and emails, but there is **no guarantee it
carries the PDC filer IDs**. Archiving before enrichment lands would silently drop
them. Correct sequence: **enrich → validate redundancy → archive.**

### Ancillary-data disposition

Role-contextual data (a legislator's official profile URL, their `@leg.wa.gov` email,
`field_confidence`) legitimately belongs on the **assignment**, not the person — they
hold it *because* they occupy the seat. It is **not** misfiled and must **not** be
migrated to the person. The eventual archiver must preserve or explicitly account for
it before archiving.

**Open question (resolve when enrichment lands):** PDC filer IDs — are they associated
with the person's *campaign* (→ person-level, survives archive independently) or with
the official legislator job (→ assignment-level, lost on archive)? This determines
whether the archiver must rescue them.

## Sequencing

1. *(blocked)* USA-WA enrichment writes seat-assignments onto the seeded seats via the
   observation endpoint (person→seat, incl. House position).
2. *(follow-up)* **Redundancy validator** — for each legacy legislator assignment,
   confirm an enriched seat-assignment covers the same person, and account for every
   ancillary datum (preserved, or deliberately dropped with the PDC question resolved).
3. *(follow-up)* **Archive** the fully-redundant legacy roles + assignments
   (dry-run + report before `--execute`, per project convention).

## Out of scope

- Title parsing / normalization of free-text legislator titles (obviated by enrichment)
- House position inference
- Generic bare-`"Representative"` / `"Senator"` triage
- Seat-aware dedup of the legacy roles
- Re-homing the Legislature-parent `"Representative, District 29"` role (handled by
  enrichment writing the correct House seat-assignment)

## Notes for the follow-up issue

- Target set: `roles` with `role_type_id IS NULL` on org
  `01KV6PQGA3Y269YY60KN2XSZAY` (House), `01KV6PQH9WE7CEFDAXTDZJP54Y` (Senate),
  `01KV6PQGHBKHEY8NXJM2Y5EDVS` (Legislature), minus the staff/leadership titles.
- The audit query used here is the seed of the redundancy validator: join each legacy
  assignment's `(person_id)` against enriched seat-assignments on the matching
  jurisdiction, and diff attached identifiers / links / contact_methods.
