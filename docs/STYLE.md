# Power Map — Style Guide

Authoritative reference for the admin dashboard UI. All values derived from source code.

---

## 1. Brand Assets

| Asset | Path | Size |
|---|---|---|
| Brand icon (topbar) | `/static/images/cannabis_observer-icon-square.svg` | 28 x 28 px |
| Brand icon (footer) | `/static/images/cannabis_observer-icon-square.svg` | 18 x 18 px |

Footer emoji sequence — always wrap decorative emoji in `aria-hidden`:

```html
<span aria-hidden="true">🌱🏛️🔍</span>
```

---

## 2. Color Palette

### Design tokens

| Token | Light | Dark | Purpose |
|---|---|---|---|
| `--color-brand` | `#6d4488` | `#a78bc4` | Primary accent (co-purple) |
| `--color-brand-hover` | `#5a3870` | `#c4aed8` | Brand hover state |
| `--color-brand-subtle` | `#f5f0f8` | `#2d1f38` | Tinted background |
| `--color-brand-subtle-border` | `#ebe1f1` | `#4a3060` | Border for tinted background |
| `--color-green` | `#8cbe69` | `#8cbe69` | co-green — reserved, not used as UI accent |
| `--color-brand-glow` | `rgba(109,68,136,0.18)` | `rgba(167,139,196,0.20)` | Focus ring glow (`box-shadow`) |
| `--color-surface-0` | `#f8fafc` | `#0f172a` | Page background |
| `--color-surface-1` | `#ffffff` | `#1e293b` | Card / panel background |
| `--color-surface-2` | `#1e293b` | `#0f172a` | Legacy dark surface (not used for sidebar) |
| `--color-sidebar-bg` | `#ffffff` | `#0f172a` | Sidebar background |
| `--color-sidebar-text` | `#64748b` | `#cbd5e1` | Sidebar link text |
| `--color-sidebar-text-active` | `#6d4488` | `#ffffff` | Active / hovered sidebar link text |
| `--color-sidebar-hover-bg` | `#f5f0f8` | `rgba(255,255,255,0.08)` | Sidebar link hover / active background |
| `--color-text` | `#0f172a` | `#f1f5f9` | Primary text |
| `--color-text-muted` | `#64748b` | `#94a3b8` | Secondary / label text |
| `--color-text-inverse` | `#f1f5f9` | `#0f172a` | Text on dark surfaces |
| `--color-border` | `#e2e8f0` | `#334155` | Default border |
| `--color-border-focus` | `#6d4488` | `#a78bc4` | Focus ring outline |
| `--color-success` | `#16a34a` | `#4ade80` | Success state |
| `--color-warning` | `#d97706` | `#fbbf24` | Warning state |
| `--color-danger` | `#dc2626` | `#f87171` | Danger / error state |
| `--color-inactive` | `#94a3b8` | `#475569` | Inactive / disabled state |

### Component colors (hardcoded, NOT token-based)

Badge, alert, and flash colors are hardcoded per-class rather than derived from semantic tokens. This is intentional — semantic status colors (green/yellow/red/blue) must remain recognizable regardless of brand palette. Never replace them with `--color-brand` or `--color-success`.

**Badges (light / dark):**

| Class | Light bg | Light text | Dark bg | Dark text |
|---|---|---|---|---|
| `.badge--active` | `#dcfce7` | `#15803d` | `#14532d` | `#86efac` |
| `.badge--inactive` | `#f1f5f9` | `var(--color-inactive)` | `#1e293b` | `var(--color-inactive)` |
| `.badge--archived` | `#fee2e2` | `#991b1b` | `#450a0a` | `#fca5a5` |

**Alerts (light / dark):**

| Class | Light bg | Light text | Light border | Dark bg | Dark text | Dark border |
|---|---|---|---|---|---|---|
| `.alert--success` | `#f0fdf4` | `#15803d` | `#bbf7d0` | `#14532d` | `#86efac` | `#166534` |
| `.alert--error` | `#fef2f2` | `#991b1b` | `#fecaca` | `#450a0a` | `#fca5a5` | `#991b1b` |
| `.alert--warning` | `#fef9c3` | `#854d0e` | `#fde68a` | `#422006` | `#fde68a` | `#713f12` |
| `.alert--notice` | `#eff6ff` | `#1d4ed8` | `#bfdbfe` | `#1e3a5f` | `#93c5fd` | `#1d4ed8` |

**Flash (light / dark):**

| Class | Light bg | Light text | Light border | Dark bg | Dark text | Dark border |
|---|---|---|---|---|---|---|
| `.flash--success` | `#f0fdf4` | `#15803d` | `#bbf7d0` | `#14532d` | `#86efac` | `#166534` |
| `.flash--info` | `#eff6ff` | `#1d4ed8` | `#bfdbfe` | `#1e3a5f` | `#93c5fd` | `#1d4ed8` |
| `.flash--warning` | `#fef9c3` | `#854d0e` | `#fde68a` | `#422006` | `#fde68a` | `#713f12` |
| `.flash--error` | `#fef2f2` | `#991b1b` | `#fecaca` | `#450a0a` | `#fca5a5` | `#991b1b` |

---

## 3. Dark Mode

### Class-based approach

Dark mode uses `html.dark` / `html.light` classes. The `dark-mode.js` toggle sets one of these on `<html>`.

**Specificity:** `html.dark` selector is `(0,1,1)`, which beats the `:root` selector `(0,0,1)` inside `@media (prefers-color-scheme: dark)`. This lets explicit user preference override the OS setting.

### No-JS fallback

`@media (prefers-color-scheme: dark) { :root { ... } }` in `admin.css` handles dark mode when JavaScript is disabled or the toggle has never been used.

### FOUC prevention

This inline script **must** appear in `<head>` **before** `<link rel="stylesheet">`:

```html
<script>
  (function(){
    var k='pm-color-scheme', s=localStorage.getItem(k),
        d=window.matchMedia('(prefers-color-scheme: dark)').matches;
    var html=document.documentElement;
    if(s==='dark'||(s===null&&d)){html.classList.add('dark');}
    else if(s==='light'){html.classList.add('light');}
  })();
</script>
<link rel="stylesheet" href="/static/admin/admin.css">
```

If the script runs after the stylesheet loads, the user sees a flash of the wrong theme.

### localStorage key

Key: `pm-color-scheme`

| Value | Meaning |
|---|---|
| `"dark"` | User explicitly chose dark |
| `"light"` | User explicitly chose light |
| absent | Follow OS `prefers-color-scheme` |

### Toggle button

```html
<button class="btn btn--ghost btn--sm admin-topbar__theme-toggle"
        id="theme-toggle"
        aria-label="Switch to dark mode"
        type="button">
  <span data-theme-icon aria-hidden="true">&#9789;</span>
</button>
```

- `id="theme-toggle"` — required by `dark-mode.js`
- `<span data-theme-icon>` — required; JS swaps content to sun/moon
- `dark-mode.js` loaded with `defer` at end of `<body>`

---

## 4. CSS Design Token System

### Naming conventions

| Prefix | Purpose | Examples |
|---|---|---|
| `--color-*` | Colors | `--color-brand`, `--color-surface-0` |
| `--space-*` | Spacing (numbered scale) | `--space-1` (0.25rem) through `--space-8` (4rem) |
| `--font-size-*` | Font sizes | `--font-size-sm` (0.8125rem), `--font-size-md` (0.9375rem) |
| `--radius-*` | Border radii | `--radius-sm` (0.25rem), `--radius-md` (0.375rem) |
| `--topbar-h` | Topbar height | `3.25rem` |

### Spacing scale

| Token | Value |
|---|---|
| `--space-1` | `0.25rem` |
| `--space-2` | `0.5rem` |
| `--space-3` | `0.75rem` |
| `--space-4` | `1rem` |
| `--space-5` | `1.5rem` |
| `--space-6` | `2rem` |
| `--space-7` | `3rem` |
| `--space-8` | `4rem` |

### Font family

```css
--font-family-base: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
```

### Adding new tokens

1. Define in `:root` block at top of `admin.css`
2. If it's a color token, add corresponding overrides in **all three** places:
   - `@media (prefers-color-scheme: dark) { :root { ... } }` — no-JS fallback
   - `html.dark { ... }` — explicit dark
   - `html.light { ... }` — explicit light (resets to light values when OS is dark but user chose light)

---

## 5. Layout Conventions

### Grid structure

```
admin-layout (grid)
├── admin-topbar    (grid-column: 1 / -1, spans full width)
├── admin-sidebar   (240px column)
└── admin-main      (1fr column, scroll container)
    ├── {% block content %}
    └── admin-footer
```

- `admin-layout`: `grid-template-columns: 240px 1fr`, `grid-template-rows: auto 1fr`, `height: 100dvh`
- `admin-main`: `overflow-y: auto` — this is the page scroll container, not `<body>`
- `admin-sidebar`: 240px fixed width, `overflow-y: auto` for long nav lists
- `admin-footer`: inside `admin-main`, after `{% block content %}`

### Mobile (<=768px)

- Sidebar becomes fixed-position off-screen drawer (`transform: translateX(-100%)`)
- Grid collapses to `grid-template-columns: 1fr`
- Sidebar opens via `.is-open` class, closed with backdrop click or Escape key
- Drawer width: 260px (slightly wider than desktop sidebar for touch targets)

---

## 6. Responsive Breakpoints

| Breakpoint | What changes |
|---|---|
| `max-width: 768px` | Mobile nav — sidebar becomes fixed drawer, hamburger button shown, grid collapses to 1 column; `admin-main` padding reduces to `var(--space-4)` |
| `max-width: 640px` | `.dup-actions` stacks; `filter-card` controls stack to single column, select min-width reset; `detail-grid` collapses to 1 column |

### Touch targets

All interactive elements must meet **44×44 px** minimum touch target (Apple HIG / WCAG 2.5.5). Enforced via `min-height: 44px` on:

- `.btn` — all button variants including `.btn--sm`
- `.form-group input, .form-group select, .form-group textarea`
- `.form-group label:has(input[type=checkbox]), .form-group label:has(input[type=radio])`
- `.filter-card__search` — main list-view search input
- `.filter-card__field select, .filter-card__field input[type=search]`
- `.admin-topbar__menu-toggle` — hamburger (also `min-width: 44px`)
- `.admin-sidebar__link` — sidebar nav links (tapped after hamburger opens on mobile)
- `.admin-sidebar__sublink` — secondary nav links (e.g. "Duplicates")

Do not use `padding` alone to hit target size — always set `min-height` / `min-width` explicitly so the intent is visible in the CSS.

### detail-grid

`<dl class="detail-grid">` is the standard pattern for label/value pairs on detail views. Renders as a 2-column grid (`minmax(140px, max-content) 1fr`) on `≥640px`, single-column below.

```html
<dl class="detail-grid">
  <dt>Status</dt>
  <dd><span class="badge badge--active">Active</span></dd>
  <dt>Created</dt>
  <dd>2026-01-01 00:00:00 UTC</dd>
</dl>
```

### Sticky thead

`.data-table thead th` is `position: sticky; top: 0` with `background: var(--color-surface-1)` and `box-shadow: 0 1px 0 var(--color-border)`. This applies automatically to all tables using the `data-table` class. No per-template configuration needed. Note: uses `box-shadow` rather than `border-bottom` because sticky-element borders disappear at sub-pixel boundaries in some browsers.

### filter-card on narrow screens

At `≤640px`, `.filter-card__controls` stacks to `flex-direction: column` and `.filter-card__field select/input` have `min-width: 0; width: 100%`, overriding the desktop `min-width: 200px`.

### RTL support

`[dir="rtl"]` rules already exist:
- Sidebar grid flips to `1fr 240px`
- Sidebar link border moves from `border-left` to `border-right`
- Mobile drawer slides from right (`translateX(100%)`) instead of left
- `.flash-region` swaps from `right` to `left`

### Reduced motion

```css
@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after {
    animation-duration: 0.01ms !important;
    transition-duration: 0.01ms !important;
  }
}
```

---

## 7. HTMX Patterns

### `is_htmx(request)` helper

Canonical implementation in `src/api/admin/deps.py`:

```python
def is_htmx(request: Request) -> bool:
    return bool(
        request.headers.get("HX-Request") and not request.headers.get("HX-Boosted")
    )
```

**Why the `HX-Boosted` guard:** `hx-boost="true"` on `admin-layout` means boosted navigation sends both `HX-Request` and `HX-Boosted` headers. Without the guard, boosted sidebar clicks receive bare fragments instead of full-page layouts.

### OOB flash injection

At the end of any HTMX partial response, include:

```jinja
{% import "admin/macros/flash.html" as flash %}
{{ flash.oob("success", "Org merged.") }}
```

This emits a `<div id="flash-region" hx-swap-oob="beforeend">` wrapper that HTMX swaps into the existing `#flash-region`.

### Mutation form pattern

```html
<form hx-post="/admin/orgs/{{ id }}/archive/"
      hx-target="#org-card"
      hx-swap="outerHTML">
  <button class="btn btn--danger btn--sm">Archive</button>
</form>
```

Always provide a non-HTMX `RedirectResponse` fallback in the route handler for graceful degradation (direct form POST without JS).

### Loading states

CSS handles loading automatically — no per-form JS:

```css
.htmx-request, .htmx-request button, .htmx-request input, .htmx-request select {
  opacity: 0.6; cursor: wait; pointer-events: none;
}
```

### Live regions

All HTMX swap targets for list content must include:

```html
aria-live="polite" aria-atomic="false"
```

The `#flash-region` already has these attributes in `base.html`.

---

## 8. Flash / Notification UX

### Macros

Import: `{% import "admin/macros/flash.html" as flash %}`

| Macro | Use case | Signature |
|---|---|---|
| `flash.message(level, body, auto_dismiss_ms=4000)` | Inline flash (rendered in page) | `level`: success/info/warning/error |
| `flash.oob(level, body, auto_dismiss_ms=4000)` | OOB injection from HTMX partials | Same levels |

### Levels

| Level | CSS class | Color (light) |
|---|---|---|
| `success` | `.flash--success` | Green |
| `info` | `.flash--info` | Blue |
| `warning` | `.flash--warning` | Yellow |
| `error` | `.flash--error` | Red |

### Behavior

- Auto-dismiss after `auto_dismiss_ms` (default 4000ms)
- Hover pauses the dismiss timer; mouseleave restarts it
- Close button (`x`) removes immediately
- Animation: `flash-in` — 0.2s ease fade + slide from top

### XSS prevention

Always `markupsafe.escape()` any DB-derived value before passing to `body`:

```python
from markupsafe import escape
flash_body = f"Merged org <strong>{escape(org_name)}</strong>."
```

The `body` parameter uses `{{ body | safe }}` — it trusts the caller.

### Persistent banners

For non-dismissible notices (e.g. dup count on org list), use `.alert--notice` with a close button — no auto-dismiss timer.

---

## 9. Pagination Conventions

### Top pagination bar

`.pagination` — flexbox, right-aligned, with border + radius + `--color-surface-0` background:

```css
.pagination {
  display: flex; gap: var(--space-2); align-items: center;
  justify-content: flex-end; font-size: var(--font-size-sm);
  background: var(--color-surface-0); border: 1px solid var(--color-border);
  border-radius: var(--radius-md); padding: var(--space-2) var(--space-4);
}
```

### Sticky footer pagination

`.pagination--sticky` — sticky to bottom of scroll container, uses negative margins to break out of `admin-main` padding, backdrop-filter blur, border-top/bottom only (no side borders or radius):

```css
.pagination--sticky {
  position: sticky; bottom: 0; z-index: 10;
  margin-left: calc(-1 * var(--space-6));
  margin-right: calc(-1 * var(--space-6));
  padding: var(--space-2) var(--space-6);
  background: color-mix(in srgb, var(--color-surface-1) 85%, transparent);
  backdrop-filter: blur(8px);
  border-top: 1px solid var(--color-border);
  border-bottom: 1px solid var(--color-border);
}
```

### Page-size options

Standard select with options: 25, 50, 100, 250.

Both top and sticky footer pagination appear on all list views.

---

## 10. Destructive Actions

### Archive gate

`archived_at IS NOT NULL` required before hard delete. Attempting to delete an active record returns HTTP 409.

### Danger zone

Use `.danger-zone` component for destructive actions on detail views:

```html
<div class="danger-zone">
  <div>
    <div class="danger-zone__label">Delete this organization</div>
    <div class="danger-zone__desc">Must be archived first.</div>
  </div>
  <button class="btn btn--danger btn--sm">Delete</button>
</div>
```

### Flash confirmation

Always show success or error flash after HTMX mutation. Use `flash.oob()` in the partial response.

### Dedup merge

In-place HTMX update (swap the dup pair out of the list) + OOB flash with merge result.

---

## 11. Dedup Workflow

1. **Banner** on org list when `count_org_duplicates(db) > 0` — `.alert--notice` with link to review screen
2. **Review screen** at `/admin/orgs/duplicates/` — shows potential duplicate pairs
3. **Actions:** merge or dismiss per pair — HTMX partial response + OOB flash
4. **Nav badge** updates via lazy `hx-get="/admin/orgs/duplicate-count-badge/"` with `hx-trigger="load"`
5. **Cache:** `count_org_duplicates(db)` is TTL-cached (5 min, process-local). Call `_invalidate_dup_count_cache()` after merge or dismiss
6. **Caveat:** cache is per-process — under multi-worker gunicorn, counts may lag up to 5 min per worker

---

## 12. Accessibility (WCAG 2.1 AA)

### Emoji

All decorative emojis must be wrapped — never bare:

```html
<span aria-hidden="true">🌱🏛️🔍</span>
```

### Focus rings

Use `:focus-visible` (not `:focus`):

```css
outline: 2px solid var(--color-border-focus);
outline-offset: 2px;
```

### Icon-only buttons

Always include `aria-label`:

```html
<button aria-label="Toggle navigation">&#9776;</button>
```

### HTMX live regions

All swap targets: `aria-live="polite" aria-atomic="false"`.

During requests, `aria-busy="true"` is automatically set on the swap target via global
`htmx:beforeRequest` / `htmx:afterSettle` listeners in `base.html`. No per-form work needed.

### Form hints

Link hint text to its input via `aria-describedby`:

```html
<input id="acronym" name="acronym" aria-describedby="acronym-hint">
<div class="form-group__hint" id="acronym-hint">Short abbreviation.</div>
```

Hint `id` convention: `{field_name}-hint`.

### Modal focus management

All modals must trap focus and restore it on close. The delete modal in
`partials/delete_modal.html` is the canonical example:

- Capture `document.activeElement` (the trigger) before moving focus
- On open: focus first interactive element inside the modal
- Tab / Shift-Tab: cycle within the modal's focusable elements
- Escape: close and restore focus
- On close: null `window.__pmCloseModal` and `window.__pmHandleDeleteResult`, remove modal, restore focus to trigger
- On DELETE success: call `close()` via `window.__pmHandleDeleteResult(event)`
- On DELETE error: keep modal open, show inline `.alert--error` message; 409 → "archive first"; other → status code; status 0 → network error

### `title` attributes

Do NOT use `title` — inaccessible to keyboard and touch users.

### Muted text

Minimum color: `var(--color-text-muted)`. Never use anything lighter.

### Skip link

`.skip-link` targets `#main-content`:

```html
<a class="skip-link" href="#main-content">Skip to main content</a>
```

Hidden off-screen by default, visible on `:focus`.

### Reduced motion

All animations and transitions collapse to `0.01ms` under `prefers-reduced-motion: reduce`.

---

## 13. Internationalization Groundwork

### String externalization

No hardcoded UI text in Python route handlers. Keep user-facing strings in Jinja templates. Future: wrap in `_()` via Babel.

### Date/number formatting

Always use a helper. Future path: `babel.dates.format_date`.

### CSS logical properties

Use logical properties for all new CSS:

| Physical (avoid) | Logical (use) |
|---|---|
| `margin-left` | `margin-inline-start` |
| `margin-right` | `margin-inline-end` |
| `padding-top` / `padding-bottom` | `padding-block` |
| `border-left` | `border-inline-start` |

### HTML lang/dir

Required on `<html>`:

```html
<html lang="en" dir="ltr">
```

`lang` and `dir` will become dynamic when a language switcher is added.

### Character encoding

`<meta charset="utf-8">` required in `<head>`.

### NFC normalization

All DB text fields should be stored NFC-normalized.

### RTL

Existing `[dir="rtl"]` rules cover: sidebar position, sidebar link borders, flash-region position, mobile drawer direction.

---

## 14. Performance Rules

### No CDN scripts

All JS must be local static files. Exception: HTMX is currently loaded from `unpkg.com` — to be replaced with a local copy.

```html
<!-- Current (to be replaced) -->
<script src="https://unpkg.com/htmx.org@1.9.12" defer></script>
```

### Script loading

Use `defer` on all non-critical `<script>` tags. The FOUC prevention script is the sole exception — it must run synchronously before the stylesheet.

### Static asset caching

`Cache-Control: public, max-age=31536000`. Cache-bust via query parameter (`?v=...`).

### Images

Always set explicit `width` and `height` to prevent layout shift:

```html
<img src="..." alt="" width="28" height="28">
```

### Inline scripts

No large inline `<script>` blocks. Extract to `static/js/*.js`. Exceptions:
- FOUC prevention script (must be synchronous, in `<head>`)
- Mobile nav toggle (small, DOM-dependent)

---

## 15. UI Components

### Button variants

| Class | Style | When to use |
|---|---|---|
| `.btn--primary` | Brand fill, white text | Primary actions (Save, Submit) |
| `.btn--secondary` | Brand-subtle fill (`--color-brand-subtle`), brand text | Secondary actions (Edit, Add, Cancel on forms) |
| `.btn--ghost` | Transparent, border | Tertiary / toolbar actions |
| `.btn--danger` | Red fill | Destructive actions (Delete, Archive) |

Sizes: `.btn--sm` (compact, `padding: var(--space-1) var(--space-3)`, min-height 44px still applies).

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
- Always `disabled` when the entity is archived — archiving/unarchiving is a separate action (Danger Zone). CSS dims the disabled toggle via `.toggle:has(input:disabled)`.
- The toggle label (`toggle__label`) can hold a badge (e.g. Active/Inactive/Archived) instead of plain text.
- No label text needed when column/section context makes the field self-evident (e.g. canonical column in a names table).
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
- Edit partial: `<label for="notes-textarea" class="field-group-label">` (not `<h3>`) for proper screen-reader association; `form-actions` margin override `style="margin-top:var(--space-2)"` — the global `.form-actions` rule uses `var(--space-5)` which is too large for the compact inline context.
- GET `/inline/notes/` → read partial; GET `/inline/notes/edit/` → form partial; POST `/inline/notes/` → read partial.
- Empty/whitespace notes saved as `NULL` (`.strip() or None`).
