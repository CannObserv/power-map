# Design — linked-entity typeahead on admin event form (#172)

**Date:** 2026-06-26
**Branch:** `feature/172-linked-entity-typeahead`
**Issue:** #172

## Goal

Replace the raw `linked_entity_id` text input on the admin event create/edit
form with the existing typeahead combobox. Admins search people or organizations
by name (scoped by the `linked_entity_type` select), and selection resolves to a
ULID stored in a hidden field. Add edit-mode name prefill and server-side
validation of the linked entity.

## Context

The form was refactored after #172 was filed: the per-entity templates the issue
names are now 3-line wrappers around one shared template,
`src/templates/admin/shared/_event_form_row.html` — the only template changed.

Reusable infra already exists:
- `window.initTypeaheadCombobox({inputId, listboxId, hiddenId, onSelect})`
  — `src/static/admin/typeahead-combobox.js`, globally loaded in `admin/base.html`.
- Per-entity search routes `GET /admin/people/search/` and `/admin/orgs/search/`
  return identical `<li data-id data-label>` fragments.

The linked-entity section is only shown when the selected event type has
`requires_linked_entity = true` (existing inline script); that behavior is kept.

`linked_entity_id` is a **polymorphic reference** (person OR organization), so no
FK is possible — app-level validation is the only integrity guard, and today
there is none (create/edit store arbitrary posted text).

## Approved approach

### 1. Shared lookup helper — `src/api/admin/entity_lookup.py` (new)
- `search_entities(db, entity_type, q) -> list[Record]` — dispatch to the people
  or org display-name query (lifts SQL already in `people.py` / `orgs.py`).
- `resolve_entity_label(db, entity_type, entity_id) -> str | None` — display name
  or `None`. One helper serves **both** validation (`None` ⇒ invalid) and
  edit-prefill (the label). Existence check is **not** archived-filtered so that
  editing other fields on an event whose target was later archived still works.

### 2. Unified search route — `src/api/admin/entity_search.py` (new)
`GET /admin/entities/search/?q=&linked_entity_type=`, mounted under
`/admin/entities` in `router.py`. Calls `search_entities`, renders a new shared
`admin/shared/_entity_search_results.html` (same `<li data-id data-label>`
markup). Existing people/orgs search routes left untouched.

Chosen over mutating the input's `hx-get` in JS (option A) because it keeps the
HTMX attributes static, lets the type ride along via `hx-include`, and
centralizes the polymorphic "search either table" logic that validation reuses.

### 3. Template — `admin/shared/_event_form_row.html`
- Replace the `linked_entity_id` text input with: visible search input + hidden
  `linked_entity_id` + `<ul class="typeahead-results">` listbox, all ids
  namespaced by `{{ ev.id or 'new' }}` (multiple edit rows can be open at once).
- Input: static `hx-get="/admin/entities/search/"`,
  `hx-include="[name='linked_entity_type']"`; visible value = `linked_entity_label`
  on edit.
- Extend the existing inline script: call `initTypeaheadCombobox(...)`; on
  `linked_entity_type` change, clear hidden + visible value and disable the
  search input until a type is chosen.

### 4. Backend prefill + validation — `src/api/admin/_events_shared.py`
- Edit-row GET and form-error re-renders resolve `linked_entity_label` via the
  helper and pass it through `_form_response` / `_ctx`.
- Create + edit POST: after the existing `requires_linked` check, when
  `linked_entity_id` is set → reject if `linked_entity_type` ∉
  {person, organization} or `resolve_entity_label(...)` is `None`
  ("Linked entity not found."), using the existing form-error pattern.

### 5. Tests (TDD)
- Python: route dispatch (person / org / empty type / empty q); edit-row GET
  carries the label; create + edit validation reject (bad type, nonexistent id)
  and accept (valid id).
- Vitest: type-switch clears prior selection; per-row id isolation.

## Out of scope
- Public API (no change to public event read endpoints).
- Event types without `requires_linked_entity` (section stays hidden as today).
- Deleting/altering the existing `/admin/people/search/` and `/admin/orgs/search/`
  routes.
