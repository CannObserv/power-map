# power-map — Admin UI Components

Reusable admin dashboard components and the table/list conventions built on them:
buttons, badges, modals, page headers, empty states, inline-row controls, and the
row-key contract every multi-instance partial follows.

---

## UI Components


### Button variants

| Class | Style | When to use |
|---|---|---|
| `.btn--primary` | Brand fill, white text | Primary actions (Save, Submit) |
| `.btn--secondary` | Brand-subtle fill (`--color-brand-subtle`), brand text | Secondary actions (Edit, Add, Cancel on forms) |
| `.btn--ghost` | Transparent, border | Tertiary / toolbar actions |
| `.btn--danger` | Red fill | Destructive actions (Delete, Archive) |

Sizes: `.btn--sm` (compact, `padding: var(--space-1) var(--space-3)`, min-height 44px still applies).

### Row action-cell button order

In a table row's action cell, order buttons left → right by escalating effect: navigation / neutral (Open, View) → mutation (Edit) → destructive (Archive, Delete). This pairs with the destructive-last rule in `docs/HTMX.md` § Destructive Actions.

### Pill toggle (`.toggle`)

Used for auto-saving boolean fields. The checkbox is hidden; `.toggle__track` + `.toggle__thumb` render the pill.

```html
<label class="toggle">
  <input type="checkbox"
         name="active"
         value="true"
         {% if org.active %}checked{% endif %}
         {% if org.archived_at %}disabled{% endif %}
         hx-post="/admin/orgs/{{ org.id }}/inline/active/"
         hx-target="#active-toggle"
         hx-swap="outerHTML"
         hx-include="this">
  <span class="toggle__track"><span class="toggle__thumb"></span></span>
  <span class="toggle__label"><!-- label or badge here --></span>
</label>
```

**Rules:**
- The checkbox is hidden via the clip technique (`position:absolute; width:1px; height:1px; clip:rect(0 0 0 0)`), **not** `width:0;height:0`, and `.toggle` must stay `position:relative`. A zero-size absolute box with no positioned ancestor anchors to the viewport, so focusing it (label click) scrolls the inner `.admin-main` container to a phantom offset and overlays empty whitespace (#253). Guarded by `tests/js/toggle-focus-scroll-guard.test.js`.
- Always `disabled` when the entity is archived — archiving/unarchiving is a separate action (Danger Zone). CSS dims the disabled toggle via `.toggle:has(input:disabled)`.
- The toggle label (`toggle__label`) can hold a badge (e.g. Active/Inactive/Archived) instead of plain text.
- No visible label text needed when column/section context makes the field self-evident — but always add `aria-label` on the checkbox for screen reader accessibility (e.g. `aria-label="Canonical"`).
- HTMX trigger on `<input>` directly (not a wrapping form). Unchecked = no value submitted = `Form("")` = `False`; checked = `value="true"` submitted = `True`.
- No explicit `hx-trigger` needed — HTMX defaults to `change` for form inputs, which is correct for checkboxes. Do **not** use `hx-trigger="click"`: click can fire before the checked state updates on some browsers, sending the wrong value.

### Field group label (`.field-group-label`)

Use for subsection header labels within entity cards and in inline form sections. Same visual style as `h2` entity-section headers but at a lower level (typically `<h3>`).

```html
<h3 class="field-group-label">Names</h3>
```

CSS: `margin: 0; font-size: var(--font-size-sm); font-weight: 600; text-transform: uppercase; letter-spacing: 0.06em; color: var(--color-text-muted)`.

In form contexts where accessibility requires a `<label for>`, use `<label>` with the same class:

```html
<label for="notes-textarea" class="field-group-label" style="margin-bottom:var(--space-3)">Notes</label>
```

### Entity card subsection layout

Within `.entity-card`, subsections (e.g. Names, Acronyms, Notes) follow this structure:

```html
<!-- Section header row: label left, action button right -->
<div style="display:flex;align-items:center;justify-content:space-between;margin:var(--space-5) 0 var(--space-3)">
  <h3 class="field-group-label">Names</h3>
  <button class="btn btn--sm btn--secondary" ...>+ Add name</button>
</div>

<!-- Table with fixed-layout columns -->
<div class="table-wrapper">
  <table id="names-table" class="data-table" style="table-layout:fixed">
    <thead>
      <tr>
        <th scope="col">Name</th>
        <th scope="col" style="text-align:right;width:5rem">Type</th>
        <th scope="col" style="text-align:right;width:6rem">Canonical</th>
        <th scope="col" style="width:9rem"></th>
      </tr>
    </thead>
    ...
  </table>
</div>
```

`margin-top: var(--space-5)` on the header div separates subsections; the first subsection (directly after `.entity-card` opens) omits this top margin.

### Cross-table column alignment

When two sibling tables must visually align matching columns (e.g. Canonical and Actions across Names and Acronyms tables):

1. Add `style="table-layout:fixed"` to both `<table>` elements.
2. Set identical `width` values on the matching right-side `<th>` elements in both tables (`width:6rem` for Canonical, `width:9rem` for Actions).
3. The leftmost column absorbs all remaining space — no explicit width needed.

### Notes inline edit pattern

The Notes field uses a separate read/edit partial pair with a header row following the subsection layout pattern. Key details:

- `id="notes-field"` on the outer `<div>` — HTMX target for both read and edit partials.
- Read partial: bordered content box (`border: 1px solid var(--color-border); border-radius: var(--radius-md); background: var(--color-surface-1)`) with muted placeholder `—` when empty.
- Edit partial: `<form>` wraps the entire partial (header + textarea). Header is the same flex `display:flex;align-items:center;justify-content:space-between` row as the read partial, with `<label for="notes-textarea" class="field-group-label">` on the left (not `<h3>`) for proper screen-reader association, and Save (`type="submit"`) + Cancel (`hx-get`) buttons on the right inside a `<div>`. No `form-actions` div — buttons live in the header row.
- GET `/inline/notes/` → read partial; GET `/inline/notes/edit/` → form partial; POST `/inline/notes/` → read partial.
- Empty/whitespace notes saved as `NULL` (`.strip() or None`).
- **Archived guard:** the Edit button in the read partial must be hidden when the entity is archived. Wrap it in `{% if not entity.archived_at %}…{% endif %}`. This applies to all inline edit buttons in all read partials — see `docs/HTMX.md` § Generic Single-Field Inline Edit Pattern for the general rule.

---

## Confirmation Modals


### Pattern

`admin-modal.js` registers a global `htmx:confirm` listener that intercepts HTMX's built-in `window.confirm()` call and renders a styled, accessible in-page modal instead. Add `hx-confirm` to any button that needs a confirmation step — no additional JS or server routes required.

### Author-facing API

| Attribute | Required | Default | Purpose |
|---|---|---|---|
| `hx-confirm="<message>"` | yes | — | Modal body text |
| `data-confirm-title="<text>"` | no | `"Are you sure?"` | Modal heading (`<h2>`) |
| `data-confirm-label="<text>"` | no | `"Confirm"` | Confirm button text |
| `data-confirm-variant` | no | `"danger"` | Confirm button variant: `"danger"` or `"primary"` |

### Example

```html
<button type="button"
        hx-post="/admin/orgs/{{ org.id }}/inline/parent/"
        hx-target="#parent-row"
        hx-swap="outerHTML"
        hx-vals='{"parent_id": ""}'
        hx-confirm="Remove parent organization?"
        data-confirm-label="Unlink">
  Unlink
</button>
```

### Default variant

Default is `danger`. Confirmations in this admin are almost exclusively for destructive actions. Use `data-confirm-variant="primary"` only when the confirmed action is non-destructive.

### Accessibility

- `role="dialog"` with `aria-modal="true"`, `aria-labelledby` (title `<h2>`), `aria-describedby` (message `<p>`)
- Focus defaults to **Cancel** on open — avoids accidental confirm on Enter
- Tab / Shift-Tab trapped within the modal's focusable elements
- Escape, Cancel, and Confirm all close the modal and restore focus to the trigger

### No backdrop-click-to-close

Intentional — prevents accidental dismissal from stray clicks. Consistent with `delete_modal.html`.

### When NOT to use

Do not use `hx-confirm` for the hard-delete modal (`delete_modal.html`). That modal requires inline error display (409 handling, network errors) and a server-rendered entity label — use the server-rendered partial pattern for that case.

---

## Page Header Pattern


Two variants — pick by page type. Both live inside `{% block content %}`.

### List page

```html
<div class="page-header">
  <h1>Organizations</h1>
  <a href="/admin/orgs/new/" class="btn btn--primary">+ Add organization</a>
</div>
```

- `page-header` is a flex row (`justify-content: space-between`).
- Action button is optional; omit the `<a>` when there is no primary list action.

### Detail page

```html
<div class="page-header">
  <div>
    <span class="page-header__type">Organization</span>
    <h1 id="page-heading">{{ display_name or '(unnamed)' }}</h1>
  </div>
</div>
```

- `page-header__type` — muted uppercase entity-type label above the `<h1>`. Always present on detail pages to orient the user.
- `id="page-heading"` — required when the page title can change via HTMX (e.g. after an inline name edit). `org-detail.js` listens for `updateOrgHeader` events and updates `#page-heading`, `#breadcrumb-current`, and `document.title` in-place. See `docs/ADMIN.md` → Page header sync.
- Breadcrumb: `<span id="breadcrumb-current">` holds the live display name in the trail.

```html
{% block breadcrumb %}
  <a href="/admin/">Dashboard</a><span class="breadcrumb__sep">›</span>
  <a href="/admin/orgs/">Organizations</a><span class="breadcrumb__sep">›</span>
  <span id="breadcrumb-current">{{ display_name or org.id }}</span>
{% endblock %}
```

### Adding live header sync to a new entity type

Follow these steps whenever a new entity detail page needs its `<h1>`, breadcrumb, and `document.title` to update live after an inline name edit. See `docs/HTMX.md` § Per-Entity Live Header Sync for the full pattern spec.

1. **JS file** — create `src/static/admin/{entity}-detail.js` listening for `update{Entity}Header` (camelCase, e.g. `updatePersonHeader`).
2. **`deps.py`** — add `{entity}_header_extra(entity_id, db)`: query the display-name view, fall back to `entity_id`, return `{"update{Entity}Header": {"display": display}}`.
3. **Mutation routes** — on every route that can change the canonical name, pass `extra=await {entity}_header_extra(entity_id, db)` to `flash_trigger()`.
4. **`base.html`** — load the JS in `base.html`'s `<head>` with `defer` (NOT the detail template's `extra_head`, which hx-boost strips on boosted navigation — see "hx-boost re-execution"). The listener is global and idempotent, so loading it site-wide is safe.
5. **Tests** — add 5 structural tests in `test_js.py` (file exists, event key, `page-heading`, `breadcrumb-current`, `document.title`). See `docs/HTMX.md` § Per-Entity Live Header Sync for the checklist.

---

## Empty-State Table Rows


Every `<tbody>` that can be empty must include a fallback row via Jinja `{% else %}`:

```html
<tbody>
  {% for n in names %}
  {% include "admin/orgs/partials/_name_row.html" %}
  {% else %}
  <tr>
    <td colspan="4" style="text-align:center;color:var(--color-text-muted)">No names</td>
  </tr>
  {% endfor %}
</tbody>
```

- `colspan` must match the actual column count of the table.
- Text: `"No {plural noun}"` — lower-case, no punctuation.
- Color: `var(--color-text-muted)` — never a lighter value.
- No icon, no call-to-action link inside the cell — those belong in the section header row.

### Jinja2 include variable scoping

`{% include %}` shares the caller's full template context, but the partial uses variables by name. If a partial references `person_id` while the outer template's context only contains `person`, set the variable explicitly immediately before each `{% include %}`:

```html
{% set person_id = person.id %}
{% include "admin/people/partials/_name_row.html" %}
```

Do this for every `{% include %}` of that partial in the same template — Jinja2 `{% set %}` in a `{% for %}` loop does not leak out of the loop body.

---

## Toggle in Inline Form Rows (Non-Auto-Save)


The `.toggle` component (§ UI Components) can also be used as a plain form field inside an edit row — for boolean attributes that are saved with the rest of the row, not auto-saved independently.

```html
<label class="toggle" style="flex-shrink:0">
  <input type="checkbox" name="is_canonical" value="true" aria-label="Canonical"
         {% if n and n.is_canonical %} checked{% endif %}>
  <span class="toggle__track"><span class="toggle__thumb"></span></span>
</label>
```

Differences from the auto-saving toggle (§ UI Components):

| | Auto-save toggle | Form-row toggle |
|---|---|---|
| Has `hx-post` | Yes — fires on `change` | No — submits with the form |
| Has `hx-include` | Yes | No |
| Has `disabled` guard | Yes (when archived) | No (row is hidden when archived) |
| `toggle__label` text | Present (Active / Inactive) | Omitted — column header provides context |
| `aria-label` on `<input>` | Optional | Required — no visible label |

No explicit `hx-trigger` needed on either variant: HTMX defaults to `change` for checkboxes. Do **not** use `hx-trigger="click"`.

---

## Section-Level Add Button


When a detail-page section contains a single flat table (no subsection `field-group-label` headers), the `+ Add` button sits next to the section `<h2>` rather than inside the entity card.

```html
<section class="entity-section">
  <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:var(--space-3)">
    <h2>Links</h2>
    <button class="btn btn--sm btn--secondary"
            hx-get="/admin/orgs/{{ org.id }}/links/new-row/"
            hx-target="#links-table tbody"
            hx-swap="afterbegin"
            type="button">+ Add link</button>
  </div>
  <div class="table-wrapper">
    <table id="links-table" class="data-table"> ... </table>
  </div>
</section>
```

Contrast with the within-card subsection pattern (§ UI Components) where the header and button are inside `.entity-card` and `<h3 class="field-group-label">` replaces `<h2>`.

**Rule of thumb:**
- One table → section-level (`<h2>` + button outside entity-card)
- Multiple tables grouped by topic → within-card subsections (`<h3 class="field-group-label">` + button inside `.entity-card`)

New rows prepend via `hx-swap="afterbegin"` on `<tbody>` — this keeps the new blank form row at the top without a server sort round-trip.

---

## `hx-push-url` on List Filters


All list-view search inputs and filter selects include `hx-push-url="true"` so that filter state is reflected in the browser URL. This enables:

- Bookmarking a filtered view
- Browser back/forward restoring the filter state
- Copying the URL to share a filtered result

```html
<input type="search" name="q" value="{{ q }}"
       hx-get="/admin/orgs/"
       hx-trigger="input delay:300ms, search"
       hx-target="#orgs-list-region"
       hx-include="[name='status'],[name='page_size']"
       hx-push-url="true">

<select name="status"
        hx-get="/admin/orgs/"
        hx-trigger="change"
        hx-target="#orgs-list-region"
        hx-include="[name='q'],[name='page_size']"
        hx-push-url="true">
  ...
</select>
```

- Each filter `hx-include`s the other active filters so the URL reflects the full combined state.
- Page-size selects add `hx-vals='{"page": 1}'` to reset pagination on page-size change.
- The partial region (`#orgs-list-region`) must include `aria-live="polite" aria-atomic="false"` (`docs/HTMX.md` § HTMX Patterns).

---

## Clipboard Copy Button


For table rows that display a URL or other copyable value, add a Copy button that writes to the clipboard and emits a flash without a server request.

```html
<button type="button" class="btn btn--sm btn--secondary"
        data-url="{{ l.url }}"
        onclick="navigator.clipboard.writeText(this.dataset.url).then(
          function() { htmx.trigger(document.body, 'showFlash', {level:'success', body:'URL copied to clipboard'}) },
          function() { htmx.trigger(document.body, 'showFlash', {level:'error', body:'Copy failed \u2014 clipboard access denied'}) }
        )">Copy</button>
```

- Store the value in a `data-*` attribute (`data-url`), not inline in the `onclick` string, to avoid escaping issues with quotes in URLs.
- `htmx.trigger(document.body, 'showFlash', {...})` dispatches the same event that server-side `flash_trigger()` uses — `flash.js` handles it identically.
- Two callbacks: success (green) and failure (red, clipboard access denied in insecure contexts or when user denies permission).
- No `hx-*` attributes needed — this is purely client-side.

---

## Re-sort Response: `_rows.html` vs. `_row.html`


Row-level HTMX editing normally returns a single updated row (`hx-swap="outerHTML"` on `#{row-id}`). When a mutation can change the **sort order** of the table, return a full tbody replacement instead.

### When to use each

| Response | Swap | Use when |
|---|---|---|
| `_row.html` single row | `hx-target="#{row-id}" hx-swap="outerHTML"` | Edit doesn't affect ordering (value, label, URL) |
| `_rows.html` full tbody | `hx-target="#{table-id} tbody" hx-swap="innerHTML"` | Edit may reorder (canonical flag, type affecting sort) |

### `_rows.html` partial

A minimal partial that re-renders all rows from a fresh sorted query result:

```html
{# admin/{entity}/partials/_{subsection}_rows.html #}
{% for n in names %}
{% include "admin/{entity}/partials/_{subsection}_row.html" %}
{% else %}
<tr><td colspan="4" style="text-align:center;color:var(--color-text-muted)">No names</td></tr>
{% endfor %}
```

The route fetches all rows with the canonical sort (`ORDER BY is_canonical DESC, name_type, name`) and passes the full list as `names` (or equivalent). The client-side `hx-target` on the form points at `#{table-id} tbody`.

### Non-HTMX path

Always use `RedirectResponse` to the detail page — the full page reload re-renders with the correct sort naturally.

### New-row (create) also uses `_rows.html`

Even the create route uses `_rows.html` (not the new single row) when the new row must be inserted in sorted position rather than prepended. Contrast with § Section-Level Add Button (section-level add button) where `hx-swap="afterbegin"` prepends the blank form row — create POST then replaces the full tbody to insert the saved row in its sorted position.

---

## Multi-instance form-row partials: row-key contract


Inline form-row partials under `src/templates/admin/**/partials/_*_form_row.html` may render multiple times in the same DOM (e.g. an inline "+ Add" row alongside an open edit drawer for the same entity type). Without a per-row id suffix, the second `initTypeaheadCombobox(...)` call resolves every `getElementById` lookup to the first form's elements — typing in form #2 silently mutates form #1's hidden field and form #2's listbox never renders. (Issue #125 surfaced this on the person-name typeahead row.)

### Convention

Every multi-instance form-row partial accepts an optional `row_key` template variable, defaulting to `'new'` for the standard "+ Add" inline row:

```jinja
{# Module-level header — point at this STYLE.md section, not a paragraph per file #}
{%- set row_key = row_key | default('new') -%}
<tr id="entity-row-{{ row_key }}">
  <input id="entity-search-display-{{ row_key }}"
         aria-controls="entity-search-results-{{ row_key }}" ...>
  <input type="hidden" id="entity-id-hidden-{{ row_key }}" name="entity_id">
  <ul id="entity-search-results-{{ row_key }}" ...></ul>
  <script>
    window.initTypeaheadCombobox({
      inputId: 'entity-search-display-{{ row_key }}',
      listboxId: 'entity-search-results-{{ row_key }}',
      hiddenId: 'entity-id-hidden-{{ row_key }}',
    });
  </script>
</tr>
```

Rules:

- Every `id="..."` gets the `-{{ row_key }}` suffix.
- Matching `aria-controls` and `initTypeaheadCombobox` arguments get the same suffix.
- `name=` attributes stay unchanged so form submission posts the right field.
- Callers can omit `row_key` for the standard inline-add row; pass `row_key=<entity.id>` (or any unique-on-the-page string) for edit drawers and other multi-row contexts.
- The standard inline-add row renders `id="<entity>-row-new"` (the `'new'` default). Its "+ Add" button opts into the duplicate-row guard via `data-new-row-id="<entity>-row-new"` (+ `hx-sync="this:drop"`), and the new-row Cancel dispatches `powerMap:newRowClosed` — see `docs/ADMIN.md` ("+ Add duplicate-row guard"). Wire all three when adding a new multi-instance partial.

### Singleton-only partials

Some partials are guaranteed singletons (one parent per org, one org field per role, one open merge modal at a time). They DO NOT need the row-key dance — but the audit conclusion belongs in the partial's top-of-file comment so future contributors don't copy the singleton pattern into a multi-instance flow:

- `src/templates/admin/orgs/partials/_parent_form.html` — singleton (swap target `#parent-row`)
- `src/templates/admin/roles/partials/_org_form.html` — singleton (swap target `#org-field`)
- `src/templates/admin/orgs/_merge_search_modal.html` — modal portal pattern (one open at a time)

### Test coverage

`tests/js/typeahead-row-key-collision.test.js` is the regression guard. It builds two forms in the same DOM with distinct row-keys, evals the real `typeahead-combobox.js` factory, and asserts a selection in form B never mutates form A's hidden field — and that `aria-controls` on each input points at its own listbox. Add cases there when introducing a new multi-instance partial.
