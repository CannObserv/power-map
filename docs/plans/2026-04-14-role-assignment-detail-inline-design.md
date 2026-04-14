# Role Assignment Detail — Inline Editing Refactor

**Date:** 2026-04-14
**Status:** Approved, ready for implementation

## Goal

Bring the Role Assignment detail page in line with STYLE.md §15/§16/§17 and the inline-edit patterns used on org/person/role detail pages. Replace the legacy full-page `/edit/` form with per-field inline editing. Person and role are immutable after creation — correcting a wrong assignment is delete + recreate.

## Background

[src/templates/admin/role_assignments/detail.html](../../src/templates/admin/role_assignments/detail.html) predates the current inline-edit conventions. It renders fields via `<dl class="detail-grid">`, routes edits through a full-page form ([src/api/admin/role_assignments.py](../../src/api/admin/role_assignments.py) `ra_edit_form` / `ra_update`), mixes metadata with editable fields, uses raw `<form>` POST for archive (no confirmation modal), and targets `body` for delete instead of the shared `delete_modal.html`. Reference conformant page: [src/templates/admin/roles/detail.html](../../src/templates/admin/roles/detail.html).

## Key decisions

- **Person and role locked after creation.** No inline edit path; correcting a wrong assignment means archive+delete then recreate. Rationale: re-pointing is rare; avoids the cost of two typeahead comboboxes (STYLE.md §18) and keeps scope tight.
- **Three editable fields:** `is_current` (auto-save toggle), `start_date`+`end_date` (combined dates partial, co-validated), `notes` (§15 Notes pattern).
- **No live header sync.** Composed title `Person – Role @ Org` cannot mutate from this page (person/role locked; dates/notes/current don't affect title). Upstream renames are picked up on next navigation.
- **CHECK-violation UX:** `is_current` toggle re-renders prior state + error flash; dates edit re-renders form with inline `.alert--error` (STYLE.md pattern for contact form inline errors).

## In scope

### Phase 1 — Structure & metadata

- Wrap content in `<section class="entity-section"><h2>Details</h2>` + `.entity-card`, matching [roles/detail.html:16-37](../../src/templates/admin/roles/detail.html#L16-L37).
- Status as a `field-group-label` row (badge variants unchanged: Current / Former / Archived).
- Person and Role render as **read-only rows** with links to their detail pages.
- Move ID / Created / Archived into the muted metadata footer paragraph.
- Remove `<dl class="detail-grid">`.

### Phase 2 — Inline editing

New partials under `src/templates/admin/role_assignments/partials/`:

1. `_is_current_toggle.html` — auto-save checkbox (precedent: `_active_toggle.html`). `hx-post` with `hx-include="this"`. Disabled when `archived_at IS NOT NULL`. On CHECK violation (flipping to `true` while `end_date IS NOT NULL`), re-render prior state + `flash_trigger("error", ...)`.
2. `_dates_read.html` / `_dates_edit.html` — single subsection covering both dates, row-level edit pattern (GET edit → form; POST → read; GET read → read; cancel reverts). On CHECK violation, re-render edit partial with inline `.alert--error` and repopulated values (HTTP 200 for HTMX swap).
3. `_notes_read.html` / `_notes_edit.html` — STYLE.md §15 Notes pattern verbatim.

New routes in [src/api/admin/role_assignments.py](../../src/api/admin/role_assignments.py):

- `POST /role-assignments/{id}/inline/is_current/`
- `GET  /role-assignments/{id}/inline/dates/`
- `GET  /role-assignments/{id}/inline/dates/edit/`
- `POST /role-assignments/{id}/inline/dates/`
- `GET  /role-assignments/{id}/inline/notes/`
- `GET  /role-assignments/{id}/inline/notes/edit/`
- `POST /role-assignments/{id}/inline/notes/`

Each mutation emits `flash_trigger("success", ...)`. Non-HTMX fallback: `RedirectResponse` to detail page.

Routes removed:

- `GET  /role-assignments/{id}/edit/` (`ra_edit_form`)
- `POST /role-assignments/{id}/edit/` (`ra_update`)
- [form.html](../../src/templates/admin/role_assignments/form.html) loses its edit-mode branch (`{% if ra %}`); create path unchanged.
- `_fetch_people` / `_fetch_roles` stay (still used by `/new/`).

### Phase 3 — Destructive actions

- Archive: replace raw `<form>` POST with `<button hx-post hx-confirm>` using `admin-modal.js` (STYLE.md §16). `data-confirm-label="Archive"`, default danger variant. Target Status field-group for in-place update.
- Delete: switch to shared `delete_modal.html` pattern (handles 409 + network errors inline).
- Both guarded by `archived_at` state as today.

## Tests

- **New** `tests/api/admin/test_role_assignments_inline.py`: per field, assert GET read/edit, POST success, POST CHECK-violation re-render, archived-entity edit-button guards.
- **Update** [tests/api/admin/test_role_assignments.py](../../tests/api/admin/test_role_assignments.py): remove assertions on deleted `/edit/` routes; add structural assertions (section wrapper, metadata footer, no `<dl>`).
- `tests/api/admin/test_js.py`: no changes — no new JS file.

## Out of scope

- Typeaheads for person/role (locked fields).
- Live header sync (`role-assignment-detail.js`, `role_assignment_header_extra`) — composed title doesn't mutate from this page.
- Changes to list view, create form body, or `_region.html`.

## Rollout

Three commits on a feature branch:

1. `refactor: restructure role assignment detail layout` — Phase 1, no behavior change.
2. `feat: inline editing for role assignment fields` — Phase 2, removes `/edit/` routes.
3. `refactor: role assignment destructive actions use shared modal` — Phase 3.
