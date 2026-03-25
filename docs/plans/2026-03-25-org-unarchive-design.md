# Org Unarchive — Design

**Date:** 2026-03-25

## Goal

Allow admins to restore an archived organization to its prior active/inactive state through the UI.

## Approved approach

Option B: add a "Restore from archive" button adjacent to the "Archived" status badge in the active toggle area. The Danger Zone is unchanged.

## Key decisions

- **Preserve prior `active` state** — unarchive sets `archived_at = NULL` only; the existing `active` column value is left untouched. An org that was inactive before archiving returns as inactive.
- **Placement** — `_active_toggle.html` (status badge area), not the Danger Zone. Unarchiving is reversible; the Danger Zone is reserved for irreversible actions.
- **Plain form POST** — `<form method="POST" action="/{org_id}/unarchive/">`, consistent with the existing archive button. No HTMX partial swap needed since unarchiving affects both the toggle and the Danger Zone (full page redirect is simpler).
- **No flash** — the archive route has no flash; keep consistent.
- **Guard:** 409 if org is not currently archived.

## Route

```
POST /admin/orgs/{org_id}/unarchive/
```

- 404 if org not found
- 409 if `archived_at IS NULL` (already active)
- Sets `archived_at = NULL`
- Returns `RedirectResponse` to `/admin/orgs/{org_id}/`, status 303

## Template change

`src/templates/admin/orgs/partials/_active_toggle.html` — when `org.archived_at` is set, render a "Restore from archive" button (`btn--secondary btn--sm`) inline after the badge.

## Out of scope

- Flash notification on unarchive
- List view changes
- Danger Zone changes
- Unarchive for people/roles (separate tickets if needed)

## Testing

Integration test mirroring the archive test:
- POST to `/unarchive/` → assert `archived_at IS NULL`, assert 303 redirect
- POST to `/unarchive/` on a non-archived org → assert 409
