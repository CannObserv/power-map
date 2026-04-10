# Design: Person-detail Assignment CRUD

**Date:** 2026-04-10
**Issue:** TBD
**Builds on:** #75 (role-detail assignment inline edit)

## Goal

Add inline create and edit to the Role Assignments table on the Person detail page, following the same row-level HTMX patterns established for the Role detail page.

## Approved Approach

### New module: `people_assignments.py`

Router prefix `/people/{person_id}/assignments`, mounted in `router.py`.

| Method | Path | Returns |
|---|---|---|
| `GET` | `/new-row/` | `_assignment_form_row.html` |
| `POST` | `/` | `_assignment_rows.html` (full tbody, re-sorted) |
| `GET` | `/{ra_id}/read-row/` | `_assignment_row.html` |
| `GET` | `/{ra_id}/edit-row/` | `_assignment_edit_row.html` |
| `POST` | `/{ra_id}/edit-row/` | `_assignment_rows.html` (full tbody, re-sorted) |

Validation mirrors role-detail: `CheckViolationError` (is_current + end_date conflict), `UniqueViolationError` (duplicate person+role+start_date). Errors re-render the form row in place via `HX-Retarget`/`HX-Reswap` headers.

### New role search endpoint

`GET /admin/roles/search/?q=...` in `roles.py`. Returns `<li data-id="{role_id}" data-label="{Org} — {Title}">` items — same shape as `/admin/people/search/`. Used by the new-row form's role typeahead.

### Templates (4 new, all under `admin/people/partials/`)

- `_assignment_row.html` — org linked to org detail, role title linked to `/admin/roles/{role_id}/`, start, end, status badge, Edit button (hidden if `archived_at`). Row `id="person-assignment-row-{ra.id}"`.
- `_assignment_rows.html` — loops `_assignment_row.html`; target is `#person-assignments-table tbody`.
- `_assignment_form_row.html` — `colspan=6`, role typeahead (same JS pattern as person-typeahead on role-detail), date fields, is_current toggle. is_current disables end_date. Cancel removes row.
- `_assignment_edit_row.html` — org + role title as read-only text, start_date/end_date/is_current editable. is_current ↔ end_date JS linkage. Save/Cancel buttons.

### Changes to existing files

- **`people.py`** (person detail query): add `r.id AS role_id` to role_assignments SELECT.
- **`people/detail.html`**: add `id="person-assignments-table"`, 6th `<th>` (Actions), `+ Add assignment` button (hidden if `person.archived_at`), switch inline `{% for %}` to `{% include "_assignment_row.html" %}`.
- **`router.py`**: mount `people_assignments.router`.

## Key Decisions

- **Person is fixed** on person detail: new-row form has a role typeahead, not a person typeahead. Edit-row shows role + org as read-only text.
- **Role title links** to `/admin/roles/{role_id}/` — adds `role_id` to the query.
- **Re-sort on save**: returns full `_assignment_rows.html` tbody replacement (same as role-detail §27 pattern).
- **Archived guard**: no Edit button on archived assignments; `GET /edit-row/` returns 409.

## Out of Scope

- Archiving/deleting assignments from person detail (use the standalone role-assignment detail pages)
- Notes field (not exposed in the role-detail inline form either)
