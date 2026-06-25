# Effective-Dated Org Names; One-WSL-Id-Per-Committee Invariant

**Issue:** #239
**Date:** 2026-06-24
**Resolves:** CannObserv/usa-wa#40

## Goal

Settle how PM models a renamed organization (the WSL committee case: id 31639,
"COG" → "RSG"), and give PM the data to answer **"which name was in effect
when"** without forking an Org.

Two coupled deliverables:

1. **Identity (contract only, no code):** a rename is **one durable Org**, not a
   fork. The WSL identifier stays anchored to that Org for its entire life.
2. **Effective-dated org names (new feature):** add a real-world validity
   timeline to `organization_names` so PM is the system of record for the
   name timeline.

## Context

CannObserv/usa-wa#40 proposed mirroring a PM "two-Org lineage" by minting
per-epoch WSL identifiers (e.g. `31639` for the live epoch + `31639:rsg` for the
retired one), which would require PM to forfeit its "one WSL Id = one committee"
invariant.

PM's architecture contradicts that premise on three independent axes:

- **Name history already lives on one durable Org.** `organization_names`
  carries `name_type ∈ {legal, dba, former}` + `is_canonical`. A rebrand =
  old name demoted to `former`, new name promoted canonical. No epoch-fork
  mechanism exists in the schema.
- **Ingestion is append-only and never displaces the canonical name.**
  `write_names` (`src/core/observation.py`) appends a new name as
  **non-canonical**; the canonical flip is a curated admin action. So "PM owns
  the curated name history" is already how the code behaves.
- **PM's rebrand tooling is *merge*, not fork.**
  `docs/plans/2026-04-17-org-manual-merge-design.md` exists specifically to
  *collapse* a rebrand that became two records into one.

The real downstream requirement (confirmed during design) is **"temporal, but
same entity"**: usa-wa needs to know which name was in effect when an assignment
was active — a date-on-the-name-record concern, not a separate-entity concern.
PM dates roles (`established_on`/`abolished_on`) and assignments
(`start_date`/`end_date`) but **not names** — that is the one gap this design
closes.

## Approved Approach

### Part 1 — Identity (no code)

- One durable PM Org per committee for its entire life. COG → RSG is a rename,
  not a split.
- `org_wa_legislature_committee_id = 31639` stays anchored to that one Org
  forever. **"One WSL Id = one committee" is promoted to a documented
  public-API invariant.**
- usa-wa keeps **one** local row; its `rematch_anchor` re-resolves that row to
  the same durable Org each cycle. No per-epoch identifiers.
- The issue's per-epoch-identifier option is **rejected** — it breaks PM's
  global identifier-uniqueness invariant, contradicts the name-history-on-one-Org
  model, and inverts the merge-rebrands tooling.

Deliverable: documented contract (this doc + a note in the public-API docs).

### Part 2 — Effective-dated org names

#### Schema

Add a real-world validity interval to `organization_names`:

```sql
effective_start DATE   -- name began in the real world; NULL = unknown lower bound
effective_end   DATE   -- name ceased; NULL = still in effect / ongoing
CHECK (effective_start IS NULL OR effective_end IS NULL
       OR effective_start <= effective_end)
```

- Added via an **idempotent `DO`/`ALTER` block** in `schema.sql`, matching the
  existing roles `established_on`/`abolished_on` pattern (`schema.sql:571`).
- Backfill = all NULL.
- "Name as of date D" semantics: **NULL `effective_start` = −∞, NULL
  `effective_end` = +∞**, so legacy un-dated names still resolve (imprecisely)
  and nothing breaks.

#### `is_canonical` and `name_type` stay decoupled

- `is_canonical` remains the curated **display** pointer (one-per-org unique
  index, drives `v_org_display_names`). It is **not** coupled to
  `effective_end IS NULL`. The currently-effective name *should* be canonical and
  curation keeps them aligned, but coupling them would blast into the merge SQL,
  the unique index, and promote/demote logic for no proportional benefit.
- `name_type` (`former` etc.) is the *kind* of name; effective dates are the
  *when*. Orthogonal and complementary.

#### Ingestion — strictly append-only, fully curated transition

- `ObservationOrgName` gains optional `effective_start` / `effective_end`.
- `write_names` stores those dates **only on newly inserted rows**. It does
  **not** mutate existing rows, does **not** auto-close the prior interval, and
  does **not** flip canonical.
- Dates supplied for an already-present name = **no-op** (consistent with the
  existing append-only / never-displace behavior).
- A rename transition is a **curated admin two-step** (below).

#### Read path

- The public org-name representation gains `effective_start` / `effective_end`,
  serialized as `YYYY-MM-DD` (consistent with how roles expose
  `established_on`/`abolished_on`).
- **No dedicated `?as_of=D` endpoint** — the consumer fetches the dated name
  list and filters locally. (YAGNI; revisit if a second consumer needs
  server-side as-of resolution.)
- **Change feed unchanged** — it already emits org-change events; the dates ride
  along when the consumer re-fetches the org.

#### Admin

- The org name-row editor gains the two date fields, mirroring the role
  `established_on`/`abolished_on` editor.
- A rename is a curator **two-step**:
  1. Add the new name (`legal`/`dba`), set `effective_start`, mark canonical →
     demotes the old canonical.
  2. Edit the old name: set `name_type = former`, set `effective_end`.
- The shared `make_names_router` (`src/api/admin/_names_shared.py`) needs an
  `effective_dates` toggle so the **person**-name forms are unaffected (person
  dating is out of scope).

#### Accepted gap

Until a transition is curated, two names can both have `effective_end IS NULL`
(overlapping open intervals → "as of D" ambiguous in the gap). Admin should make
the open-interval state **visible** (e.g. surface "2 open name intervals"); no
hard DB guard — that would fight the append-only ingestion contract.

## Key Decisions

| Decision | Rationale |
|---|---|
| One durable Org, no fork | Matches PM's schema, append-only ingestion, and merge-rebrands tooling; preserves global identifier uniqueness. |
| Keep "one WSL Id = one committee" | Forfeiting it (per-epoch ids) breaks the core invariant for every consumer, not just usa-wa. |
| Date names, don't fork them | The requirement is "temporal, same entity" — a date-on-record concern. PM already dates roles/assignments; names are the missing parallel. |
| `effective_start`/`effective_end` nullable DATE + CHECK | Mirrors the established roles pattern; NULL = unknown bound keeps legacy rows resolvable. |
| `is_canonical` decoupled from timeline | Minimal blast radius; display vs. timeline are genuinely orthogonal concerns. |
| Ingestion append-only, curated transition | Preserves the existing never-displace invariant exactly; a bad/late feed observation can never silently change PM's display name or timeline. |
| No `?as_of=` endpoint | Dated list is sufficient for the one consumer; YAGNI. |

## Out of Scope

- Person-name effective dating (org-only for now; possible future parallel).
- Backfill of historical committee rename dates (all NULL initially; curate as
  needed).
- Coupling `is_canonical` to `effective_end IS NULL`.
- A dedicated `?as_of=D` query endpoint.
- All usa-wa-side code (separate repo / issue: CannObserv/usa-wa#40).

## Reassessment (post-#238 rebase, 2026-06-25)

Branch rebased onto main after #238 landed. Findings, all confirming the design:

- **#238 is pure admin HTMX UX** (generalized the "+ Add" in-flight guard into one
  `add-row-guard.js`; touched `_name_form_row.html`). No conflict with the
  schema / ingestion / read-path work; only the admin step builds on the new
  guard wiring (untouched by adding form fields).
- **Broadcast: no gap.** A name-row change already emits a change-feed event —
  `trg_touch_org_on_name_change` bumps `organizations.updated_at`, which fires
  `trg_entity_changes_organizations` → an `entity_changes` `'updated'` row. So
  the "change feed unchanged" claim holds; consumers re-fetch and get the dates.
  (`data/ENTITY_STATES.md` §3's trigger list omits this transitive touch-parent
  path; PM behavior is correct.)
- **Admin uses the existing precedent.** `make_names_router` already gates
  person-only columns via `supports_person_metadata`; effective dates ride a
  parallel `supports_effective_dates` flag (org-only), keeping person forms
  untouched.

## Implementation Order (suggested)

1. Schema: add columns + CHECK (idempotent block) + test.
2. Ingestion: extend `ObservationOrgName` + `write_names` to store dates on new
   rows; tests for store-on-new and no-op-on-existing.
3. Read path: add dates to the public org-name response model + serializer; test.
4. Admin: add date fields to the org name-row form/read partials + the
   `make_names_router` `effective_dates` toggle; tests.
5. Docs: public-API contract note for the "one WSL Id = one committee" invariant
   and the dated-name representation; update `docs/CONVENTIONS.md` / STYLE as
   needed.
