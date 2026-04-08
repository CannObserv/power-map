# People Detail Screen Redesign

**Date:** 2026-04-08
**Issue:** #72
**Goal:** Convert the static People detail page to a full inline-editing surface, achieving parity with the Orgs detail screen and all patterns documented in `docs/STYLE.md`.

---

## Approved Approach

Parallel-migrate every section from static read-only to HTMX inline editing, following orgs patterns exactly. Add missing backend routes (unarchive, inline field endpoints). Remove the monolithic `/edit/` form once all fields are reachable inline.

---

## Key Decisions and Rationale

### 1. Page header — remove Edit button, add IDs
Remove the `<a href=".../edit/">` button from the page header. Inline editing replaces it.
Add `id="page-heading"` to `<h1>` and `id="breadcrumb-current"` to the breadcrumb `<span>` for live-update support (§17).
Use `v_person_display_names` for the display value (not `names[0].name`).

### 2. Details card — pronouns, notes, status
The current `<dl class="detail-grid">` becomes an entity card with subsections matching orgs:
- **Notes** — inline edit pattern (§15, Notes subsection): `GET/POST /people/{id}/inline/notes/`, partials `_notes_read.html` / `_notes_form.html`, target `id="notes-field"`.
- **Pronouns** — inline edit pattern: `GET /people/{id}/inline/pronouns/` → read partial; `GET /people/{id}/inline/pronouns/edit/` → form partial; `POST /people/{id}/inline/pronouns/` → read partial. Target `id="pronouns-field"`. Single text input; save empty as NULL.
- **Status badge** — display-only (no active toggle — people have no `active` column). Show Archived/Active badge inline in the card.
- Metadata (ID, created, updated) moves to the §22 metadata footer; removed from the detail-grid.

### 3. Names section — inline CRUD, inside Details card
Matches orgs Names subsection (§15 entity card subsection layout):
- Partials: `_name_row.html`, `_name_form_row.html`, `_name_rows.html`
- Routes on a new `people_names.py` router (mirrors `orgs_names.py`):
  - `GET /{person_id}/names/new-row/` → blank form row
  - `GET /{person_id}/names/{name_id}/read-row/` → read partial
  - `GET /{person_id}/names/{name_id}/edit-row/` → form partial
  - `POST /{person_id}/names/{name_id}/edit-row/` → read partial (re-sort: returns `_name_rows.html`)
  - `DELETE /{person_id}/names/{name_id}/` → 200 empty (JS removes row)
  - `POST /{person_id}/names/{name_id}/set-canonical/` → returns `_name_rows.html` (re-renders full tbody)
- Auto-promote invariant: `_maybe_promote_sole_name(person_id, db)` inside every edit/delete transaction.
- Last-identity guard: block delete when person has exactly one name (HTTP 200 + flash error for HTMX, 409 for non-HTMX).
- Name change updates `id="page-heading"`, `id="breadcrumb-current"`, and `document.title` via `flash_trigger(extra={"updatePersonHeader": {...}})` + a `person-detail.js` listener (mirrors `org-detail.js`).

### 4. Contact Information section — split email/phone, inline rows
Matches orgs Contact Information pattern (two subsections within one entity card):
- Email and phone as separate `<h3 class="field-group-label">` subsections with independent `+ Add` buttons
- Partials: `_contact_row.html`, `_contact_form_row.html` (reuse orgs patterns, adapt for person entity_type)
- Routes on `people_contacts.py`:
  - `GET /{person_id}/contacts/new-row/?contact_type=email|phone`
  - `GET|POST /{person_id}/contacts/{contact_id}/edit-row/`
  - `GET /{person_id}/contacts/{contact_id}/read-row/`
  - `DELETE /{person_id}/contacts/{contact_id}/`
- Validation errors: HTTP 200 re-render with `{% if error %}` alert at top of form (§ contact form inline errors).

### 5. Addresses section — inline rows (section-level §21)
Single table → section-level add button (§21). Partials: `_address_row.html`, `_address_form_row.html`.
Routes on `people_addresses.py` (mirrors `orgs_addresses.py`, entity_type='person').

### 6. Links section — unified (replaces split URLs + Social Links)
Collapse the current "URLs" + "Social Links" sections into one "Links" section matching orgs `links`/`link_types` model. Fix the stale `s.platform_name` / `s.handle` template references.
Section-level add button (§21). Partials: `_link_row.html`, `_link_form_row.html`. Routes on `people_links.py`.

### 7. Identifiers section — inline rows (section-level §21)
Same as orgs. Routes on `people_identifiers.py`.

### 8. Role Assignments section
Keep existing static read-only table. Role assignment management belongs on the Role detail page. No inline editing here — it's a contextual view only.

### 9. Unarchive route
Add `POST /{person_id}/unarchive/` to `people.py` (mirrors `org_unarchive`). Danger Zone shows Archive or Unarchive+Delete buttons depending on `archived_at`.

### 10. Remove monolithic `/edit/` form
After all inline routes exist, remove `GET/POST /{person_id}/edit/` routes and `form.html` template. Redirect any lingering links to detail page.

### 11. `person-detail.js`
New static file `src/static/admin/person-detail.js` — listens for `updatePersonHeader` HTMX event, updates `#page-heading`, `#breadcrumb-current`, `document.title`. Loaded via `{% block extra_head %}` with `defer`.

---

## Out of Scope

- Role assignment create/edit inline on person detail (belongs on role detail screen)
- Social links rendered differently from general links (unified as per orgs model)
- Clipboard copy buttons on links (orgs doesn't have these on person detail either)
- Any new people-specific fields not already in the schema

---

## Implementation Order

1. Page header + breadcrumb IDs (template-only, no backend)
2. Bug fix: social links template (`platform_name` → `url_type_name`, collapse URLs + Social into Links)
3. Unarchive route + Danger Zone button
4. Metadata footer (move ID/timestamps out of detail-grid)
5. Names inline CRUD + `person-detail.js` header sync
6. Notes inline edit
7. Pronouns inline edit
8. Contacts inline CRUD (email + phone)
9. Addresses inline CRUD
10. Links inline CRUD
11. Identifiers inline CRUD
12. Remove `/edit/` form + routes

---

## New Files

| File | Purpose |
|---|---|
| `src/api/admin/people_names.py` | Names inline CRUD router |
| `src/api/admin/people_contacts.py` | Contacts inline CRUD router |
| `src/api/admin/people_addresses.py` | Addresses inline CRUD router |
| `src/api/admin/people_links.py` | Links inline CRUD router |
| `src/api/admin/people_identifiers.py` | Identifiers inline CRUD router |
| `src/static/admin/person-detail.js` | Header sync JS |
| `src/templates/admin/people/partials/` | All row/form partials (mirrors orgs/partials/) |

## Modified Files

| File | Change |
|---|---|
| `src/api/admin/people.py` | Add unarchive route; remove `/edit/` routes |
| `src/api/admin/router.py` | Mount new people sub-routers |
| `src/templates/admin/people/detail.html` | Full rewrite |
| `src/templates/admin/people/list.html` | Already updated (#72) |
| `src/templates/admin/people/_rows.html` | Already updated (#72) |
| `src/templates/admin/people/form.html` | Delete |
