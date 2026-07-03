# Canonical Legislator Representation (Design)

Issue: #261
Date: 2026-07-03
Status: Approved (brainstorming)

## Goal

Define one canonical, complete data pattern for elected legislators (WA House
Representatives, WA Senate Senators) that consistently captures: chamber, office,
district, position, occupant, and tenure. Aggregation ("all Representatives",
"all seats in a district", "whole chamber") must be a query over structured
columns, not a set of parallel bookkeeping rows.

## Approach: the seat IS the Role

Four layers, each already a first-class entity in the schema:

| Layer    | Table                                    | Carries                                             |
|----------|------------------------------------------|-----------------------------------------------------|
| Body     | `organizations` (hierarchy via `parent_id`) | WA Legislature (parent), WA House, WA Senate (children) |
| District | `jurisdictions` (type `legislative_district`) | LD-5 as one enduring entity, shared by all its seats |
| Seat     | `roles` (extended)                       | office kind + chamber + district + position         |
| Tenure   | `role_assignments` (unchanged)           | person + seat + term dates                          |

The **seat is a `roles` row**, matching the table's own definition: "Role =
position definition at an organization (independent of who holds it or when)."
District, office, and position are **structured columns** on the seat, so a
"generic role" (all Reps, all seats in LD-5) is a derivation/query, never a
stored row. `role_assignments` stays lean: person + seat + dates.

WA fact baked in: the House and Senate **share the same 49 districts**, so LD-5
is one `jurisdictions` row referenced by three seats (1 Senator + 2 Reps). Use
jurisdiction type `legislative_district` (the upper/lower split is for states
whose chambers use different maps).

### Rejected alternative

Jurisdiction/position on the **assignment** (generic "Representative" role,
district + position recorded per tenure). Rejected: the seat has no durable
identity, district is a property of the seat not the occupant, and every tenure
re-states district + position. Also rejected: parallel "generic Role" rows for
aggregation (reintroduces occupant-assignment ambiguity and duplicate
bookkeeping).

## Schema changes

### 1. New typed classifier (mirrors `jurisdiction_types`)

```sql
CREATE TABLE IF NOT EXISTS role_types (
    id           TEXT        PRIMARY KEY,
    slug         TEXT        NOT NULL UNIQUE,
    display_name TEXT        NOT NULL,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

INSERT INTO role_types (id, slug, display_name) VALUES
    (<ulid>, 'state_representative', 'State Representative'),
    (<ulid>, 'state_senator',        'State Senator')
ON CONFLICT (id) DO UPDATE SET
    slug = EXCLUDED.slug, display_name = EXCLUDED.display_name;
-- extend later: speaker, majority_leader, committee_chair, ...
```

### 2. Extend `roles` (three nullable columns; existing rows untouched)

```sql
ALTER TABLE roles ADD COLUMN IF NOT EXISTS role_type_id    TEXT REFERENCES role_types(id);
ALTER TABLE roles ADD COLUMN IF NOT EXISTS jurisdiction_id TEXT REFERENCES jurisdictions(id);
ALTER TABLE roles ADD COLUMN IF NOT EXISTS qualifier       TEXT;
```

- `role_type_id`: typed office classifier (nullable for backward compat with
  existing free-text roles; populate going forward).
- `jurisdiction_id`: the enduring district identity (single FK). Never
  re-pointed on redistricting.
- `qualifier`: general disambiguator for otherwise-identical roles at the same
  org. Holds "Position 1", "Position 2", "At-Large", "Seat B", "Department 5".
  NULL when a role needs no disambiguation (e.g. a Senate seat, unique per
  district). Deliberately generic, not seat/position-specific.

### 3. Uniqueness: split the current index

Today `uq_role_org_title` on `(organization_id, lower(title))` would block two
"State Representative" rows per chamber. Replace with two partial indexes:

```sql
-- districted seats: identity = chamber + office + district + position
CREATE UNIQUE INDEX uq_role_seat ON roles
    (organization_id, role_type_id, jurisdiction_id, qualifier) NULLS NOT DISTINCT
    WHERE jurisdiction_id IS NOT NULL AND archived_at IS NULL;

-- non-districted (leadership, generic): keep title-based identity
CREATE UNIQUE INDEX uq_role_org_title ON roles
    (organization_id, lower(title))
    WHERE jurisdiction_id IS NULL AND archived_at IS NULL;
```

### 4. `role_assignments`: no change

Tenure stays person + role + `start_date` / `end_date` / `is_current`. District
and office are reached through the seat. Convention: **one assignment per
continuous tenure** in a seat (re-election without a gap keeps the same span; a
gap starts a new assignment).

## Worked example: LD-5

```
organizations:  WA Legislature
                  |- WA House  -- [seat] state_representative, LD-5, qualifier "Position 1"
                  |            -- [seat] state_representative, LD-5, qualifier "Position 2"
                  |- WA Senate -- [seat] state_senator,        LD-5, qualifier NULL
jurisdictions:  LD-5   (one row, referenced by all three seats)
role_assignments:  Alice -> LD-5 Position 1 seat, 2023-01-09 .. 2025-01-13 (is_current)
```

## Aggregation (all queries, no extra rows)

- All Representatives: `roles WHERE role_type_id = state_representative`
- All seats in LD-5: `roles WHERE jurisdiction_id = LD-5`
- All WA House: `roles WHERE organization_id = WA House` (join assignments for occupants)
- Whole Legislature: roles under `parent_id = WA Legislature` (org-hierarchy traversal)
- Everyone representing County X: jurisdiction graph (`is_fully_contained_by`) then join roles
- Full history of a seat: assignments for that one seat-Role, ordered by date

## Temporality and conventions

- **Tenure**: `role_assignments.start_date` / `end_date`.
- **Redistricting**: seat's `jurisdiction_id` points at the enduring district
  identity. Boundary versions (if ever populated) live in `jurisdictions`
  (`valid_from` / `valid_until`, `supersedes` / `evolved_from` edges). The seat
  is never re-pointed. Single-FK, YAGNI-friendly; a dated seat<->district
  junction was explicitly declined.
- **Seat lifecycle dates**: `roles` already carries `established_on` /
  `abolished_on` DATE columns (with a `chk_role_date_order` CHECK). These are the
  role-level "validity" dates — a seat created or abolished by reapportionment.
  No new date columns were added. Correction to an earlier draft that claimed
  role dates did not exist and pointed only at `archived_at`.
- **Archive vs abolish**: `roles.archived_at` remains soft-delete (data hygiene),
  distinct from `abolished_on` (the seat ceased to exist in the world).
- **Implemented as** issue #261 / branch `feature/legislator-seats`; the new
  columns are `role_type_id` (FK `role_types`), `jurisdiction_id`, `qualifier`,
  with a `chk_role_districted_needs_type` CHECK and split `uq_role_seat` /
  `uq_role_org_title` partial unique indexes.

## Open choices (defaults chosen)

1. `roles.title` stays `NOT NULL`: store a generated display label ("State
   Representative, LD-5, Position 1") for seats; querying uses the structured
   columns. Making title nullable + deriving display via a `v_role_display_names`
   view is a larger blast radius, deferred.
2. Assignment granularity: one assignment per continuous tenure. (Confirmed.)
3. Leadership (Speaker, Majority Leader): out of scope now; later either new
   `role_types` or title-based non-districted roles. The split index supports both.

## Out of scope (follow-on work)

- Backfill script converting any existing generic Rep/Senator roles into
  per-seat roles and assigning districts.
- Admin dashboard + public-API surfacing of the new columns.
- Seeding the 49 WA districts as jurisdictions and the 147 seat-Roles.
- Leadership / committee roles.
