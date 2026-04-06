# Role Detail Redesign

## Goal

Modernize the role detail page to match org detail patterns: replace the monolithic "Edit" form with inline editing, add "+ Add assignment" to the assignments table, and rename "Assignment History" to "Assignments".

## Approved Approach

### Page Layout (top to bottom)

1. **Page header** — `<h1>` with role title, entity type label "Role". No edit button.
2. **Details section** (entity-card)
   - Status badge (Active/Archived) — read-only (roles have no `active` column)
   - Organization — inline read/edit with typeahead search (reuses existing `GET /admin/orgs/search/`)
   - Title — inline read/edit with text input
   - Notes — inline read/edit (same pattern as org notes)
3. **Assignments section** (renamed from "Assignment History")
   - Table: Person, Start, End, Status, (Actions column TBD for future edit/delete)
   - "+ Add assignment" button inserts form row at top of tbody
   - Form row: person typeahead + `type="date"` start/end + `is_current` checkbox + Save/Cancel
4. **Metadata footer** — muted text: ID, Created, Updated
5. **Danger Zone** — archive/delete (unchanged)

### New Routes

**`src/api/admin/roles_detail.py`** — sub-router for inline editing on a single role:

| Method | Path | Returns |
|--------|------|---------|
| GET | `/{role_id}/inline/org/` | org read partial |
| GET | `/{role_id}/inline/org/edit/` | org typeahead form |
| POST | `/{role_id}/inline/org/` | org read partial + flash |
| GET | `/{role_id}/inline/title/` | title read partial |
| GET | `/{role_id}/inline/title/edit/` | title text form |
| POST | `/{role_id}/inline/title/` | title read partial + flash |
| GET | `/{role_id}/inline/notes/` | notes read partial |
| GET | `/{role_id}/inline/notes/edit/` | notes form |
| POST | `/{role_id}/inline/notes/` | notes read partial + flash |
| GET | `/{role_id}/assignments/new-row/` | blank assignment form row |
| POST | `/{role_id}/assignments/` | assignment tbody (re-sorted) |

**People search** (new endpoint in `src/api/admin/people.py`):

| Method | Path | Returns |
|--------|------|---------|
| GET | `/people/search/?q=...` | `_search_results.html` (`<li>` items) |

### New Templates

`src/templates/admin/roles/partials/`:
- `_org_read.html`, `_org_form.html` — org inline read/edit
- `_title_read.html`, `_title_form.html` — title inline read/edit
- `_notes_read.html`, `_notes_form.html` — notes inline read/edit
- `_assignment_row.html` — single assignment read row
- `_assignment_rows.html` — full tbody replacement after create
- `_assignment_form_row.html` — person typeahead + date inputs + Save/Cancel

`src/templates/admin/people/partials/`:
- `_search_results.html` — typeahead result items

### Key Decisions

- **No `active` toggle on roles** — roles only have `archived_at`, no `active` boolean. Status is read-only badge.
- **Organization editable inline** — typeahead search, reuses existing org search endpoint.
- **Title editable inline** — simple text input in details card (not heading click-to-edit).
- **Native `type="date"` inputs** — no `dateparser` dependency. Consistent with existing RA form. Upgrade path exists later.
- **Person typeahead** — new `GET /people/search/` endpoint. Same pattern as org search.
- **`is_current` / `end_date` constraint** — client-side: checkbox disables end_date input. Server-side: catch `CheckViolationError`.
- **Unique constraint** — catch `UniqueViolationError` on `(person_id, role_id, start_date)`, return form with error.
- **Sub-router in `roles_detail.py`** — keeps inline editing routes separate from list/create/archive in `roles.py`.

### Out of Scope

- Inline edit/delete on existing assignment rows (future work)
- Role merge from role detail page
- `active` boolean column on roles table
