# Phase 2 — Person Name Admin UI Design

**Date:** 2026-05-04
**Issue:** #123
**Status:** Approved
**Phases covered:** 2a, 2b, 2c, 2d

## Goal

Surface the schema added in #121 in the admin UI: `locale`, `script`, `sort_as`, `visibility`, `reading_of_id`, expanded `name_type`, and the `person_name_parts` sidecar. The schema is in production; today every existing row defaults to `visibility='public'` and the UI is byte-identical to pre-#121. Phase 2 makes the new fields editable.

## Background

#121 landed:

- New columns on `person_names`: `locale`, `script`, `sort_as`, `visibility`, `reading_of_id`.
- New `person_name_parts` sidecar table (1:0..1, ON DELETE CASCADE).
- Expanded `name_type` CHECK: 12 values (added `maiden`, `religious`, `stage`, `deadname`, `reading`, `romanization`, `mrz`).
- `trg_deadname_visibility` trigger coerces `deadname + public` → `legal_only`.
- `v_person_display_names` filters `visibility='public'`.
- Visibility-rule lint test on raw `person_names` access.

Existing UI, before any Phase 2 work:

- Person detail page at [src/templates/admin/people/detail.html](src/templates/admin/people/detail.html) renders names as a 4-column table (`name`, `name_type`, `is_canonical`, actions).
- Read row template: [src/templates/admin/people/partials/_name_row.html](src/templates/admin/people/partials/_name_row.html).
- Edit/new form: [src/templates/admin/people/partials/_name_form_row.html](src/templates/admin/people/partials/_name_form_row.html) — a flex form in a `colspan=4` cell, posting to `/admin/people/{person_id}/names/...`.
- Shared router: [src/api/admin/_names_shared.py](src/api/admin/_names_shared.py) backs both org and person name CRUD via `make_names_router()` — generic over `names_table`, `entity_fk`, etc.

## Approved Approach

**Edit drawer per row.** The inline read row stays as-is (4 columns). Clicking "Edit" replaces the row with a form-row that has the existing flex form on top and a **disclosure-revealed metadata drawer underneath** containing all Phase 2 fields. The drawer is part of the same `<tr><td colspan="4">` cell — visually nested under the row, not a modal overlay.

**Shared router stays generic.** `_names_shared.py` keeps its org/person duality. Person-only fields (visibility, locale, script, sort_as, reading_of_id, parts) are gated behind a `supports_metadata: bool = False` config flag passed to `make_names_router()`. When True, the router accepts the extra Form fields, persists them, and renders the extended templates. Org name CRUD passes False and continues working unchanged.

## Phasing

Each phase is independently shippable. They land as separate plans, reviewed and committed in sequence.

### Phase 2a — Visibility + expanded `name_type` + deadname disclosure toggle

**Smallest user-visible change. Highest urgency** (it's the one that protects subjects with deadnames the moment any data is marked).

Schema fields exposed: `visibility`, expanded `name_type`.

UI changes:

- `_name_form_row.html` gains two new selects: `visibility` (3 values) and the expanded `name_type` (12 values).
- `_name_row.html` gains visibility badges where non-`public`: `[legal-only]`, `[hidden]`. (`public` rows render exactly as today.)
- New person-detail toggle: **"Show legal/historical names"** (default collapsed). When collapsed, the names table renders only rows with `visibility='public'`. When expanded, all rows render. Per-page session — no persistence, resets on navigation.
- Confirmation dialog (HTMX `hx-confirm` or a custom modal) when an admin selects `name_type='deadname'`: *"Marking this as a deadname will hide it from public listings — visibility will be set to legal_only. Confirm?"*. The DB trigger does the coercion regardless; the dialog explains the side effect.

Router changes:

- `_names_shared.py` accepts `visibility` Form field when `supports_metadata=True`.
- `make_names_router(...)` for `person_names` passes `supports_metadata=True`; for `organization_names` passes `False` (default).
- New person-detail handler returns a different filter set on the names table depending on a query param (e.g. `?show_historical=1`).

Backend tests (TDD red→green):

- POST a new name with `visibility='legal_only'` → DB row reflects it.
- POST a name with `name_type='deadname'` and `visibility='public'` → trigger coerces; DB row has `visibility='legal_only'`.
- GET person-detail without `?show_historical=1` → response excludes hidden / legal_only rows.
- GET person-detail with `?show_historical=1` → response includes them.
- POST a `visibility='legal_only'` name → person list/search results do NOT show that name (even if it's the canonical row, the view filter holds).
- All 12 `name_type` values accepted by the form; `'invalid'` rejected with HTTP 400 or form re-render with error.

Frontend tests (Vitest):

- Disclosure toggle script: clicking "Show legal/historical names" toggles a CSS class / data attribute on the names table.
- Confirm dialog: changing `name_type` to `deadname` triggers the confirm; cancelling returns the select to its previous value.

### Phase 2b — Locale + script + sort_as

Schema fields exposed: `locale`, `script`, `sort_as`.

UI changes:

- Edit drawer adds three inputs: `locale` (BCP 47 typeahead), `script` (ISO 15924 typeahead), `sort_as` (free text).
- Read row gains a small subtitle line under the name when any are set: e.g. "Latn · en-US" or "sort_as: van der Meer".
- Person list, search, typeahead all change `ORDER BY name` → `ORDER BY COALESCE(sort_as, name) COLLATE "und-x-icu"`.

Typeahead data:

- BCP 47: pre-seeded list of ~60 common locales (`en-US`, `en-GB`, `es-ES`, `es-MX`, `zh-Hant-TW`, `zh-Hans-CN`, `ja-JP`, `is-IS`, `pt-BR`, …). Free-input fallback allowed (any non-empty string accepted at the form level; DB has no CHECK on the column).
- ISO 15924: pre-seeded list of ~20 common scripts (`Latn`, `Hans`, `Hant`, `Kana`, `Hira`, `Cyrl`, `Arab`, `Hang`, …). Free-input fallback allowed.

Both typeaheads use the existing `typeahead-combobox` component documented at [tests/js/typeahead-combobox.test.js](tests/js/typeahead-combobox.test.js).

Backend tests:

- POST a name with `locale='en-US'`, `script='Latn'`, `sort_as='Foo Bar'` → DB row reflects all three.
- Person list with two names ('Åberg', 'Aaron') in `und-x-icu` collation: 'Åberg' sorts after 'Aaron' (vs. ASCII-sort which inverts depending on case).

### Phase 2c — Linked names (`reading_of_id`)

Schema fields exposed: `reading_of_id`.

UI changes:

- When the admin selects `name_type` in `('reading', 'romanization', 'mrz')`, a new "Reading of" field appears in the drawer. It's a typeahead scoped to the SAME person's existing names where `name_type` is *not* in `('reading', 'romanization', 'mrz')` — only "visual" rows are link targets.
- Read row gains a parent-link indicator: a tree-row indent + "↳ romanization of *<visual name>*" subtitle. The visual row is the parent; readings nest under it.
- ON DELETE CASCADE is already enforced at DB level — deleting a visual row removes its children automatically. UI surfaces this in the delete-confirm modal: *"This name has 2 linked phonetic / romanization rows that will also be deleted."*

Display order on the names table: visual rows first (sorted by canonical/type/name), each immediately followed by its child reading rows.

Backend tests:

- POST a `name_type='romanization'` row with `reading_of_id` pointing to a row from a *different* person → 400.
- POST without `reading_of_id` when `name_type='romanization'` → form re-renders with error (or save with NULL — design decision: NULL is allowed at DB level, so we make it a UI hint, not a hard requirement).
- Delete a visual row → cascade verified by re-querying the table.

### Phase 2d — Structured parts editor (`person_name_parts`)

Schema fields exposed: `person_name_parts.*` (the sidecar).

UI changes:

- Drawer gets a new "Structured parts" section. When the parts row doesn't exist yet, the section shows a single "Add structured parts" button. Clicking expands the editor and creates an in-memory parts row that's persisted on Save.
- Editor controls (in this order):
  - `primary_identifier` dropdown: `family` / `given` / `patronymic` / `mononym` (or unset).
  - `given_names[]` — orderable list of up to 5 chips/inputs. Reorder via up/down buttons (no drag-drop initially; YAGNI).
  - `family_names[]` — same.
  - `additional_names[]` — same.
  - `honorific_prefix` — free text.
  - `honorific_suffix` — free text.
- "Remove structured parts" button at the bottom of the editor → deletes the `person_name_parts` row.

Form encoding:

- Arrays serialised as `given_names[]=María&given_names[]=José&...`. FastAPI `List[str]` form field handles this natively.
- Validation: max 5 elements per array. Empty strings filtered out before INSERT.

Backend tests:

- POST with `given_names=['María', 'José']`, `family_names=['García', 'López']`, `primary_identifier='family'` → upsert into `person_name_parts`; round-trip read returns the same arrays.
- POST with 6 array elements → form rejected with 400 / re-render.
- POST with `primary_identifier='invalid'` → DB CHECK rejects; UI surfaces error.
- DELETE the parent `person_names` row → `person_name_parts` row gone (cascade).

## Cross-cutting decisions (apply across all phases)

- **Edit drawer** is a single HTMX swap — clicking Edit fetches the full edit-form fragment (existing flex row + drawer) in one request. No multi-step state.
- **Drawer state** isn't persisted between edits — collapsed by default on every Edit click; admin expands manually.
- **Deadname toggle** scope is per-page session: the URL gains `?show_historical=1` when toggled on. Reload preserves; navigation away resets.
- **Any admin** can use the toggle; no fine-grained capability gating (we have a single role).
- **No audit log** for viewing legal/hidden/deadname rows. Out of scope; YAGNI for the current threat model.
- **Pre-existing `_names_shared.py` callers** (org name CRUD) are unchanged. The `supports_metadata` flag defaults to False; org code passes nothing.

## Schema-side things still owed (no schema change)

- **Confirmation dialog when `name_type → deadname`** — pure UI, no schema.
- **Lint test** for raw `person_names` access — already exists; new files added in Phase 2 must comply or be added to allow-list.
- **`v_person_display_names`** already filters `visibility='public'`. The deadname-toggle reads names directly from `person_names` with the toggle's filter, so it must be in `ALLOWED_DIRECT_ACCESS` (the existing person-detail handler is already in the allow-list).

## Files Touched (anticipated)

### Phase 2a

- Modify: `src/api/admin/_names_shared.py` — add `supports_metadata` flag + `visibility` Form field.
- Modify: `src/api/admin/people.py` — accept `?show_historical=1`; pass filter to template.
- Modify: `src/api/admin/people_names.py` — pass `supports_metadata=True`.
- Modify: `src/templates/admin/people/detail.html` — toggle UI.
- Modify: `src/templates/admin/people/partials/_name_form_row.html` — add visibility select + expanded name_type.
- Modify: `src/templates/admin/people/partials/_name_row.html` — visibility badge.
- Create: `src/static/admin/person-name-deadname-confirm.js` — confirm dialog when name_type changes to `deadname`.
- Create: `tests/api/admin/test_people_names_phase2a.py` — backend coverage.
- Create: `tests/js/person-name-deadname-confirm.test.js` — JS coverage.

### Phase 2b

- Modify: `_names_shared.py` — locale/script/sort_as form fields.
- Modify: name form template — typeahead inputs.
- Modify: `src/api/admin/people.py` — switch sort to `und-x-icu` collation.
- Create: BCP 47 + ISO 15924 lookup data files (`src/api/admin/static/locales.json`, `scripts.json` or constants module).
- Tests: typeahead JS + sort-order integration test.

### Phase 2c

- Modify: name form template — `reading_of_id` typeahead conditional on `name_type`.
- Modify: name list template — child-row indent + subtitle.
- Add: typeahead route filtering to the same person's visual rows.
- Tests: typeahead scope, cross-person link rejection, cascade-on-delete display.

### Phase 2d

- Create: `src/api/admin/people_name_parts.py` — CRUD router for the sidecar.
- Create: `src/templates/admin/people/partials/_name_parts_editor.html` — drawer section.
- Modify: name form template — embed parts editor.
- Tests: form parsing, save round-trip, cascade.

## Out of Scope (deferred)

- **Phase 3** (separate issue): Public API additive fields (`PersonName` Pydantic model gains locale/script/visibility/parts), `Accept-Language` content negotiation.
- **Phase 4** (separate plans per source): Ingestion changes to populate structured parts when source data carries them.
- **Audit logging** for legal/hidden/deadname views.
- **Fine-grained role/capability gating** (we have a single admin role).
- **Drag-and-drop reordering** of structured parts (YAGNI; up/down buttons suffice for max-5 lists).
- **vCard 4.0 export** endpoint.
- **ICAO MRZ auto-generation** pipeline.
- **Backfilling `locale='en'` / `script='Latn'`** on existing rows (intentionally left NULL).

## Open Question (one)

**Confirmation dialog mechanism for `name_type → deadname`.** The existing admin uses HTMX `hx-confirm` for simple yes/no on delete. A `<select>` change isn't a navigation event, so `hx-confirm` doesn't apply directly. Options:

1. JS listener on the select element: `change` → `confirm()` → revert if cancelled.
2. Submit-time check: form submits without confirmation; server returns 409 with a "are you sure" modal if `name_type='deadname'` and the user hasn't checked a hidden `confirmed_deadname` field.

(1) is simpler and matches the user's intent ("show a confirmation"). The plan defaults to (1). Flagging as the only open UX question.
