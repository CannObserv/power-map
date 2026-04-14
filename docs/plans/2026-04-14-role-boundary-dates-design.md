# Role Boundary Dates Design

**Date:** 2026-04-14
**Issue:** #99
**Status:** Approved

## Goal

Add optional `established_on` and `abolished_on` date fields to roles. When set, any assignment's `start_date` / `end_date` must fall within those boundaries — hard block on violation.

## Approved Approach

Application-level boundary enforcement (PostgreSQL CHECK constraints cannot reference other tables), with a DB-level intra-row order constraint on the role itself.

## Schema Changes (`src/core/schema.sql`)

Add two nullable `DATE` columns and one intra-row check to `roles`:

```sql
established_on  DATE,
abolished_on    DATE,
CONSTRAINT chk_role_date_order
    CHECK (established_on IS NULL OR abolished_on IS NULL
           OR established_on <= abolished_on)
```

Migration blocks (in the `-- Column additions` section):

```sql
DO $$ BEGIN
  ALTER TABLE roles ADD COLUMN established_on DATE;
EXCEPTION WHEN duplicate_column THEN NULL; END $$;

DO $$ BEGIN
  ALTER TABLE roles ADD COLUMN abolished_on DATE;
EXCEPTION WHEN duplicate_column THEN NULL; END $$;

DO $$ BEGIN
  ALTER TABLE roles ADD CONSTRAINT chk_role_date_order
    CHECK (established_on IS NULL OR abolished_on IS NULL
           OR established_on <= abolished_on);
EXCEPTION WHEN duplicate_object THEN NULL; END $$;
```

## Boundary Validation Helper

New `_check_assignment_within_bounds(start_date, end_date, role) -> str | None` in `roles_assignments_inline.py`. Pure logic, returns an error string or `None`. Rules:

- If `established_on` set: `start_date` (if set) >= `established_on`; `end_date` (if set) >= `established_on`
- If `abolished_on` set: `start_date` (if set) <= `abolished_on`; `end_date` (if set) <= `abolished_on`

Called from `assignment_create` and `assignment_edit_row_post` before the DB write.

## Inline Editing — Role Detail

New routes in `roles_detail.py` under `/roles/{role_id}/inline/dates/`:

- `GET /inline/dates/` → `_dates_read.html`
- `GET /inline/dates/edit/` → `_dates_form.html`
- `POST /inline/dates/` → validates new boundaries against all existing active assignments; on conflict returns form partial with flash error (count of violating assignments is sufficient); on success returns `_dates_read.html`

`_get_role` extended to select `established_on, abolished_on` — both inline files get the fields automatically since `roles_assignments_inline.py` imports `_get_role`.

## UI

- **Role detail** — new "Dates" row in the Details card between Title and Notes; inline read/edit toggle (same pattern as existing fields).
- **Assignment form rows** (`_assignment_form_row.html`, `_assignment_edit_row.html`) — small muted hint line showing role boundaries when at least one is set, e.g. _"Role active: 2010-01-01 – 2020-12-31"_.

## Test Surface

- `tests/api/admin/test_roles_detail_inline.py` — new `/inline/dates/` routes: read, edit, save valid, save order-violation, save conflicts-with-existing-assignments
- `tests/api/admin/test_roles_assignments_inline.py` — boundary enforcement on create and edit
- Schema constraint test for `chk_role_date_order`

## Key Decisions

| Decision | Rationale |
|---|---|
| Hard block on boundary save if assignments violate | Consistent with rest of validation model; admin UI = deliberate edits |
| App-level assignment enforcement | PostgreSQL CHECK can't span tables; trigger complexity not warranted |
| DB constraint only for `established_on <= abolished_on` | Single-row check; safe and cheap |
| `_get_role` extended (not a new fetch) | Both detail and assignment inline files already call it; zero duplication |

## Out of Scope

- Ingestion CSV support for the new fields
- Boundary violations on archived assignments
- Bulk-fix tooling for existing data
