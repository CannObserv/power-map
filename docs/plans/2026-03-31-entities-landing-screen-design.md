# Entities Landing Screen Design

**Date:** 2026-03-31
**Status:** Approved

## Goal

Add an `/admin/entities/` landing screen modeled on the Settings screen. Converts the sidebar "Entities" group label into a navigable section-link and provides a card-grid overview of all four entity types with record counts.

## Approved Approach

Option A — new `entities.py` module, single landing page, `admin-sidebar__section-link` navigation.

## Navigation

- Sidebar: convert `span.admin-sidebar__group-label` "Entities" → `a.admin-sidebar__section-link` linking to `/admin/entities/`
- `active_section = 'entities'` on the landing route only; existing sub-pages keep their existing `active_section` values
- Same pattern as Settings section-link — sub-links stay un-highlighted when on a sub-page

## URL

`GET /admin/entities/` — new route in `src/api/admin/entities.py`, mounted in `router.py`.

## Data

Single `fetchrow` query:

```sql
SELECT
    (SELECT COUNT(*) FROM people           WHERE archived_at IS NULL) AS people,
    (SELECT COUNT(*) FROM organizations    WHERE archived_at IS NULL) AS orgs,
    (SELECT COUNT(*) FROM roles            WHERE archived_at IS NULL) AS roles,
    (SELECT COUNT(*) FROM role_assignments WHERE archived_at IS NULL) AS assignments
```

`org_dup_count` via the existing `get_org_dup_count` dep (TTL-cached, consistent with all other non-dashboard routes).

## Landing Page Cards

CSS grid: `grid-template-columns: repeat(auto-fill, minmax(260px, 1fr))` — same as `settings/index.html`.

| Card | Count badge | Primary action | Secondary action |
|---|---|---|---|
| People | N records | Manage → `/admin/people/` | — |
| Organizations | N records | Manage → `/admin/orgs/` | "N duplicates →" `/admin/orgs/duplicates/` (hidden when 0) |
| Roles | N records | Manage → `/admin/roles/` | — |
| Assignments | N records | Manage → `/admin/role-assignments/` | — |

## Files Changed

- `src/api/admin/entities.py` — new, single `GET /entities/` route
- `src/templates/admin/entities/index.html` — new landing template
- `src/templates/admin/base.html` — sidebar: `span.admin-sidebar__group-label` → `a.admin-sidebar__section-link`
- `src/api/admin/router.py` — import and mount `entities_module`
- `tests/api/admin/test_entities.py` — new; covers: page renders, counts correct, dup link shown/hidden conditionally

## Out of Scope

- Section-link staying highlighted when on a sub-page
- Archived entity counts
- Imports card (dashboard concern)
