# People: Merge Duplicate Pairs — Design

**Issue:** #55  
**Date:** 2026-04-02

## Goal

Add a Merge action to the People Duplicates review screen so operators can consolidate two person records into one, following the same pattern as org merge.

## Approved Approach

Mirror `POST /{winner_id}/merge/{loser_id}/` from `orgs.py` with people-specific adjustments.

### Route

`POST /admin/people/{winner_id}/merge/{loser_id}/`

Winner survives; loser is hard-deleted after all references are reassigned.

### Table reassignment order

1. **`person_names`** — loser's non-canonical names reassigned to winner; loser's canonical name demoted to `is_canonical=FALSE` then reassigned (preserves name as alias on winner).
2. **`role_assignments`** — reassign `person_id = winner_id` where no conflict exists. Conflict = loser has an active assignment with the same `(role_id, start_date)` as winner; delete those duplicate rows from loser (keep winner's).
3. **Polymorphic tables** (bulk `entity_id` reassign where `entity_type='person'`): `contact_methods`, `links`, `entity_addresses`, `import_provenance`, `field_confidence`.
4. **`identifiers`** — reassign `entity_id` (no `entity_type` column).
5. **`duplicate_dismissals`** — reassign any dismissals referencing the loser; delete the specific merged pair's dismissal record.
6. `DELETE FROM people WHERE id = loser_id`.

All steps inside a single transaction with `FOR UPDATE` locks on both rows.

### UI

Add "Keep A" and "Keep B" buttons to `_duplicates_region.html` for people, alongside the existing "Not a duplicate" button. Identical pattern to `admin/orgs/_duplicates_region.html`.

### Post-merge

- Flash: "Merged **{loser}** into **{winner}**. Review role assignments and contact info for duplicates."
- `invalidate_dup_count_cache()` from `people_dups`
- HTMX: re-render `_duplicates_region.html` with updated pairs
- Non-HTMX: redirect to `/admin/people/duplicates/`

## Key Decisions

| Decision | Rationale |
|---|---|
| Loser's canonical name → alias on winner | Preserves name history; consistent with org merge treatment of non-canonical names |
| Conflicting role assignments → delete loser's | Winner's data takes precedence; avoids unique index violation on `uq_role_assignment_person_role_start` |
| No confirmation modal | Org merge has none; pairs are already curated by the review screen |

## Out of Scope

- Cross-entity merges (person ↔ org)
- Undo / merge history log
- Bulk merge
