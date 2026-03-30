# Settings Screen Design

**Date:** 2026-03-30
**Status:** Approved

## Goal

Replace the "Reference / Lookups" section in the admin sidebar with a "Settings" landing screen. The landing page provides a card-grid overview of all configuration tables; editable tables link to dedicated sub-pages; schema-constrained (read-only) types are displayed inline on the landing page as chips.

## Approved Approach

Option 3 — new `settings.py` module, landing page + sub-pages for editable tables, read-only types on the landing page.

## Navigation

- Sidebar: remove "Reference" group + "Lookups" link; add "Settings" group with single "Settings" link → `/admin/settings/`
- `active_section = 'settings'` on all settings routes

## URL Map

| Old | New |
|---|---|
| `/admin/lookups/link-types-social/` | `/admin/settings/link-types/` (merged) |
| `/admin/lookups/link-types-general/` | `/admin/settings/link-types/` (merged) |
| `/admin/lookups/identifier-types/` | `/admin/settings/identifier-types/` |
| *(none)* | `/admin/settings/` — new landing page |

No redirect shims — internal admin tool, clean URL cut.

## Landing Page (`/admin/settings/`)

Card grid (CSS Grid, 2-col wide / 1-col mobile). Two card types:

**Editable table cards** — title, record count badge, "Manage →" link:
- Link Types → `/admin/settings/link-types/`
- Identifier Types → `/admin/settings/identifier-types/`

**Read-only cards** — title, inline chips, developer note:
- Organization Name Types — `legal`, `dba`, `former`
- Person Name Types — `legal`, `former`, `preferred`, `alias`, `initials`
- Address Types — `mailing`, `physical`, `other`

Read-only values are hardcoded in the template (CHECK constraints; no DB query). Editable table counts fetched in a single multi-statement query on the landing handler.

## Sub-pages

### Link Types (`/admin/settings/link-types/`)

Single page, two tables — General first, Social second. Each table has its own "+ Add" inline row. `is_social` is implicit (not user-editable).

**Listing query (per table):**
```sql
SELECT lt.*, COUNT(l.id) AS usage_count
FROM link_types lt
LEFT JOIN links l ON l.link_type_id = lt.id
WHERE lt.is_social = <bool>
GROUP BY lt.id
ORDER BY lt.display_name
```

**Columns:** Display Name | Slug | In Use | Actions

**CRUD routes (inline row-editing pattern):**
- `GET /settings/link-types/` — page
- `GET /settings/link-types/{scope}/new-row/` — blank form row (`scope` = `general` | `social`)
- `GET /settings/link-types/{scope}/{id}/edit-row/` — edit form partial
- `POST /settings/link-types/{scope}/{id}/edit-row/` — save, return read partial
- `GET /settings/link-types/{scope}/{id}/read-row/` — cancel, return read partial
- `DELETE /settings/link-types/{scope}/{id}/` — delete; FK violation → 200 + flash error

### Identifier Types (`/admin/settings/identifier-types/`)

Single table.

**Listing query:**
```sql
SELECT eit.*, COUNT(i.id) AS usage_count
FROM entity_identifier_types eit
LEFT JOIN identifiers i ON i.entity_identifier_type_id = eit.id
GROUP BY eit.id
ORDER BY eit.display_name
```

**Columns:** Display Name | Slug | Full Name | Entity Type | In Use | Actions

Entity Type edit field: `<select>` with options `organization`, `person`, `role_assignment`.

**CRUD routes:** same inline row-editing pattern as link types.

## "In Use" Display

- Count > 0: show as plain number (e.g. `3`)
- Count = 0: show `—`
- Delete button always active; FK violation on attempt → `flash_trigger("error", ...)` with 200 response (HTMX) or 409 (non-HTMX)
- Count provides user context before attempting a delete

## Implementation Notes

- New file: `src/api/admin/settings.py` — replaces `lookups.py`
- `src/api/admin/lookups.py` — deleted
- `src/api/admin/router.py` — swap `lookups` import for `settings`
- Templates: `src/templates/admin/settings/` replaces `src/templates/admin/lookups/`
  - `index.html` — landing page
  - `link_types.html` — combined general + social tables
  - `identifier_types.html` — identifier types table
  - Inline partials: `_link_type_row.html`, `_link_type_edit_row.html`, `_identifier_type_row.html`, `_identifier_type_edit_row.html`
- `src/templates/admin/base.html` — sidebar update
- `tests/api/admin/test_lookups.py` → `tests/api/admin/test_settings.py` — URLs updated to new routes; new tests for landing page and usage counts

## Out of Scope

- Schema migrations to make address/name types DB-managed (deferred; read-only for now)
- Pagination on sub-pages (reference tables are small)
- Archiving for lookup rows (hard delete with FK guard is sufficient)
