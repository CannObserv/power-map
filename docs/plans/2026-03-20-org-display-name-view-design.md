# Design: Org Display Name View

**Date:** 2026-03-20

## Goal

Show organization names throughout the admin UI as "[Org Name] ([Org Acronym])" (e.g. "National Cannabis Industry Association (NCIA)"). Orgs without an acronym show just the name. Fix the duplicate-row bug caused by multiple `is_canonical = TRUE` rows per org (one per `name_type`).

## Approved Approach

Add a `v_org_display_names` PostgreSQL view that pre-computes the formatted display name per org. All queries join this view instead of joining `organization_names` directly with `is_canonical = TRUE`.

## Key Decisions

- **View, not query-level logic.** Centralizes the display format in one place; changing it later requires only updating the view definition.
- **`CREATE OR REPLACE VIEW`** fits naturally in `apply_schema()`, which is idempotent and runs at startup.
- **`name_type != 'acronym'`** selects the primary name (legal, dba, former). The acronym is fetched in a second join. This avoids coupling the view to a specific primary name type.
- **`COALESCE(nl.name || ' (' || na.name || ')', nl.name)`** — if no acronym, emits just the name. There will never be an org with only an acronym and no primary name (invariant enforced by application logic).
- **Search** filters on `dn.display_name ILIKE $q`, so searching "NCIA" or "National Cannabis" both return the org.
- **Templates unchanged.** Variable names `org_name` and `canonical_name` stay the same.

## View Definition

```sql
CREATE OR REPLACE VIEW v_org_display_names AS
SELECT o.id AS organization_id,
       COALESCE(nl.name || ' (' || na.name || ')', nl.name) AS display_name
FROM organizations o
LEFT JOIN organization_names nl
    ON nl.organization_id = o.id AND nl.is_canonical = TRUE AND nl.name_type != 'acronym'
LEFT JOIN organization_names na
    ON na.organization_id = o.id AND na.is_canonical = TRUE AND na.name_type = 'acronym'
```

## Scope

- Add view to `schema.sql`
- Replace ~14 `LEFT JOIN organization_names n ON ... AND n.is_canonical = TRUE` occurrences in `orgs.py`, `roles.py`, `role_assignments.py`, `people.py` with `LEFT JOIN v_org_display_names dn ON dn.organization_id = o.id`
- Update column references: `n.name AS org_name` / `n.name AS canonical_name` → `dn.display_name`
- Update ORDER BY and WHERE clauses that reference `n.name`

## Out of Scope

- `organization_names` management in the org detail/edit forms (untouched)
- Public API endpoints (admin UI only)
- Changing name type semantics or adding new name types
