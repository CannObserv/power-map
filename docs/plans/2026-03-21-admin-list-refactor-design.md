# Admin List View Refactor — Design Doc

**Date:** 2026-03-21
**Scope:** Organizations list (all three features); pagination + per-page changes apply to all four list views (orgs, people, roles, role_assignments). Deduplication is orgs-only for now.

---

## Goal

Three improvements to admin dashboard list screens:

1. Move pagination controls to the top; add a sticky footer copy.
2. Add a per-page size control to the filter bar.
3. Add org deduplication: discovery banner on list + dedicated merge screen.

---

## 1. Pagination — Top Bar + Sticky Footer

### Approach

Render the `pagination` macro twice in each `_region.html`:

- Once **above** `<div class="table-wrapper">` (primary).
- Once **below**, wrapped in `<div class="pagination--sticky">` (sticky footer).

The sticky div uses CSS `position: sticky; bottom: 0` so it follows the viewport on long lists. On short lists (single page), both instances are hidden by the existing `{% if total_pages > 1 %}` guard inside the macro.

### CSS (shared stylesheet)

```css
.pagination--sticky {
  position: sticky;
  bottom: 0;
  background: var(--color-surface);
  border-top: 1px solid var(--color-border);
  padding: 0.5rem 1rem;
  z-index: 10;
}
```

### Key decisions

- No JS required; HTMX on both instances targets the same region ID.
- Both instances are identical markup — no divergence risk.
- Applies to: `orgs/_region.html`, `people/_region.html`, `roles/_region.html`, `role_assignments/_region.html`.

---

## 2. Per-Page Size Control

### Approach

Add `page_size` as a validated query param on each list route:

```python
page_size: int = Query(50, ge=10, le=500)
```

`PAGE_SIZE` module-level constant becomes the default only. `pagination_context()` signature is unchanged (already takes `page_size`).

Add a `<select name="page_size">` with options `[25, 50, 100, 250]` to the filter bar in each `list.html`. On `change`, HTMX re-fetches with `page=1` reset (explicit `hx-vals='{"page": 1}'`) and the new `page_size`.

`page_size` is appended to `extra_qs` in each `_region.html` so pagination links preserve the chosen size:

```
"q=" ~ q|urlencode ~ "&status=" ~ status|urlencode ~ "&page_size=" ~ page_size
```

### Key decisions

- Value round-trips through the URL — survives navigation and back-button.
- Changing page size resets to page 1 to avoid out-of-range pages.
- Validated server-side (`ge=10, le=500`) — ignores invalid URL params.

---

## 3. Org Deduplication — Hybrid

### Discovery (list screen)

A dismissible banner above the table on `/admin/orgs/`:

> "N possible duplicate organizations — Review →"

Computed once per page load via a count query against candidate pairs (see below). Hidden when count = 0. Links to `/admin/orgs/duplicates/`.

### Candidate detection

Use PostgreSQL `pg_trgm` extension (`similarity()` function) on normalized canonical names (lowercased, punctuation stripped). Threshold: `similarity > 0.85`. Pairs are surfaced as `(org_a_id, org_b_id)` where `org_a_id < org_b_id` to avoid duplication.

Fallback if `pg_trgm` is unavailable: exact match on `lower(regexp_replace(name, '[^a-z0-9 ]', '', 'g'))`.

### Dedicated screen (`/admin/orgs/duplicates/`)

Lists candidate pairs ranked by similarity score (descending). Each pair is displayed side-by-side:

| Field | Record A | Record B |
|---|---|---|
| Name | … | … |
| Acronym | … | … |
| Created | … | … |
| Roles | N | N |

**Actions per pair:**

- **Merge (keep A)** / **Merge (keep B)** — user chooses the survivor.
- **Not a duplicate** — dismisses the pair; stored in `duplicate_dismissals` so it doesn't resurface.

### Merge endpoint

`POST /admin/orgs/{winner_id}/merge/{loser_id}/`

Runs in a single transaction:

1. Reassign all FK references from `loser_id` to `winner_id`:
   - `organization_names` (non-canonical names become alt names on winner)
   - `organization_acronyms` (non-canonical)
   - `roles` (`organization_id`)
   - Any other tables with `organization_id` FK
2. Hard-delete the loser (`DELETE FROM organizations WHERE id = loser_id`).
3. Redirect to `/admin/orgs/duplicates/`.

### Dismissals table

```sql
CREATE TABLE IF NOT EXISTS duplicate_dismissals (
    id TEXT PRIMARY KEY,
    entity_type TEXT NOT NULL,          -- 'organization', 'person', etc.
    entity_a_id TEXT NOT NULL,
    entity_b_id TEXT NOT NULL,
    dismissed_by TEXT NOT NULL,         -- AdminUser email
    dismissed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (entity_type, entity_a_id, entity_b_id)
);
```

Pairs are stored with `entity_a_id < entity_b_id` (consistent ordering).

### Key decisions

- Dedup is orgs-only for this iteration. Table is generic (`entity_type`) to support people later.
- Hard-delete after merge (not archive) — loser has no independent identity after reassignment.
- Similarity threshold (0.85) is a starting point; can be tuned once we see false positive rates.
- `pg_trgm` must be enabled: `CREATE EXTENSION IF NOT EXISTS pg_trgm;` added to `apply_schema`.

---

## Out of Scope

- People/roles deduplication.
- Bulk merge (select multiple pairs at once).
- Automated merge without human review.
- API endpoints for deduplication (admin UI only).
