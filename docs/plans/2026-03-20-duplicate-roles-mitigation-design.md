# Duplicate Roles & Role Assignments — Mitigation Design

**Date:** 2026-03-20

## Goal

Fix duplicate `roles` and `role_assignments` rows that accumulated due to a non-idempotent
import pipeline, and prevent recurrence with DB-level constraints.

## Root Cause

The import pipeline (`src/core/ingestion/pipeline.py`) deduplicated orgs and people via
DB queries before inserting. Roles did not: `role_index` was a pure in-memory dict,
empty at the start of every run. When CSV files changed (new file hash → new batch),
all roles were re-inserted as fresh rows. Assignment dedup then failed too, because it
queries `WHERE person_id = $1 AND role_id = $2` — but the newly generated `role_id`
never matched an existing assignment.

The `roles` table also had no unique constraint on `(organization_id, title)`, so the
DB offered no safety net.

## Approved Approach

### 1. Schema changes (`src/core/schema.sql`)

- Update header: `Requires PostgreSQL 14+` → `Requires PostgreSQL 15+`
  (needed for `NULLS NOT DISTINCT`; production instance is PostgreSQL 16)

- Add unique index on roles (one definition per org+title, excluding archived):
  ```sql
  CREATE UNIQUE INDEX IF NOT EXISTS uq_role_org_title
      ON roles (organization_id, lower(title))
      WHERE archived_at IS NULL;
  ```

- Add unique index on role_assignments (same person+role+start_date is a duplicate;
  NULLs treated as equal so unknown-start-date assignments are also deduplicated):
  ```sql
  CREATE UNIQUE INDEX IF NOT EXISTS uq_role_assignment_person_role_start
      ON role_assignments (person_id, role_id, start_date) NULLS NOT DISTINCT
      WHERE archived_at IS NULL;
  ```

- Drop the now-redundant partial index `uq_role_assignment_current`
  (the new index is strictly stronger).

### 2. One-time cleanup script (`scripts/deduplicate_roles.py`)

Run once against production after deploying the schema changes. Dry-run by default
(`--dry-run`); pass `--execute` to commit.

**Step A — Deduplicate roles:**
For each `(organization_id, lower(title))` group with count > 1:
- Canonical = row with the smallest `id` (ULID sort ≈ insertion order)
- `UPDATE role_assignments SET role_id = <canonical> WHERE role_id IN (<duplicates>)`
- Migrate polymorphic children (urls, contact_methods, social_links, identifiers,
  field_confidence rows) from duplicate role ids to canonical
- Delete duplicate role rows

**Step B — Deduplicate role_assignments:**
For each `(person_id, role_id, start_date)` group (NULLs equal) with count > 1:
- Canonical = smallest `id`
- Migrate polymorphic children to canonical
- Delete duplicate assignment rows

Both steps run inside a single transaction. Script prints a before/after summary
(duplicates found, rows removed).

### 3. Pipeline fix (`src/core/ingestion/pipeline.py`)

Before Pass 3, pre-populate `role_index` from the DB — mirroring the existing
pattern for orgs and people:

```python
for row in await conn.fetch(
    "SELECT id, organization_id, lower(title) AS title_lower "
    "FROM roles WHERE archived_at IS NULL"
):
    role_index[(row["organization_id"], row["title_lower"])] = row["id"]
```

Role insert uses `ON CONFLICT (organization_id, lower(title)) WHERE archived_at IS NULL DO NOTHING`
as a safety net (pre-population means it rarely fires).

Assignment dedup query updated to NULL-safe start_date comparison:

```sql
SELECT id FROM role_assignments
WHERE person_id = $1 AND role_id = $2 AND start_date IS NOT DISTINCT FROM $3
```

### 4. Testing

- **Unit:** add `transform_role` test asserting a pre-populated `role_index` yields
  `role_action = "matched"` without a new `role_id`
- **Integration:** run `run_import` twice against a real DB with the same files;
  assert `roles_loaded = 0`, `roles_matched = N` on the second run
- **Migration script:** integration test that seeds known duplicate roles and
  assignments, runs the script, asserts counts return to expected clean state

## Key Decisions

| Decision | Rationale |
|---|---|
| `NULLS NOT DISTINCT` on assignment unique index | Treats unknown start dates as equal — matches user intent; requires PG15+ |
| Partial indexes `WHERE archived_at IS NULL` | Consistent with archive model; archived records don't block re-creation |
| Pre-populate `role_index` from DB | Mirrors existing org/people dedup pattern; O(1) lookup per row during run |
| Canonical = smallest ULID | ULIDs are time-ordered; smallest ≈ earliest insert; deterministic |

## Out of Scope

- Dedup of orgs or people (already idempotent)
- Admin UI duplicate detection / merge UI
- Backfilling `start_date` on existing NULL assignments
