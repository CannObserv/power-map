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
| `.badge--success` | `#dcfce7` | `#15803d` | `#14532d` | `#86efac` |
| `.badge--warning` | `#fef9c3` | `#854d0e` | `#422006` | `#fde68a` |

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

### Flash from HTMX mutation routes

Use `flash_trigger(level, body)` from `src.api.admin.deps`. Pass it as the `headers` argument to `TemplateResponse`:

```python
from src.api.admin.deps import flash_trigger

return templates.TemplateResponse(
    request, "admin/orgs/_region.html", ctx,
    headers=flash_trigger("success", f"Merged <strong>{escape(name)}</strong>."),
)
```

HTMX dispatches a `showFlash` DOM event when it processes the `HX-Trigger` response header. `flash.js` catches that event and injects a flash `<div>` into `#flash-region` imperatively — no OOB element in the response body. This works for any swap target, including `<tr>`.

**Timing:** `HX-Trigger` fires immediately on response receipt, before the DOM swap. This is imperceptible for fixed-position flash overlays. If a future route needs to reference post-swap DOM state, use `HX-Trigger-After-Settle` as the header name instead.

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

### HTMX attribute inheritance

`hx-swap`, `hx-target`, and other HTMX attributes inherit from parent elements unless explicitly overridden. A typeahead `<input>` inside a `<form hx-swap="outerHTML">` will inherit `outerHTML` and replace its `hx-target` element entirely rather than updating its contents — destroying the `<ul>` the dropdown depends on.

**Rule:** always set `hx-swap="innerHTML"` explicitly on any typeahead search input whose parent `<form>` carries a different `hx-swap`.

```html
<input hx-get="..."
       hx-target="#search-results"
       hx-swap="innerHTML"   <!-- explicit override — do not omit -->
       ...>
```

### Do not mix table elements and non-table elements in one HTMX response

A `<tr>` and a `<div>` **cannot be siblings** in the same HTMX response body. When HTMX parses a response, it sets the HTML as `innerHTML` of a container `<div>`. A `<tr>` in non-table context is invalid HTML — the browser's foster-parenting algorithm moves or strips it. HTMX has special-case handling for table elements, but only when the **entire response** is a table fragment. Mixed content breaks that detection and silently discards the `<tr>`.

**Use `HX-Retarget` + `HX-Reswap` response headers instead of OOB** when you need a server response to update a different element than the form's `hx-target` — for example, to fill a modal portal without touching the triggering row:

```python
return templates.TemplateResponse(
    request, "admin/orgs/partials/_my_modal.html", ctx,
    headers={
        "HX-Retarget": "#modal-portal",
        "HX-Reswap": "innerHTML",
    },
)
```

HTMX overrides the client-side `hx-target` / `hx-swap` with the header values. The triggering element (e.g. the `<tr>` form row) stays untouched in the DOM. The modal portal receives the response body. The modal's own action forms then target the original row directly.

**When OOB is safe:** use it only when all elements in the response share the same parsing context (all are `<div>` / block, or all are wrapped in an explicit `<table>` / `<tbody>`). Never mix table and non-table elements as OOB siblings.

### Live regions

All HTMX swap targets for list content must include:

```html
aria-live="polite" aria-atomic="false"
```

The `#flash-region` already has these attributes in `base.html`.

---

## 8. Flash / Notification UX

### API

| Where | How |
|---|---|
| HTMX mutation route (Python) | `headers=flash_trigger(level, body)` from `src.api.admin.deps` |
| Inline / non-HTMX (Jinja) | `{% from "admin/macros/flash.html" import message as flash_message %}` then `{{ flash_message(level, body) }}` |

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

**Two escaping contexts — not competing approaches:**

| Context | Tool | Reason |
|---|---|---|
| Normal template variables (`{{ org.name }}`) | Jinja2 autoescape (automatic) | `Jinja2Templates` sets `autoescape=True` globally — no developer action needed |
| Flash body string with intentional HTML markup | `markupsafe.escape()` on user values | Body exits the Jinja2 pipeline via `\| safe` (inline) or JS `innerHTML` (HX-Trigger) — autoescape never runs |

Always `markupsafe.escape()` any DB-derived value before composing a flash body string:

```python
from markupsafe import escape
body = f"Merged org <strong>{escape(org_name)}</strong>."
```

Do **not** add `markupsafe.escape()` to values passed as normal template context — Jinja2 autoescape already handles them, and double-escaping will corrupt output (`&amp;lt;` instead of `<`).

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

---

## 16. Confirmation Modals

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

## 17. Page Header Pattern

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
- `id="page-heading"` — required when the page title can change via HTMX (e.g. after an inline name edit). `org-detail.js` listens for `updateOrgHeader` events and updates `#page-heading`, `#breadcrumb-current`, and `document.title` in-place. See AGENTS.md → Page header sync.
- Breadcrumb: `<span id="breadcrumb-current">` holds the live display name in the trail.

```html
{% block breadcrumb %}
  <a href="/admin/">Dashboard</a><span class="breadcrumb__sep">›</span>
  <a href="/admin/orgs/">Organizations</a><span class="breadcrumb__sep">›</span>
  <span id="breadcrumb-current">{{ display_name or org.id }}</span>
{% endblock %}
```

---

## 18. Typeahead / Combobox

Used when selecting a related entity by searching (e.g. parent organization, child organization). Combines an HTMX search input with a JS-managed dropdown and a hidden ID field.

### HTML structure

```html
<div class="form-group" style="margin-bottom:0;position:relative">
  <input id="parent-search" type="text" autocomplete="off"
         placeholder="Type to search…"
         value="{{ parent.display_name if parent else '' }}"
         hx-get="/admin/orgs/search/"
         hx-trigger="input changed delay:200ms"
         hx-target="#parent-search-results"
         hx-params="q"
         name="q"
         role="combobox"
         aria-expanded="false"
         aria-haspopup="listbox"
         aria-controls="parent-search-results"
         aria-autocomplete="list">
  <input type="hidden" name="parent_id" id="parent-id-hidden"
         value="{{ parent.id if parent else '' }}">
</div>
<ul id="parent-search-results" class="typeahead-results" role="listbox"></ul>
```

- **Hidden input** (`name="parent_id"`) holds the selected entity's ID. The visible text input is display-only; the form submits the hidden value.
- **`hx-params="q"`** — sends only the query string, not the other form fields.
- **`hx-trigger="input changed delay:200ms"`** — debounces; `changed` prevents re-firing on arrow-key navigation.
- **`hx-swap="innerHTML"`** must be explicit when the input is inside a `<form>` with a different `hx-swap` — see §7 HTMX attribute inheritance.

### Search results template (`_search_results.html`)

```html
{% for r in results %}
<li id="opt-{{ r.id }}" role="option"
    data-id="{{ r.id }}"
    data-label="{{ r.display_name }}">{{ r.display_name }}</li>
{% endfor %}
```

- `role="option"` on every `<li>`.
- `data-id` — entity ID; JS copies this to the hidden input on selection.
- `data-label` — display text; JS copies this to the visible input.
- `id` must be unique across the page. When multiple typeaheads coexist, prefix the ID in the `htmx:afterSwap` listener: `li.id = listboxId + '-' + li.id`.

### Scoped search endpoints

When the candidate list must exclude already-linked items (e.g. children), use a scoped endpoint rather than the generic `/search/`:

```
GET /{org_id}/children/search/?q=...
```

The scoped endpoint filters out the current entity and any already-linked records. See AGENTS.md → Child org scoped search.

### JavaScript contract

The inline JS block in the form partial (or an extracted `.js` file) must implement:

| Behaviour | Detail |
|---|---|
| **Open dropdown** | `htmx:afterSwap` on the `<ul>` — position via `getBoundingClientRect`, set `aria-expanded="true"` |
| **Close dropdown** | Outside click, scroll (capture phase), Escape key, or item selection — set `aria-expanded="false"`, clear `ul` |
| **Arrow navigation** | `ArrowDown` / `ArrowUp` — cycle `.is-active` class on `<li>` items, scroll into view, set `aria-activedescendant` |
| **Enter to select** | Copy `data-id` → hidden input, `data-label` → visible input, close dropdown |
| **Escape** | Close dropdown without selection |
| **Click to select** | `ul.addEventListener('click')` — delegate to `closest('[data-id]')` |
| **Scoped IDs** | In `afterSwap`, prefix each `li.id` with the listbox's own `id` to prevent duplicate IDs when two typeaheads are mounted |

---

## 19. Empty-State Table Rows

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

---

## 20. Toggle in Inline Form Rows (Non-Auto-Save)

The `.toggle` component (§15) can also be used as a plain form field inside an edit row — for boolean attributes that are saved with the rest of the row, not auto-saved independently.

```html
<label class="toggle" style="flex-shrink:0">
  <input type="checkbox" name="is_canonical" value="true" aria-label="Canonical"
         {% if n and n.is_canonical %} checked{% endif %}>
  <span class="toggle__track"><span class="toggle__thumb"></span></span>
</label>
```

Differences from the auto-saving toggle (§15):

| | Auto-save toggle | Form-row toggle |
|---|---|---|
| Has `hx-post` | Yes — fires on `change` | No — submits with the form |
| Has `hx-include` | Yes | No |
| Has `disabled` guard | Yes (when archived) | No (row is hidden when archived) |
| `toggle__label` text | Present (Active / Inactive) | Omitted — column header provides context |
| `aria-label` on `<input>` | Optional | Required — no visible label |

No explicit `hx-trigger` needed on either variant: HTMX defaults to `change` for checkboxes. Do **not** use `hx-trigger="click"`.

---

## 21. Section-Level Add Button

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

Contrast with the within-card subsection pattern (§15) where the header and button are inside `.entity-card` and `<h3 class="field-group-label">` replaces `<h2>`.

**Rule of thumb:**
- One table → section-level (`<h2>` + button outside entity-card)
- Multiple tables grouped by topic → within-card subsections (`<h3 class="field-group-label">` + button inside `.entity-card`)

New rows prepend via `hx-swap="afterbegin"` on `<tbody>` — this keeps the new blank form row at the top without a server sort round-trip.

---

## 22. Metadata Footer

All detail pages end with a metadata line showing the record's internal ID and timestamps, placed after the last section and before or inside `.danger-zone`.

```html
<p style="color:var(--color-text-muted);font-size:var(--font-size-sm);margin-top:var(--space-6)">
  Metadata &middot; ID: <code>{{ entity.id }}</code>
  &middot; Created: {{ entity.created_at.strftime('%Y-%m-%d') }}
  &middot; Updated: {{ entity.updated_at.strftime('%Y-%m-%d') }}
</p>
```

- `font-size: var(--font-size-sm)` — visually subordinate.
- `color: var(--color-text-muted)` — lowest-priority text.
- ID in `<code>` — monospace distinguishes it as a technical value.
- Dates formatted `YYYY-MM-DD` (no time component — too verbose for a footer).
- `&middot;` (·) as separator — not a pipe or dash.

---

## 23. `hx-push-url` on List Filters

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
- The partial region (`#orgs-list-region`) must include `aria-live="polite" aria-atomic="false"` (§7).

---

## 24. Merge Bar Pattern

A floating action strip for bulk-select-and-merge on tables with potentially duplicate rows. Currently used on the Roles table of the org detail page; `role-merge.js` drives it.

### Required DOM structure

```html
<!-- Toggle button — outside the table -->
<button id="roles-merge-btn" class="btn btn--sm btn--secondary" type="button">Merge</button>

<!-- Table — data-org-id used by the JS to build merge POST URLs -->
<div class="table-wrapper" style="position:relative">
  <table id="roles-table" class="data-table" data-org-id="{{ org.id }}">
    <thead>
      <tr>
        <!-- Merge checkbox column — hidden until merge mode -->
        <th scope="col" class="merge-col" style="display:none;width:2rem;padding-right:0"></th>
        <th scope="col">Title</th>
        ...
      </tr>
    </thead>
    <tbody>
      {% for role in roles %}
      <tr data-title="{{ role.title or '' }}" data-role-id="{{ role.id }}">
        <td class="merge-col" style="display:none;padding-right:0">
          <input type="checkbox" name="merge-select" value="{{ role.id }}">
        </td>
        <td>...</td>
        ...
      </tr>
      {% endfor %}
    </tbody>
  </table>

  <!-- Merge action bar — hidden until merge mode, positioned inside table-wrapper -->
  <div id="roles-merge-bar" class="merge-bar" style="display:none">
    <span class="merge-bar__label">Merge roles:</span>
    <button class="btn btn--sm btn--primary merge-bar__keep-a" type="button"></button>
    <button class="btn btn--sm btn--primary merge-bar__keep-b" type="button"></button>
  </div>
</div>
```

### JS data contract (`role-merge.js`)

| Element / attribute | Purpose |
|---|---|
| `#roles-table` | Root — JS attaches change delegation and reads `data-org-id` |
| `data-org-id` | Used to build `/admin/orgs/{id}/roles/{keep}/merge/{discard}/` POST URL |
| `#roles-merge-btn` | Toggle button — JS toggles text ("Merge" ↔ "Cancel merge") and classes (`btn--secondary` ↔ `btn--ghost`) |
| `#roles-merge-bar` | Action bar — shown when merge mode is active |
| `.merge-col` | Column cells hidden/shown en bloc on mode toggle |
| `input[name="merge-select"]` | Per-row checkbox; `value` = role ID; `data-title` read from the parent `<tr>` |
| `tr[data-title]` | Used by both merge checkbox reading and the inline roles filter |
| `.merge-bar__keep-a` / `.merge-bar__keep-b` | JS sets `hx-post` and `hx-confirm` dynamically; calls `htmx.process()` after attribute mutation |

### Progressive disclosure states

| Checked count | Label | Button A | Button B |
|---|---|---|---|
| 0 | "Select 2 roles to merge:" | `—` (disabled) | `—` (disabled) |
| 1 | "Select 1 more:" | Selected role title (disabled) | `—` (disabled) |
| 2 | "Merge roles:" | `Keep "A"` (enabled, `hx-post` set) | `Keep "B"` (enabled, `hx-post` set) |

Max selection is 2; additional checkboxes are `disabled` once two are checked.

### Exit conditions

Merge mode exits automatically after a successful merge (JS listens for the `showFlash` event dispatched by the flash system after the server responds).

### Client-side roles filter

The same `role-merge.js` also handles the roles filter input (`#roles-filter`). It filters `tr[data-title]` rows client-side by comparing `data-title.toLowerCase()` against the input value — no server round-trip.

```html
<input type="search" id="roles-filter" placeholder="Filter roles…"
       class="filter-card__search">
```

---

## 25. Clipboard Copy Button

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
