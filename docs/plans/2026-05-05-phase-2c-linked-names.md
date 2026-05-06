# Phase 2c — Linked Names (`reading_of_id`)

**Issue:** #123 (Phase 2c sub-task)
**Branch:** `feat/123-phase-2c-linked-names` (re-using the Phase 2b worktree)
**Design:** `docs/plans/2026-05-04-phase-2-person-name-ui-design.md` § Phase 2c

**Goal:** Surface the Phase-1 `reading_of_id` column in the admin UI so admins can link a `reading` / `romanization` / `mrz` row to its parent visual row. Visual rows render first; child rows render indented underneath with a "↳ romanization of <parent>" subtitle. Cross-person links are rejected. ON DELETE CASCADE is already enforced at the DB level (Phase 1) — UI just needs to mention the cascade in the delete confirm.

**Architecture:**
- Backend: `_names_shared.py` accepts `reading_of_id` Form field gated by `supports_person_metadata`. Validation: when `name_type ∈ {'reading','romanization','mrz'}`, the field is optional but must reference a row on the *same person* whose `name_type` is NOT in that set. FK violations + cross-person + circular-link errors surface as form errors (HTMX flash + non-HTMX 422), never 500.
- New typeahead endpoint `GET /admin/people/{person_id}/_reading_target_search`: returns same-person rows whose `name_type` is in the visual set (everything except reading/romanization/mrz).
- Form template: conditional typeahead block shown by JS when `name_type` is in the reading set.
- Read row: indent + subtitle when `n.reading_of_id` is set.
- Delete confirm: count cascade children and surface in the dialog message.

**Pre-conditions (already in place):**
- `person_names.reading_of_id TEXT REFERENCES person_names(id) ON DELETE CASCADE` — Phase 1.
- `make_names_router(supports_person_metadata=True)` builder pattern — Phase 2a/2b.

---

## Task 1: Backend — `reading_of_id` Form field + typeahead endpoint

**Files:**
- Modify: `src/api/admin/_names_shared.py` — add `reading_of_id` to Form signature + `_metadata_pairs` ordering tuple. Same-person + visual-target validation in handler.
- Create: `src/api/admin/people_reading_target_search.py` — `GET /admin/people/{person_id}/_reading_target_search?q=<term>` returning HTML option rows.
- Modify: `src/api/admin/router.py` — mount the new search router (before `people_module` for path priority).
- Create: `tests/api/admin/test_people_reading_target_search.py` (~10 tests).
- Create: `tests/api/admin/test_people_names_reading_of_id.py` (~10 tests).

**Done when:**
- Create + edit accept `reading_of_id`; round-trip persists.
- POST with cross-person `reading_of_id` → form error (HTMX flash, non-HTMX 422), no row written.
- POST with `reading_of_id` pointing to another reading row (e.g. self-reference, or chain) → form error.
- Empty `reading_of_id` allowed for any `name_type`; the column is nullable at DB level.
- Typeahead endpoint returns only same-person rows with visual `name_type`; sorted; capped; auth-guarded; uses `escape_like` + `ESCAPE '\'`.
- Org-side path inert (no reading_of_id column on organization_names).

## Task 2: Templates — conditional typeahead + read-row subtitle + delete-confirm cascade hint

**Files:**
- Modify: `src/templates/admin/people/partials/_name_form_row.html` — conditional `reading_of_id` typeahead block, hidden by default; JS toggles visibility based on `name_type` value.
- Modify: `src/templates/admin/people/partials/_name_row.html` — when `n.reading_of_id` is set, render an indent + "↳ {name_type} of <parent name>" subtitle.
- Create: `src/templates/admin/people/partials/_reading_target_search_results.html` — `<li>` option-row partial.
- Update: delete button on `_name_row.html` to include child-cascade count in `hx-confirm` text.
- Tests: `tests/api/admin/test_people_name_templates.py` — assert typeahead structure + subtitle rendering.

**Done when:**
- Form row renders the typeahead with `role="combobox"` + listbox + hidden field, suffixed with row key (per #125 audit pattern: `reading-target-{{ row_key }}`).
- JS shows/hides the block based on `name_type` value (`reading`, `romanization`, `mrz` → show).
- Read-row subtitle renders parent name + relationship word ("↳ romanization of: van der Meer").
- Delete confirm mentions cascade when child rows exist.

## Task 3: Display order — visual rows first, children nested

**Files:**
- Modify: `src/api/admin/_names_shared.py` (and any other place fetching `person_names` for display) — switch `ORDER BY` from current shape to a "visual rows first, then their children grouped" arrangement. Implementation: window function or two-pass sort; visual row sorted by canonical/type/name, child rows immediately after by name_type/name.
- Test: integration test inserts a person with one visual + two readings; asserts read-page renders them in the expected order.

## Task 4: End-to-end smoke + regression

- Manual flow: create a Hant `legal` row, then a `romanization` row pointing at it; delete the visual; confirm cascade on the readings.
- `uv run pytest --no-cov -q`, `npm run test:js`, `uv run ruff check src/ tests/`.
- Open PR / merge to main.

---

## Out of Scope (Phase 2d)

- `person_name_parts` structured-parts editor.
EOF
