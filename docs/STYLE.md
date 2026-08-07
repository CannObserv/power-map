# power-map — Visual Style Guide

Brand assets, colour, dark mode, CSS design tokens, layout and breakpoints, plus
internationalization and performance rules for the admin dashboard. Component and
interaction patterns live in `docs/UI.md`, `docs/HTMX.md`, `docs/FORMS.md` and
`docs/MERGE.md`; server-side rules in `docs/ADMIN.md`.

---

## Brand Assets


| Asset | Path | Size |
|---|---|---|
| Brand icon (topbar) | `/static/images/cannabis_observer-icon-square.svg` | 28 x 28 px |
| Brand icon (footer) | `/static/images/cannabis_observer-icon-square.svg` | 18 x 18 px |

Footer emoji sequence — always wrap decorative emoji in `aria-hidden`:

```html
<span aria-hidden="true">🌱🏛️🔍</span>
```

---

## Color Palette


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
| `.badge--inactive` | `#f1f5f9` | `#556070` | `#1e293b` | `var(--color-text-muted)` |
| `.badge--neutral` | `#f1f5f9` | `#556070` | `#1e293b` | `var(--color-text-muted)` |
| `.badge--archived` | `#fee2e2` | `#991b1b` | `#450a0a` | `#fca5a5` |
| `.badge--success` | `#dcfce7` | `#15803d` | `#14532d` | `#86efac` |
| `.badge--warning` | `#fef9c3` | `#854d0e` | `#422006` | `#fde68a` |

`.badge--neutral` and `.badge--inactive` intentionally share a palette but are **kept as distinct rules** — do not consolidate them. `--neutral` is an informational tag (e.g. a non-US country code on an address row); `--inactive` is a lifecycle status. A `badge--X` referenced in a template but missing from CSS falls back to bare `.badge` (no bg/fg) and renders unstyled — `tests/api/admin/test_css.py` guards against that. Both avoid the lower-contrast `--color-inactive` for text: light blocks use `#556070` (≈5.8:1) and dark blocks use `--color-text-muted` (≈5.7:1), so the label clears WCAG AA on the grey background.

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

## Dark Mode


### Class-based approach

Dark mode uses `html.dark` / `html.light` classes. The `dark-mode.js` toggle sets **one or neither** on `<html>`:

- `html.dark` — force dark
- `html.light` — force light
- *neither class* — follow OS (the **system** state); the `@media (prefers-color-scheme: dark)` rules govern

**Specificity:** `html.dark` selector is `(0,1,1)`, which beats the `:root` selector `(0,0,1)` inside `@media (prefers-color-scheme: dark)`. This lets explicit user preference override the OS setting.

### Three-state toggle (#25)

`#theme-toggle` cycles the stored preference: **light → system → dark → light**. The cycle is driven off the *stored* `localStorage` value (not the rendered class) — `system` and explicit `light` both render as light and are indistinguishable by class. The **system** state is the *absent* key: reaching it calls `localStorage.removeItem`, so the FOUC `<head>` script needs no extra case (absent already means follow-OS there).

### No-JS fallback

`@media (prefers-color-scheme: dark) { :root { ... } }` in `admin.css` handles dark mode when JavaScript is disabled or the toggle has never been used.

### FOUC prevention

This inline script **must** appear in `<head>` **before** `<link rel="stylesheet">`. No `"system"` value is ever stored — the system state is the absent key — so the script's existing `s===null` follow-OS branch covers it unchanged:

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
| absent | **system** — follow OS `prefers-color-scheme`; the toggle's third state (written by clearing the key, not a `"system"` literal) |

### Toggle button

```html
<button class="btn btn--ghost btn--sm admin-topbar__theme-toggle"
        id="theme-toggle"
        aria-label="Color theme"
        type="button">
  <span data-theme-icon aria-hidden="true"></span>
</button>
```

- `id="theme-toggle"` — required by `dark-mode.js`
- `<span data-theme-icon>` — required; JS sets the per-state icon (current-state): ☀ light, ◑ system, ☽ dark
- **Single source of truth:** the per-state icon + `aria-label` strings live only in `dark-mode.js`'s `META` map. The server can't know the client's stored preference, so it renders a **neutral** default (`aria-label="Color theme"`, empty icon); JS populates the correct state on load and after each `htmx:afterSettle`. (Starting empty also avoids a wrong-glyph flash for users whose stored preference isn't `system`.)
- **No-JS / pre-JS affordance:** `admin.css` gives the empty span a `[data-theme-icon]:empty::before { content: "◑"; }` placeholder (the neutral system glyph) so the button never renders blank before JS runs; once JS sets real `textContent` the `:empty` rule stops matching.
- `dark-mode.js` loaded with `defer` from `<head>` (document-delegated, survives hx-boost swaps)

---

## CSS Design Token System


### Naming conventions

| Prefix | Purpose | Examples |
|---|---|---|
| `--color-*` | Colors | `--color-brand`, `--color-surface-0` |
| `--space-*` | Spacing (numbered scale) | `--space-1` (0.25rem) through `--space-8` (4rem) |
| `--font-size-*` | Font sizes | `--font-size-xs` (0.65rem) through `--font-size-xl` (1.375rem) |
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

### Font size scale

| Token | Value | Use |
|---|---|---|
| `--font-size-xs` | `0.65rem` | Entity-type label (`page-header__type`) |
| `--font-size-sm` | `0.8125rem` | Tables, labels, sidebar, badges |
| `--font-size-md` | `0.9375rem` | Body text, form inputs |
| `--font-size-lg` | `1.125rem` | Card titles |
| `--font-size-xl` | `1.375rem` | Page `<h1>` |

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

## Layout Conventions


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

## Responsive Breakpoints


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

## Internationalization Groundwork


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

## Performance Rules


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

## Metadata Footer


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
