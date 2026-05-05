# Phase 2d — Structured Parts Editor (`person_name_parts`)

**Issue:** #123 (Phase 2d sub-task — final phase of #123)
**Branch:** `feat/123-phase-2d-structured-parts` (re-using the Phase 2b worktree)
**Design:** `docs/plans/2026-05-04-phase-2-person-name-ui-design.md` § Phase 2d

**Goal:** Surface the `person_name_parts` sidecar table (1:0..1 with `person_names`, ON DELETE CASCADE) in the admin UI. Each name row gains a "Structured parts" sub-form within the existing inline edit drawer. Save upserts the parts row; "Remove structured parts" deletes it. Reading-row admins can decompose a name into `given_names[]` / `family_names[]` / `additional_names[]` (≤5 each), `honorific_prefix`, `honorific_suffix`, and a `primary_identifier` discriminator (`family` / `given` / `patronymic` / `mononym`).

**Architecture:**
- Backend: a small CRUD router on `person_name_parts` keyed by `person_name_id`. Upsert on POST (INSERT … ON CONFLICT DO UPDATE), DELETE for the "Remove" affordance. No standalone "create" — the row exists iff at least one field is set on save.
- Form encoding: arrays as `given_names=María&given_names=José&...` (FastAPI `list[str]` works natively for repeated form fields). Empty strings dropped. Cap of 5 per array enforced at the handler.
- Validation: `primary_identifier` constrained by DB CHECK to {`family`,`given`,`patronymic`,`mononym`} or NULL. UI offers a `<select>` covering exactly those values plus a blank "—". Length cap enforced server-side; client-side affordance limits the visible inputs.
- Form template: nested editor block embedded in the existing `_name_form_row.html` (inside the same `<form>`), hidden by default behind a toggle. Pre-populates from a `parts` dict the handler passes alongside `n`.
- Read row: light indicator that structured parts exist (e.g., a subtitle line "parts: family · given · …" when populated). Drawer details only revealed during edit.

**Pre-conditions (already in place — Phase 1):**
- `person_name_parts` table with PK `person_name_id REFERENCES person_names(id) ON DELETE CASCADE`.
- Columns: `given_names TEXT[]`, `family_names TEXT[]`, `additional_names TEXT[]`, `honorific_prefix TEXT`, `honorific_suffix TEXT`, `primary_identifier TEXT CHECK (… IN (…))`, `created_at` / `updated_at`.
- `trg_updated_at_person_name_parts` BEFORE UPDATE trigger.

---

## Task 1: Backend — CRUD endpoints + handler form fields

**Files:**
- Create: `src/api/admin/people_name_parts.py` — new router with two routes:
  - `POST /admin/people/{person_id}/names/{name_id}/parts/` — upsert.
  - `POST /admin/people/{person_id}/names/{name_id}/parts/delete/` — delete (POST not DELETE because forms can't natively issue DELETE).
- Modify: `src/api/admin/router.py` — mount the new router.
- Modify: `src/api/admin/_names_shared.py` — augment the `read-row` and `edit-row` handlers to fetch the parts row (LEFT JOIN or a separate fetch) and pass it to the templates as `parts`.
- Modify: `src/api/admin/people.py` — augment the person-detail handler so each name row includes its parts (so the rows partial can show the indicator).
- Create: `tests/api/admin/test_people_name_parts.py` (~12 tests).

**Done when:**
- POST upsert with `given_names=['María','José']`, `family_names=['García']`, `primary_identifier='family'` → row exists in `person_name_parts` with the given arrays.
- Second POST with different arrays overwrites (UPDATE path of upsert) — no UniqueViolation.
- POST with empty strings interspersed → empty strings filtered out before INSERT.
- POST with 6+ elements in any of the array fields → 422 (or HTMX flash + 200) with form re-render; no row mutated.
- POST with `primary_identifier='nonsense'` → form error (server-side allowlist match before SQL; the DB CHECK is the fallback).
- POST delete → `person_name_parts` row gone; subsequent fetch returns NULL parts.
- Org-side endpoints unaffected (organization_names has no parts sidecar).
- Auth: missing `X-ExeDev-UserID` → 307 redirect (existing `get_admin_user` dep).
- Cross-person guard: `name_id` must belong to `person_id` → 404 otherwise.

## Task 2: Templates — drawer editor + read-row indicator

**Files:**
- Create: `src/templates/admin/people/partials/_name_parts_editor.html` — the editor block (renders inside the form `<td colspan="4">`).
- Modify: `src/templates/admin/people/partials/_name_form_row.html` — include the editor block, behind a toggle.
- Modify: `src/templates/admin/people/partials/_name_row.html` — small "parts: …" subtitle when populated; "Remove structured parts" handled via the editor's own POST-to-delete button.
- Tests: extend `tests/api/admin/test_people_name_templates.py` with parts-editor structural assertions (~6 tests).

**Done when:**
- Editor block contains: 5 inputs each for given/family/additional (rendered as repeating `<input name="given_names">` etc), honorific prefix/suffix free-text, `<select name="primary_identifier">` with the 4 allowed values + blank, "Remove structured parts" submit button (separate inline form posting to `…/parts/delete/`), and a "Save parts" submit button.
- When `parts` dict is None → all inputs render empty.
- When `parts` dict is populated → arrays pre-fill into the first N inputs (e.g., given_names=['María'] → first input value="María", remaining empty).
- When `parts.primary_identifier` is set → that option `selected`.
- Read-row subtitle shows truncated parts summary only when parts exist.

## Task 3: Wiring — handler enrichment + cap enforcement

**Files:**
- Modify: `src/api/admin/_names_shared.py` — `_fetch_name_for_row` (or equivalent) joins to `person_name_parts` so edit-row + read-row receive `parts`.
- Modify: `src/api/admin/people.py` — person-detail name fetch joins parts so the read-row partial can show the subtitle.
- Modify: `src/api/admin/people_name_parts.py` — implement cap (`if len(...) > 5`), empty-string trim, primary_identifier allowlist.
- Tests: extend the parts test file with a single round-trip integration test (create name → POST parts → reload detail → verify subtitle).

**Done when:**
- Detail page shows the parts subtitle on linked rows.
- Edit form pre-populates parts when the row has them.
- 5-cap and empty-trim verified end-to-end.

## Task 4: End-to-end verification

- Run the full default Python suite + integration tests.
- Run JS suite (no JS changes expected for 2d but confirm no regressions).
- Smoke test in dev server with a Hispanic-style two-family-name decomposition:
  - Create person; add `legal` name "María José García López".
  - Open editor → expand parts → enter `given_names=[María, José]`, `family_names=[García, López]`, `primary_identifier=family` → Save.
  - Reload detail → subtitle shows "parts: García López · María José" (or similar compact form).
  - Edit again → values pre-fill.
  - Click "Remove structured parts" → row gone; subtitle disappears.
  - Delete the parent name row → cascade verified (no orphan row in `person_name_parts`).

---

## Out of scope (deferred / explicit non-goals)

- **Drag-and-drop reordering.** Up-down buttons not in scope either; the order users type is the order saved. YAGNI for max-5 lists.
- **Auto-population from name strings.** Parts are user-entered only.
- **Per-script alternate parts.** A given `person_names` row has at most one parts row; a Han `legal` row and a Latn `romanization` row each get their own row, but cross-script linking is not modeled.
- **Public API exposure.** Phase 3 territory.
- **vCard 4.0 export.** Future.
