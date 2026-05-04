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

### Phase 2-prep — `bcp47_locales` + `iso15924_scripts` lookup tables

Prerequisite for Phase 2b. Adds two reference tables seeded from canonical sources, plus FK constraints from `person_names.locale` and `person_names.script`. Land before 2b begins; independently shippable.

Schema additions:

```sql
CREATE TABLE bcp47_locales (
    code         TEXT PRIMARY KEY,        -- e.g. 'en-US', 'zh-Hant-TW', 'is-IS'
    language     TEXT NOT NULL,           -- ISO 639-1/-3 primary subtag, e.g. 'en'
    script       TEXT,                    -- ISO 15924 subtag (nullable; not all locales pin script)
    region       TEXT,                    -- ISO 3166-1 region (nullable)
    display_name TEXT NOT NULL            -- 'English (United States)'
);

-- pg_trgm GIN indexes power the typeahead's `code ILIKE '%q%' OR
-- display_name ILIKE '%q%'` queries. The PK already covers exact-code
-- lookups; the trigram indexes make substring/prefix search sub-millisecond
-- on the full ~7000-row table.
CREATE INDEX idx_bcp47_locales_code_trgm
    ON bcp47_locales USING GIN (code gin_trgm_ops);
CREATE INDEX idx_bcp47_locales_display_name_trgm
    ON bcp47_locales USING GIN (display_name gin_trgm_ops);

CREATE TABLE iso15924_scripts (
    code         TEXT PRIMARY KEY,        -- 4-letter code, e.g. 'Latn', 'Hant', 'Kana'
    numeric_code SMALLINT UNIQUE NOT NULL, -- e.g. 215 for Latin
    name         TEXT NOT NULL            -- 'Latin', 'Han (Traditional variant)'
);

CREATE INDEX idx_iso15924_scripts_code_trgm
    ON iso15924_scripts USING GIN (code gin_trgm_ops);
CREATE INDEX idx_iso15924_scripts_name_trgm
    ON iso15924_scripts USING GIN (name gin_trgm_ops);

ALTER TABLE person_names
  ADD CONSTRAINT person_names_locale_fkey
    FOREIGN KEY (locale) REFERENCES bcp47_locales(code) ON UPDATE CASCADE,
  ADD CONSTRAINT person_names_script_fkey
    FOREIGN KEY (script) REFERENCES iso15924_scripts(code) ON UPDATE CASCADE;
```

Both FKs preserve existing rows (all `locale` and `script` are NULL today after the Phase 1 migration). `ON UPDATE CASCADE` lets us rename codes if a registry change ever requires it. No `ON DELETE` clause — default `NO ACTION` blocks deletion of a referenced lookup row, which is correct (the registry doesn't shrink).

Seeding strategy:

- One-shot Python script: `scripts/seed_locales_scripts.py`. Idempotent (`INSERT … ON CONFLICT DO UPDATE SET …`). Runs once at deploy time per environment; can be re-run when registries refresh.
- `langcodes` library iterates every CLDR locale. For each, populate `code`, `language`, `script`, `region`, and the human-readable `display_name`. Extracts language and region subtags via `langcodes.Language.get(...)`.
- `pycountry.scripts` enumerates all ISO 15924 entries. For each: `code` (alpha_4), `numeric_code` (numeric), `name`.
- No curated default-set. The typeahead narrows the full table by user keystrokes (`code` and `display_name` / `name` substring match). With pg_trgm GIN indexes the full-table search is fast enough that pre-computed defaults add no value.

**Library dependencies live in a `seed` dep group, NOT runtime deps.** Configured in `pyproject.toml`:

```toml
[dependency-groups]
seed = ["langcodes>=3.5", "pycountry>=24.0"]
```

The seed script is invoked via `uv run --group seed scripts/seed_locales_scripts.py` and is the only place in the codebase that imports `langcodes` or `pycountry`. Request-path code never validates these strings via the libraries — DB FK is the authoritative check.

Validation layering:

| Layer | What it does | Source of truth |
|---|---|---|
| Admin form Pydantic | Strips whitespace; rejects empty strings | UI ergonomics |
| FK to lookup table | Rejects unregistered codes | Authoritative |
| `langcodes` / `pycountry` | (Seed script only) populates the lookup tables | Registry mirror |

A POST with `locale='xx-XX'` (well-formed but unregistered) raises `asyncpg.exceptions.ForeignKeyViolationError` at the DB; admin handler maps to a friendly form error.

Files touched:

- Modify: `src/core/schema.sql` — add the two tables + FK migrations (idempotent `DO $$ ... IF NOT EXISTS ... END $$` blocks).
- Modify: `pyproject.toml` — add `[dependency-groups.seed]`.
- Create: `scripts/seed_locales_scripts.py` — generates and upserts rows.
- Create: `tests/scripts/test_seed_locales_scripts.py` — unit tests against `langcodes` / `pycountry` (no DB).
- Create: `tests/core/test_schema_locale_script_lookups.py` — integration tests for FK enforcement, idempotent re-seed, trigram-search behaviour.

Done criteria:

- [ ] Seed script populates ~7000 locales and ~200 scripts on a fresh DB.
- [ ] FK constraints reject unregistered codes; well-formed-but-unknown codes return `ForeignKeyViolationError`.
- [ ] `bcp47_locales` and `iso15924_scripts` each carry pg_trgm GIN indexes on `code` and on the human-readable column (`display_name` / `name`).
- [ ] Typeahead `ILIKE '%foo%'` queries on either table return in <5 ms (sub-millisecond expected on this size).
- [ ] Re-running the seed script is a no-op for unchanged rows (`ON CONFLICT DO UPDATE` only fires when registry data changes).
- [ ] `langcodes` and `pycountry` are not in the runtime dependency list (verified by `uv run python -c 'import langcodes'` failing in the default env).

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

**Depends on Phase 2-prep** (lookup tables + FKs must exist).

Schema fields exposed: `locale`, `script`, `sort_as` (FKs from 2-prep are now in place).

UI changes:

- Edit drawer adds three inputs:
  - `locale` — typeahead backed by `bcp47_locales`. Empty input shows a placeholder ("Type to search locales…"); each keystroke narrows via `code ILIKE '%q%' OR display_name ILIKE '%q%'`. Submit-time FK enforcement rejects free input that doesn't resolve to a registered code; UI surfaces the resulting `ForeignKeyViolationError` as a form error ("Locale 'xx-XX' is not a registered BCP 47 code").
  - `script` — typeahead backed by `iso15924_scripts`. Same shape: empty placeholder, narrows by `code ILIKE '%q%' OR name ILIKE '%q%'`. Same FK rejection behaviour.
  - `sort_as` — plain text input. No DB validation (free string).
- Read row gains a small subtitle under the name when any are set: e.g. "Latn · en-US" or "sort_as: van der Meer".
- Person list, search, typeahead all change `ORDER BY name` → `ORDER BY COALESCE(sort_as, name) COLLATE "und-x-icu"`.

New endpoints (server-side typeahead candidates):

- `GET /admin/people/_locale_search?q=<term>&limit=20` — returns up to 20 matches whose `code` or `display_name` contains `q` (ILIKE). Empty `q` returns no rows (placeholder shown by UI). Sort: `code ASC` after the substring filter. JSON shape: `[{"code": "en-US", "display_name": "English (United States)"}, …]`.
- `GET /admin/people/_script_search?q=<term>&limit=20` — same shape for scripts (matches `code` or `name`).

Both endpoints lean on the pg_trgm GIN indexes added in Phase 2-prep; query plans should show `Bitmap Index Scan` on the trigram index, not seq scan.

Both endpoints reuse the existing `typeahead-combobox` JS component documented at [tests/js/typeahead-combobox.test.js](tests/js/typeahead-combobox.test.js); no new client-side library.

Backend tests:

- POST a name with valid `locale='en-US'`, `script='Latn'`, `sort_as='Foo Bar'` → DB row reflects all three.
- POST a name with `locale='xx-XX'` (well-formed but unregistered) → form re-renders with error; no row created.
- POST a name with `script='Xxxx'` (not in registry) → same.
- Locale search endpoint: empty query returns no rows (UI shows placeholder); query='Spanish' returns es-ES, es-MX, etc.; query returns at most `limit` rows.
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

### Phase 2-prep

- Modify: `src/core/schema.sql` — add `bcp47_locales`, `iso15924_scripts` tables; FK migrations on `person_names.locale` and `person_names.script` (idempotent `DO $$ ... END $$` blocks).
- Modify: `pyproject.toml` — `[dependency-groups.seed]` with `langcodes>=3.5`, `pycountry>=24.0`.
- Create: `scripts/seed_locales_scripts.py` — populates both lookup tables from `langcodes` + `pycountry`. Idempotent (`ON CONFLICT DO UPDATE`). No curated constants — full registry only.
- Create: `tests/scripts/test_seed_locales_scripts.py` — unit tests over `langcodes` / `pycountry` enumeration (no DB; mocks the connection).
- Create: `tests/core/test_schema_locale_script_lookups.py` — integration tests for FK enforcement, idempotent re-seed, pg_trgm GIN index presence, well-formed-but-unregistered rejection.
- Modify: `docs/CONVENTIONS.md` — append a "BCP 47 / ISO 15924 lookup tables" subsection under "Person names — i18n & cultural awareness" describing the validation layering (UI → FK → seed-script-only registry libs).

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

### Phase 2b (depends on 2-prep)

- Modify: `_names_shared.py` — `locale` / `script` / `sort_as` form fields gated by `supports_metadata`.
- Modify: name form template — typeahead inputs wired to the new search endpoints.
- Modify: `src/api/admin/people.py` — switch list sort to `ORDER BY COALESCE(sort_as, name) COLLATE "und-x-icu"`.
- Create: `src/api/admin/people_locale_script_search.py` — `GET /admin/people/_locale_search`, `GET /admin/people/_script_search`. Both query the lookup tables, filter by `q` ILIKE on `code` and the human-readable column (using the trigram GIN indexes), sort `code ASC`, cap at `limit`. Empty `q` returns no rows.
- Modify: `src/api/admin/router.py` — mount the new search router.
- Tests: typeahead endpoint contract, FK-rejection-as-form-error path, sort-order integration test on names containing diacritics.

No data files in `src/api/admin/static/` — the lookup is DB-backed; seeding handles registry source.

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
