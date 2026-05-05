# Phase 2b — Locale + Script + sort_as Typeahead

**Issue:** #123 (Phase 2b sub-task)
**Worktree:** `.worktrees/feat/123-phase-2b-locale-script-typeahead`
**Design:** `docs/plans/2026-05-04-phase-2-person-name-ui-design.md` § Phase 2b

**Goal:** Surface the Phase-1 `locale`, `script`, and `sort_as` columns in the admin form via DB-backed typeaheads (locale + script) and a plain text input (sort_as). Switch person sort to ICU collation honoring `sort_as`.

**Architecture:**
- Two new admin search endpoints (`_locale_search`, `_script_search`) hitting the seeded `bcp47_locales` / `iso15924_scripts` tables. Trigram-GIN indexed substring search.
- `_names_shared.py` `supports_metadata=True` path gains `locale`, `script`, `sort_as` Form fields; FK violations surface as form errors (not 500).
- Form template: two typeahead combo-boxes (reuse existing `typeahead-combobox.js`) + one plain input.
- Read row: subtitle line when any of the three are set ("Latn · en-US" / "sort_as: …").
- Person list/search/typeahead: `ORDER BY COALESCE(sort_as, name) COLLATE "und-x-icu"`.

**Pre-conditions (all already in place from Phase 2-prep):**
- `bcp47_locales` + `iso15924_scripts` populated (3425 + 226 rows on prod)
- FK constraints active on `person_names.locale` / `.script` and `bcp47_locales.script`
- pg_trgm GIN indexes on both columns of both tables

---

## Task 1: Locale + script search endpoints

**Files:**
- Create: `src/api/admin/people_locale_script_search.py`
- Create: `tests/api/admin/test_people_locale_script_search.py`
- Modify: `src/api/admin/router.py` — mount the new router

**Done when:**
- `GET /admin/people/_locale_search?q=spa&limit=20` returns JSON list of locales whose `code` or `display_name` contains `spa` (ILIKE), capped at `limit`, sorted `code ASC`. Empty `q` returns `[]` (placeholder UX).
- `GET /admin/people/_script_search?q=lat&limit=20` returns matching scripts (`code` / `name` ILIKE).
- Both routes auth-guarded with `get_admin_user`.
- Tests cover: empty q → empty list, substring on code, substring on display_name/name, limit cap, sort order, auth required.

## Task 2: Backend — accept locale / script / sort_as Form fields

**Files:**
- Modify: `src/api/admin/_names_shared.py` — extend Form params + builder helpers (`_insert_name`, `_update_name` already accept dynamic columns; reuse the pattern)
- Create: `tests/api/admin/test_people_names_locale_script.py`

**Done when:**
- Create + edit accept `locale`, `script`, `sort_as` Form fields when `supports_metadata=True`.
- Empty-string `sort_as` is treated as None (don't store empty strings).
- Whitespace stripped from `sort_as`.
- FK violations on locale/script surface as user-friendly form errors (HTMX 200 + flash, non-HTMX 422).
- `org_names` (supports_metadata=False) ignores all three fields, even if posted.
- Tests cover: round-trip valid values, FK violation → form error path, empty sort_as → NULL, whitespace strip, all 12 name_types still work, org-side untouched.

## Task 3: Templates — typeahead inputs + read-row subtitle

**Files:**
- Modify: `src/templates/admin/people/partials/_name_form_row.html` — add typeahead-combobox for locale + script, plain input for sort_as
- Modify: `src/templates/admin/people/partials/_name_row.html` — subtitle line under name when any of the three set

**Done when:**
- Form row exposes three inputs: `locale` (combobox bound to `_locale_search`), `script` (combobox bound to `_script_search`), `sort_as` (plain text).
- Each combobox carries the existing combobox a11y attrs (role="combobox", aria-controls, aria-haspopup, etc.).
- Read row renders subtitle "{script} · {locale}" or "sort_as: {value}" (or both, separated) when set.
- Static template tests verify: combobox attrs present, sort_as input present, subtitle appears for non-NULL fields.

## Task 4: Person list / search / typeahead use sort_as + ICU collation

**Files:**
- Modify: `src/api/admin/people.py` — list query, search endpoint, anywhere person_names is sorted for display
- Modify: `tests/api/admin/test_people.py` (or new `test_people_sort.py`) — diacritic-sort regression

**Done when:**
- All person list/search queries that ORDER BY name now ORDER BY `COALESCE(sort_as, name) COLLATE "und-x-icu"`.
- Test inserts "Åberg" + "Aaron" + "Zebra"; default sort places "Aaron" before "Åberg" before "Zebra" (ICU "und" puts Å near A).
- Test inserts a name whose `sort_as = "van der Meer"` and confirms sort honors that key over the visible name.

## Task 5: End-to-end smoke + final regression sweep

- Manual flow: edit a name; locale typeahead suggests "en-US" on "spa" → no, on "Eng"; script typeahead suggests "Latn" on "lat"; saving with `xx-XX` shows form error; saving valid persists; subtitle appears on read row.
- Run full suite + JS + ruff. Expect green except pre-existing unrelated address-normalizer flake.
- Open PR for issue #123 (Phase 2b → main).

---

## Out of Scope (Phase 2c/2d)

- Linked names via `reading_of_id` (Phase 2c).
- Structured parts editor (Phase 2d).
