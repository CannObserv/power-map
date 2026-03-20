---
title: People List View Refactor — Filter Card, Status Label, Pagination
date: 2026-03-20
status: approved
---

## Goal

Improve usability of admin list views (People, Orgs, Roles, Assignments) with three changes:
1. Wrap search/filter controls in a bounding card with labeled inputs
2. Fix Status filter — add visible label, resolve cramped spacing
3. Add paginator (separate issue) — numbered pages + prev/next + position info, HTMX partial swap

## Approved approaches

### 1. Filter card — new `.filter-card` CSS class

A dedicated class rather than reusing `.entity-card`, so filter-specific layout can be tuned independently.

**Layout:**
- Full-width search input at top of card
- Flex row of labeled filter controls below, each in a `.filter-card__field` wrapper with a stacked `<label>` and control
- Applied uniformly to all four list views: People, Orgs, Roles, Assignments

**CSS additions to `admin.css`:**
```css
.filter-card { background: var(--color-surface-1); border: 1px solid var(--color-border); border-radius: var(--radius-lg); padding: var(--space-4) var(--space-5); margin-bottom: var(--space-5); }
.filter-card__search { width: 100%; padding: var(--space-2) var(--space-3); border: 1px solid var(--color-border); border-radius: var(--radius-md); font-family: inherit; font-size: var(--font-size-sm); color: var(--color-text); background: var(--color-surface-1); margin-bottom: var(--space-3); }
.filter-card__search:focus { outline: none; border-color: var(--color-border-focus); box-shadow: 0 0 0 3px rgba(37,99,235,0.15); }
.filter-card__controls { display: flex; gap: var(--space-4); flex-wrap: wrap; align-items: flex-end; }
.filter-card__field { display: flex; flex-direction: column; gap: var(--space-1); }
.filter-card__field label { font-size: var(--font-size-sm); font-weight: 600; color: var(--color-text-muted); }
.filter-card__field select { padding: var(--space-2) var(--space-3); border: 1px solid var(--color-border); border-radius: var(--radius-md); font-family: inherit; font-size: var(--font-size-sm); color: var(--color-text); background: var(--color-surface-1); }
.filter-card__field select:focus { outline: none; border-color: var(--color-border-focus); box-shadow: 0 0 0 3px rgba(37,99,235,0.15); }
```

**HTML structure (per list view):**
```html
<div class="filter-card">
  <input type="search" name="q" class="filter-card__search" placeholder="Search by name…" …htmx attrs…>
  <div class="filter-card__controls">
    <div class="filter-card__field">
      <label for="status-filter">Status</label>
      <select id="status-filter" name="status" …htmx attrs…>…</select>
    </div>
  </div>
</div>
```

Existing `hx-get`, `hx-trigger`, `hx-target`, `hx-include`, `hx-push-url` attributes on the search input and status select are unchanged.

### 2. Status filter label

Replace bare `<select aria-label="Filter by status">` with a `.filter-card__field` block containing a visible `<label for="status-filter">Status</label>`. The `for`/`id` pairing preserves accessibility. Spacing resolves naturally from the column layout.

### 3. Pagination — separate GH issue

**Pattern:** Numbered pages with ellipsis + prev/next + position info.
`"Showing X–Y of Z" | ‹ Prev | 1 … 4 5 [6] 7 8 … 20 | Next ›`

**HTMX:** Out-of-band swap. `_rows.html` emits:
- `<tr>` rows into `#people-table-body` (existing swap target — no changes to search/status HTMX attrs)
- `<div id="people-pagination" hx-swap-oob="true">…controls…</div>` alongside

Pagination links carry: `hx-get`, `hx-target="#people-table-body"`, `hx-push-url="true"`, `hx-include="[name='q'],[name='status']"`.

**Backend:** Add `total_pages` (`math.ceil(total / PAGE_SIZE)`) to context in `people.py`, `orgs.py`, `roles.py`, `role_assignments.py`. All already have `page`, `page_size`, `total`.

**CSS:** Existing `.pagination` and `.pagination__info` classes used as-is.

## Affected files

### Filter card + status label (this issue)
- `src/static/admin/admin.css` — add `.filter-card` family
- `src/templates/admin/people/list.html`
- `src/templates/admin/orgs/list.html`
- `src/templates/admin/roles/list.html`
- `src/templates/admin/role_assignments/list.html`

### Pagination (separate issue)
- `src/api/admin/people.py` — add `total_pages` to context
- `src/api/admin/orgs.py`
- `src/api/admin/roles.py`
- `src/api/admin/role_assignments.py`
- `src/templates/admin/people/_rows.html` — add OOB pagination block
- `src/templates/admin/orgs/_rows.html`
- `src/templates/admin/roles/_rows.html`
- `src/templates/admin/role_assignments/_rows.html`

## Out of scope
- Filter card on detail pages
- Configurable page size
- Cursor-based pagination
- Additional filter controls (date range, etc.)
