# power-map — Person-Name Admin Controls

The admin editing surface for person names: the shared names-router factory's person
gates, the locale/script typeahead, linked reading rows, structured name parts, and the
single-form editor. Name *semantics* (canonical pointer, visibility rules, readings) live
in `docs/NAMES.md`; the server-side conventions every admin route follows are in
`docs/ADMIN.md`.

## Person-name editor

### Person-name metadata controls (Phase 2a–2d, #123)

Person-name CRUD shares its router factory with org-name CRUD via `make_names_router` in `src.api.admin._names_shared`. The factory accepts `supports_person_metadata: bool = False`:

- `org_names`: leaves the default (`False`) — `organization_names` has no person metadata columns.
- `people_names`: passes `supports_person_metadata=True` — accepts `visibility`, `locale`, `script`, `sort_as` Form fields on create/edit and persists them.

A second, independent gate `supports_effective_dates: bool = False` (#239) controls the org name-validity timeline:

- `org_names`: passes `supports_effective_dates=True` — the create/edit form accepts `effective_start` / `effective_end` date inputs and writes them to `organization_names` (form-as-source-of-truth: an empty input clears the column to NULL). `_parse_optional_date` converts empty strings to None; a malformed value raises `_DateParseError` → 422/flash. The DB `chk_org_name_effective_date_order` CHECK is caught (`asyncpg.CheckViolationError`, `constraint_name` match) and surfaced as a friendly flash rather than a 500. The org name read row shows the effective range and the names table has an `Effective` column (colspan 5).
- `people_names`: leaves the default (`False`) — `person_names` has no effective-date columns; person forms are unaffected. Kept separate from `supports_person_metadata` so the two entity types stay decoupled.

Validation layering:

- Pydantic / FastAPI validates `visibility` against the `PersonNameVisibility = Literal["public","legal_only","hidden"]` from `src.core.types` at request parse — invalid values return 422.
- `_normalise_optional_str` strips whitespace and converts empty strings to None for `locale`/`script`/`sort_as` so blank inputs become NULL columns rather than ''.
- The org-vs-person divergence is the inline `vis = visibility if supports_person_metadata else None` gate at the top of each handler. The same `... if supports_person_metadata else None` shape is used for `loc`, `scr`, `sa` immediately below. Payloads sent to org_names are silently dropped.
- `_metadata_pairs(...)` returns the canonical (column, value) tuple ordering used by both builder helpers:
  - `_insert_name`: includes a column only when its value is non-None — DB defaults (`visibility='public'`, others NULL) handle the rest.
  - `_update_name(write_metadata=True)`: SETs every metadata column to the supplied value (form is the source of truth) — except visibility, which is skipped when None so the DB default + `trg_deadname_visibility` trigger keep authority.
- FK violations on `locale` / `script` are caught in both create and edit handlers; HTMX → 200 + flash trigger with column-specific message via `_fk_violation_message`; non-HTMX → 422. Never a bare 500.

Locale + script typeahead (Phase 2b):

- HTML option-list endpoints `GET /admin/people/_locale_search` and `/_script_search` (in `src.api.admin.people_locale_script_search`) return `<li role="option" data-id data-label>` partials shaped for the existing `typeahead-combobox.js` factory. Substring filter on code OR human-readable column with `escape_like` + `ESCAPE '\\'`; sorted code ASC; capped at `limit` (default 20, max 100); empty `q` returns no rows.
- The form-row template's display input mirrors its trimmed value to the hidden code field on `blur`, so typed-but-not-selected input still submits — invalid codes then trip the FK-violation flash rather than being silently discarded.

Sort + collation (Phase 2b):

- `v_person_display_names.sort_key = COALESCE(sort_as, name)`. Every person ORDER BY uses `sort_key COLLATE "und-x-icu" NULLS LAST` for diacritic-aware ordering (Å near A) with `sort_as` overrides honored.

Linked names — `reading_of_id` (Phase 2c, #123):

- Name CRUD accepts a `reading_of_id` Form field gated by `supports_person_metadata`. The column is a self-FK on `person_names` (ON DELETE CASCADE) — a `reading` / `romanization` / `mrz` row may point at the visual row it transliterates.
- Typeahead `GET /admin/people/{person_id}/_reading_target_search` returns same-person rows whose `name_type` is OUTSIDE `_READING_TYPES` (`reading`, `romanization`, `mrz`) — only visual rows are valid parents. Filters `visibility = 'public'` to mirror the default detail view; uses `escape_like` + `<> ALL($N::text[])` for the type filter.
- `_validate_reading_of_target` (in `_names_shared.py`) runs before the INSERT/UPDATE and surfaces four bypass attempts as form errors (HTMX flash; non-HTMX 422):
  1. Target row doesn't exist (DB FK catches it too — this gives a friendlier message).
  2. Target is on a *different* person (cross-person link).
  3. Target equals the editing row's own id (self-reference; `name_id` is threaded through on the edit path).
  4. Target's `name_type` is itself in `_READING_TYPES` (chain — A→B→C is rejected even if each link is technically same-person).
- The form template's reading-of block is hidden by default; inline JS shows it when `name_type ∈ _READING_TYPES`.
- Read-row template indents linked rows (`class="name-row--child"`) and renders a "↳ {name_type} of: <em>{parent_name}</em>" subtitle. The handler enriches each row with `reading_of_name` (LEFT JOIN parent) and `reading_child_count` (LATERAL count) — both the detail-page and the post-mutation tbody re-render in `_fetch_names_for_rows` carry the enrichment so cancel-from-edit + post-save look identical.
- Delete confirm text becomes "Delete this name and its N linked reading row(s)? (cascade)" when `reading_child_count > 0`.

Structured parts — `person_name_parts` (Phase 2d, #123 / Issue #127):

- Sidecar table, 1:0..1 with `person_names`, ON DELETE CASCADE.
- Server-side validation (in `upsert_or_delete_parts` helper, `src.api.admin.people_name_parts`):
  - Cap: 5 entries per array (`given_names`, `family_names`, `additional_names`). Cap is checked BEFORE trimming so the message reflects what the user typed.
  - Empty-string trim: blank entries are dropped before INSERT; `_trim_array` preserves user order.
  - `primary_identifier` allowlist matches the DB CHECK (`family` / `given` / `patronymic` / `mononym`); a blank value becomes NULL.
  - All-empty payload semantics (Issue #127): if the row already had a parts row, Save **deletes** it; if it never had one, no-op. There is no separate Remove button — clearing every parts field and clicking Save is the delete path.
- Read-row subtitle: handlers attach a `parts_summary` field via `build_parts_summary(family, given, additional)` from `src.api.admin.deps` — a "<family> · <given> · <additional>" line; None when nothing structural is set so the template's `{% if n.parts_summary %}` guard hides the row.

Person-name editor — single form / single Details disclosure / single Save (Issue #127):

- One `<form>` per name row in `_name_form_row.html` posts to `/edit-row/` (existing rows) or `/` (new rows). The earlier two-form split (outer name form + inner parts form) is gone; one Save commits both halves in one transactional upsert via `name_create` / `name_edit_row_post` calling `upsert_or_delete_parts` inside the existing `async with db.transaction():` block.
- Inline portion of the row (visible without expanding anything): name input, name_type select, is_canonical toggle, Save, Cancel.
- **`name_type` dropdown source of truth**: the `<select name="name_type">` iterates a `name_types` context variable supplied by the names-router factory. People-side routes pass `PERSON_NAME_TYPES` and orgs-side passes `ORG_NAME_TYPES` (both in `src/core/types.py`); the settings page reads from the same constants. To add a new value: update `src/core/types.py` (Literal + tuple) and the schema CHECK in lockstep — the dropdown, settings badges, and parametrized round-trip tests pick it up automatically. `tests/core/test_types.py` enforces tuple ↔ Literal ↔ schema parity and fails on drift. The form handlers also reject unknown `name_type` values with a 422 / flash before they reach the DB CHECK, so a stale cached form surfaces a friendly error instead of a raw `CheckViolationError`.
- A single `<details>` "Details" disclosure (rendered by `_name_parts_editor.html`) holds, in order:
  1. **Metadata**: visibility / sort_as / locale / script / reading_of_id (from `_name_metadata_fields.html` — order paired so flex-wrap puts visibility+sort_as on one row and locale+script on the next at most viewport widths; updated in #131).
  2. `<hr>` separator.
  3. **Name parts**: primary_identifier, the given/family/additional CardStack inputs (`person-name-parts-cardstack.js`), and honorific prefix/suffix.
- Auto-open predicate (in `_name_parts_editor.html`): the disclosure renders with `open` when any non-default metadata or parts value is present on the editing row:
  ```jinja
  {%- set _meta_set = n and (
      (n.visibility and n.visibility != 'public') or
      n.locale or n.script or n.sort_as or n.reading_of_id
  ) -%}
  {%- set _parts_set = parts is not none -%}
  ```
  The `n and (...)` guard is defensive — `_name_form_row.html` only includes the parts editor when `n` is set, but the predicate stays safe if that gate ever changes.
- New-name form (`n is None`) does not render the Details disclosure (no `name_id` to attach parts to). To keep metadata fields reachable when creating a row, `_name_form_row.html` includes `_name_metadata_fields.html` directly inline in that branch — same markup, different host.
- Typeahead init `<script>` (locale / script / reading_of_id) lives in `_name_form_row.html` AFTER the include so it runs once the inputs (which may be inside the disclosure) are in the DOM. Browsers query elements inside `<details>` regardless of open state.
- The standalone `POST /parts/` and `POST /parts/delete/` routes are deleted — `_summary_oob_fragment` and `_ensure_name_belongs_to_person` are gone with them. The unified Save flow re-renders the whole tbody so the OOB-swap pattern is no longer used.

Issue #131 follow-ups (lookup bug fix + redesign II):

- **Typeahead query parameter**: locale / script / reading-of inputs must send `q={value}` to their search endpoints. The display inputs are unnamed (so they don't pollute the parent Save POST) and use `hx-vals='js:{q: (event && event.target ? event.target.value : "")}'` to set the request param. The earlier shape (`name="q_locale"` + `hx-params="q"`) silently sent no `q` because no input was named `q` — the filter dropped everything. Don't reintroduce `hx-params="q"` here.
- **Per-row ID namespacing**: every typeahead element id (`locale-search-display`, `locale-search-results`, `locale-hidden`, the script + reading-of equivalents, and `reading-of-block`) is suffixed with a `_uid` discriminator. `_name_metadata_fields.html` and `_name_parts_editor.html` agree on `{%- set _uid = n.id if n else 'new' -%}`; the form row also exposes `data-name-row-typeahead data-uid="{{ _uid }}"` on its `<tr>` for the per-row JS module to discover. This lets an open Edit drawer and the inline new-name form coexist on the page without `getElementById` collisions.
- **Per-row JS wiring**: typeahead init + reading-of-block toggle live in `src/static/admin/person-name-row-typeahead.js`. The module hooks `DOMContentLoaded` for server-rendered rows and `htmx:afterSwap` for HTMX-injected rows, scans the swap target for `[data-name-row-typeahead]`, and reads `data-uid` to compose the namespaced element ids. No inline `<script>` in `_name_form_row.html`.
- **+ Add duplicate-row guard (`add-row-guard.js`, #238)**: every inline "+ Add" button that prepends an unsaved `<tr id="<entity>-row-new">` is disabled while that row exists, so a double-click can't create two colliding `#<entity>-row-new` rows (the id-collision class #131 / #237 fought on person names and events). A button opts in with **`data-new-row-id="<tr-id>"`** (e.g. `name-row-new`, `acronym-row-new`, `email-row-new`). `src/static/admin/add-row-guard.js` is loaded site-wide from `base.html` (#237), registers document-level listeners once (`htmx:afterSwap`, `htmx:load`, `powerMap:newRowClosed`), and `sync()` scans `button[data-new-row-id]` on every call — disabling each iff its row is present. Document-scoped (not table-scoped) so it survives hx-boost and catches outerHTML row swaps; each `<entity>-row-new` is page-unique, so a global id check is correct. This one guard replaced the per-feature `person-detail-add-name-guard.js` and `event-add-guard.js`, and uniquely handles pages with **multiple** add-buttons (org detail) that a single-button-by-id guard could not. New-row Cancel handlers must dispatch `powerMap:newRowClosed` (they remove the row client-side with no HTMX round-trip); existing-row Cancel uses an `hx-get` read-row swap and needs no dispatch.

  **Two race windows, two owners — don't cross them.** Double-clicking "+ Add" is two separate races: (A) a second request fired *while the first is in flight*, before any row exists; and (B) a deliberate second click *after* the row is rendered. The guard owns **B** — it's a UI invariant over DOM state ("disabled while `#<entity>-row-new` exists"), and it is the **sole writer of `disabled`**. Window **A** is a request-lifecycle concern and belongs to htmx: every guarded button carries **`hx-sync="this:drop"`**, so htmx drops a second concurrent request from the same element. Do **not** use `hx-disabled-elt="this"` here: htmx re-enables a disabled-elt after the swap (`htmx:afterRequest`, after `htmx:afterSwap`), which clobbers the guard's disable and reopens window B (#238 CR). Because `hx-sync` never touches `disabled`, the two mechanisms compose without conflict and without depending on htmx event ordering. In-flight *visual* feedback needs no per-button `hx-indicator`: htmx adds `htmx-request` to the requesting button by default, and the global loading rule (`admin.css` — `.htmx-request { opacity:.6; cursor:wait; pointer-events:none }`) dims it for the request's duration. That rule is CSS-only, so it too leaves `disabled` to the guard.
- **`powerMap:` custom event prefix**: project-wide convention for client-side custom DOM events that don't go through HTMX's `HX-Trigger` header (those follow the `update{Entity}Header` camelCase shape — see `docs/HTMX.md` § JS file (`src/static/admin/{entity}-detail.js`)). Today's only `powerMap:` event is `powerMap:newRowClosed` (dispatched by every new-row inline Cancel; #238); future custom events use the same prefix to avoid colliding with browser/library events. Page-wide `powerMap:*` events are dispatched on `document` and listened on `document` (matches the page-wide `htmx:afterSwap` listener convention used by `person-name-deadname-confirm.js` and `person-name-parts-cardstack.js`); element-scoped events should target the relevant element directly.
- **Hint-as-placeholder convention**: locale/script/sort_as/honorific-prefix/honorific-suffix carry concrete examples in their `placeholder` attributes (e.g. `Locale` → `BCP 47 — e.g. en, en-US, ja-JP`). The previous below-control `<small>` helpers under honorific prefix/suffix are removed; the placeholder is the single source of truth for one-line guidance. Primary Identifier is the exception: its multi-line cultural-context help (`<small>` with `family in Japan; patronymic in Iceland; mononym ...`) sits between the label and the `<select>` — placeholders can't hold that much text.
- **Cardstack inputs full-size**: each card in the given/family/additional CardStacks wraps its `<input>` in `<div class="form-group" style="margin-bottom:0;flex:1">` so the input inherits the baseline `.form-group input` rule (font-size, padding, `min-height: 44px`). A bare `<input style="flex:1">` falls back to browser-default text input styling and renders visibly smaller than the rest of the form.
- **Reorder focus-follows-value (#145)**: after a ↑/↓ click on a cardstack arrow, `person-name-parts-reorder.js` moves focus to the neighbor card's same-direction button so repeated keypresses walk the value through the stack. At the boundary (neighbor's same-direction button is disabled), focus falls back to the neighbor's input — the cell that just received the value. Lookups are scoped to the neighbor element (form-scoped via `cardsIn(stack)`), so concurrent reorder in one form never moves focus out of that form.
