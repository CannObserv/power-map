# Assignment Inline Edit — Design Doc

**Date:** 2026-04-08
**Issue:** TBD

## Goal

Add inline edit support to the Assignments table on the Role detail page. Currently the table is read-only after creation; users must delete and re-create to correct date or status mistakes.

## Approved Approach

Row-level HTMX edit following the standard `edit-row` / `read-row` pattern (AGENTS.md, STYLE.md §15, §20, §27).

**Person is read-only in edit mode** — shown as a static link. Only `start_date`, `end_date`, and `is_current` are editable. Changing person is semantically a reassignment; delete + recreate is the right path.

## Routes

Three new routes added to `roles_detail.py` under the existing `/roles/{role_id}` prefix:

```
GET  /assignments/{ra_id}/read-row/   → _assignment_row.html (Cancel target)
GET  /assignments/{ra_id}/edit-row/   → _assignment_edit_row.html (new template)
POST /assignments/{ra_id}/edit-row/   → _assignment_rows.html on success; edit row re-render on error
```

## Template Changes

| File | Change |
|---|---|
| `_assignment_row.html` | Add `id="assignment-row-{{ ra.id }}"` on `<tr>`; add Actions `<td>` with Edit button (hidden when `ra.archived_at`) |
| `_assignment_edit_row.html` | New — person as static link, date inputs, is_current non-auto-save toggle, Save/Cancel |
| `detail.html` | Add `<th>` for Actions (5th column) |
| `_assignment_form_row.html` | Update `colspan` 4 → 5 |

## Re-sort on Save (§27)

`is_current` and `start_date` both appear in `ORDER BY is_current DESC, start_date DESC NULLS LAST`.
Save always returns `_assignment_rows.html` (full tbody replacement) targeting `#assignments-table tbody` with `hx-swap="innerHTML"` — not single-row `outerHTML`.

## Context Fix

All routes returning `_assignment_rows.html` must include `role_id` in template context so the read rows can render their Edit button URLs. The existing `assignment_create` route currently omits `role_id` — this is fixed as part of this work.

## Edit Row: `is_current` Toggle

Uses non-auto-save toggle pattern (§20): checkbox submits with the form (no `hx-post` on the input). Same end_date disable JS as the create form, but IDs scoped to `ra.id` (e.g. `is-current-cb-{{ ra.id }}`, `end-date-input-{{ ra.id }}`) to avoid conflicts if new-row and edit-row are simultaneously open.

## Error Handling

Mirrors the create route. On failure (bad date format, `is_current=True` + end_date, unique violation):
- Flash error via `flash_trigger`
- Re-render edit row with `HX-Retarget="#assignment-row-{ra_id}"` + `HX-Reswap="outerHTML"`
- HTTP 200 (HTMX ignores non-2xx by default)

Non-HTMX fallback: `RedirectResponse` to the role detail page.

## Archived Guard

Edit button in `_assignment_row.html` is wrapped in `{% if not ra.archived_at %}` per §26.

## Out of Scope

- Delete / archive assignment (separate feature)
- Editing the person field on an existing assignment
- Archive/unarchive assignment rows
