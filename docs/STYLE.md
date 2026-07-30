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

## 3. Dark Mode

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

## 4. CSS Design Token System

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

**Portal pattern — first-class use case:** `HX-Retarget` is also the right tool when a mutation *may or may not* produce a secondary UI element (confirm dialog, validation step). The form row's `hx-target` always points at itself; the server conditionally redirects the response to a dedicated portal `<div>` elsewhere in the page. The row is never touched. See §29 (Address confirm flow) for the full example.

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

### Row-action buttons

Every `btn--sm` in a read-row partial must have `aria-label`. Multiple identical labels
("Edit", "Delete") across rows on the same page fail WCAG 2.1 AA SC 2.4.6 / 4.1.2.

Pattern: `aria-label="[Action] [entity-specific descriptor]"`

- **[Action]**: imperative matching visible text (`Edit`, `Delete`, `Archive`, `Unarchive`, `View`, `Open`, `Unlink`, `Copy`, `Revoke`, `Grant`, `Close`)
  Exception: where the visible text *is* the descriptor (the API-key scope panel's Grant buttons show the scope id, not the verb), prefix the action in the `aria-label` (`aria-label="Grant {{ st.id }}"`). WCAG 2.5.3 (Label in Name) still holds — the visible scope id is contained in the accessible name.
- **[entity-specific descriptor]**: the row's most natural identifier — name, value, address type, etc.
  Address rows use `a.address_type` (e.g. `"Edit mailing address"`) since the full formatted address is unwieldy. If an entity has two addresses of the same type, labels will collide — acceptable given the rarity of this case.

```html
<button aria-label="Edit name {{ n.name }}">Edit</button>
<button aria-label="Delete contact {{ c.value }}">Delete</button>
<button aria-label="Archive assignment at {{ ra.org_name or '(unnamed)' }}">Archive</button>
<a aria-label="Edit {{ org.canonical_name or '(unnamed)' }}">Edit</a>
```

**Excluded**: Save/Cancel in form rows (`*_form_row.html`, `*_edit_row.html`) — only one row is
editable at a time, so disambiguation is not needed. Static linting enforced by
`tests/api/admin/test_aria_labels.py`.

**Looped buttons outside `*_rows?.html`**: the lint auto-discovers `*_row.html` / `*_rows.html`
partials only. A partial that renders repeated action buttons in a loop under a different name
(e.g. `settings/partials/_api_key_scopes.html` — per-scope Revoke/Grant) hits the same SC 2.4.6
problem but is missed by the glob. Add such files to `_EXTRA_LOOPED_BUTTON_TEMPLATES` in the lint
rather than widening the glob — most non-row partials carry single buttons (Close/Save) that
legitimately need no `aria-label`. Note the lint checks `aria-label` **presence**, not accessible
name by any mechanism, so a button labeled via visible text alone still needs an `aria-label` to
pass; when adding one, fold any `.visually-hidden` descriptor into the `aria-label` (it overrides
the text-node name) so nothing is dropped. (#247)

### Status badges

Entity state (active / inactive / archived / current / former, validation status, import
action, etc.) is **always conveyed by badge text** — never by color or icon alone (WCAG
1.4.1 Use of Color). Row-level styling (`tr.is-archived` strike-through, `tr.is-inactive`
muted first cell) is **redundant** with the in-row text badge and must never be the sole
signal; the `.badge` background color is decorative reinforcement only.

When adding a status indicator, render a text label inside the `.badge`. If a state must
appear without visible text (a space-constrained icon or colored dot), expose the state
name in a `.visually-hidden` span or `aria-label`. Audit 2026-06 (#245): no color-only or
icon-only status indicators exist in the admin UI — keep it that way.

### Presence indicators (notes) (#318)

Distinct from status badges: a **presence indicator** is an icon-only marker that some
optional payload exists, not an entity state. Assignment read rows
(`people/partials/_assignment_row.html`, `roles/partials/_assignment_row.html`) show a
`role="img" aria-label="Has notes"` glyph when `ra.notes` is set. Rules:

- The indicator carries an accessible name via `aria-label` (never a bare glyph, never
  `title`) — this does **not** breach the "no icon-only *status*" rule above, because entity
  status is still conveyed by its text `.badge` alongside.
- **Never render the note text inline.** Provenance can be long or sensitive; the row shows
  only that a note exists. Read/author the text on the standalone assignment page
  (`/admin/role-assignments/{id}/` → `_notes_read.html` / `_notes_form.html`), reached via
  the row's **Open** link. The inline create/edit forms deliberately carry no notes control
  (row real estate).

### Role & assignment attachment panels (#326)

Role and role-assignment detail pages carry the same shared-factory attachment panels as
orgs/people/jurisdictions:

- **Contacts** (email + phone) and **Links** on both — `roles_{contacts,links}.py` and
  `role_assignments_{contacts,links}.py`, thin wirings of `make_contacts_router` /
  `make_links_router` (`entity_type` `'role'` / `'role_assignment'`).
- **Identifiers** on assignments only (`role_assignments_identifiers.py`) — the role
  *definition* carries no identifiers (`entity_identifier_types` excludes `'role'`). The
  picker excludes internal types, so `role_wa_pdc` is the offered public type.
- Partials mirror the org set under `roles/partials/` and `role_assignments/partials/`
  (context id keys `role_id` / `ra_id`); detail handlers fetch the arrays into context. The
  `+ Add` buttons are gated on an active (non-archived) role/assignment; existing rows stay
  read-visible.
- **Role-level ancillary cleanup (CR).** A role's own `contact_methods`/`links`
  (`entity_type='role'`, no FK — the assignment-only `ancillary_migrate` #324 machinery does
  not cover these) must be cleaned like the assignment case, else the #326 editors orphan
  them. `src.core.ancillary_migrate` grew `rehome_role_ancillary` (merge: re-point + dedup
  onto the surviving role, emit a `'role'` 'updated' signal) and `delete_role_ancillary`
  (hard-delete: drop the rows). Wired into all three role-deleting paths: `roles.py`
  hard-delete, `orgs_roles.py::role_merge`, and both `orgs_merge.py` role-pair deletes.
- **Addresses deliberately excluded** — the address editor is hand-built per entity (not a
  shared factory) and semantically thin on a role/assignment; the public observation API
  still accepts them.
- **Known gap (#327):** these shared admin routers do raw INSERT/DELETE and do **not** emit
  a parent `entity_changes` 'updated' signal (the public observation path does). Combined
  with the no-touch-cascade tables (#324), admin edits are invisible to subscribers until
  #327 lands the consistent emit.

### HTMX live regions

All swap targets: `aria-live="polite" aria-atomic="false"`.

During requests, `aria-busy="true"` is automatically set on the swap target via global
`htmx:beforeRequest` / `htmx:afterSettle` listeners in `base.html`. No per-form work needed.

### Form labels

Every `<input>` (except `type="hidden"`), `<select>`, and `<textarea>` must have a
programmatic **accessible name**. A `placeholder` is **not** a label — it disappears on
input and many screen readers skip it (WCAG 2.1 AA SC 1.3.1 / 4.1.2). `<select>` can't
carry a placeholder at all, so it always needs an explicit name.

Three acceptable mechanisms:

1. **Visible `<label for>`** — preferred for full-page forms (`*/form.html`) where vertical
   space is free:
   ```html
   <label for="name">Canonical name</label>
   <input id="name" name="name" type="text">
   ```
2. **Wrapping `<label>`** — when the control sits inside its label:
   ```html
   <label>Visibility <select name="visibility">…</select></label>
   ```
3. **`aria-label`** — for the dense inline form-row / edit-row grids
   (`*_form_row.html`, `*_edit_row.html`, `_event_form_row.html`) where a visible `<label>`
   would break the layout. Mirror the placeholder's intent as a concise noun phrase; keep the
   `placeholder` for the visual hint:
   ```html
   <input type="text" name="event_place_text" aria-label="Place" placeholder="Place (optional)">
   <select name="event_type_id" aria-label="Event type">…</select>
   ```

Repeated controls across rows (e.g. a per-row merge checkbox) need a **disambiguating**
descriptor, same rule as row-action buttons (SC 2.4.6):

```html
<input type="checkbox" name="merge-select" aria-label="Select {{ role.title or '(untitled)' }} for merge">
```

Do **not** rely on `title` for the accessible name (see *`title` attributes* below). Enforced
at two tiers (#246): static template lint in `tests/api/admin/test_aria_labels.py` (fast,
pre-render, per-file heuristic) and the authoritative rendered-DOM sweep in
`tests/api/admin/test_a11y_render.py` (integration tier — fetches every admin GET route and
resolves label ancestry and id references against real output).

### Optional-field cue

Inline form rows signal "(optional)" only in the `placeholder`, which assistive tech reads
unreliably (same reason `placeholder` is not a label, above). Mark an optional inline field on
**both** channels:

- **Visible:** keep the `(optional)` suffix in the `placeholder`.
- **Assistive tech:** add `aria-describedby` pointing to a `.visually-hidden` hint element
  (defined in `admin.css`). Namespace the hint `id` with the row key so multiple open rows
  don't collide.

```html
<input type="text" name="event_place_text" aria-label="Place"
       aria-describedby="event-place-opt-{{ _le_key }}"
       placeholder="Place (optional)">
<span class="visually-hidden" id="event-place-opt-{{ _le_key }}">Optional</span>
```

When the parenthetical carries more than optionality (a format hint), put the full text in the
hint: `Optional — city, postal, or street precision`.

Fields with a visible `<label>` don't need this — the label already names the field accessibly;
append `(optional)` to the label text instead. Static linting:
`tests/api/admin/test_aria_labels.py::test_optional_placeholder_cue_has_describedby`.

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

Do **not** use the HTML `title` attribute — its tooltip is invisible to keyboard and
touch users and is announced inconsistently by screen readers, so it must never be the
sole carrier of information (table-cell expansions, badge state, button purpose).
Surface the text visibly, in a `.visually-hidden` span, or via `aria-label`:

```html
<!-- avoid: full name only reachable on mouse hover -->
<td title="{{ ident.type_full_name }}">{{ ident.type_name }}</td>
<!-- prefer: expansion exposed to assistive tech -->
<td>{{ ident.type_name }}<span class="visually-hidden"> — {{ ident.type_full_name }}</span></td>
```

Static linting enforced by `tests/api/admin/test_aria_labels.py::test_no_title_attribute`
(`data-*` attributes such as `data-title` are unaffected).

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

### Screen-reader testing

The static lints in `tests/api/admin/test_aria_labels.py` catch missing accessible names
structurally, but cannot verify the *announced experience*. Manually screen-read admin
changes that touch **forms, tables/rows, modals, badges, or live regions** before merging.

**Recommended combos** (cover at least one; use both platforms for high-traffic flows):

| Platform | Screen reader | Browser |
|---|---|---|
| macOS | VoiceOver (⌘F5) | Safari (primary), Chrome |
| Windows | NVDA (free) | Firefox (primary), Chrome |
| Linux | Orca | Firefox |

**Checklist:**

- **Forms** — every field announces a name + role; `(optional)` is spoken; hints
  (`aria-describedby`) are read.
- **Row actions** — repeated "Edit"/"Delete" buttons announce their entity-specific label,
  not a bare verb.
- **Status badges** — the state word ("Archived", "Inactive") is spoken; nothing conveys
  state by color or icon alone.
- **Table cells** — no information is mouse-hover-only (no `title`-only expansions).
- **Modals** — focus moves in on open, is trapped, and returns to the trigger on close
  (Esc + button); the modal exposes an accessible name.
- **Live regions** — HTMX swaps (flash messages, inline saves) are announced via the
  `aria-live="polite"` target without stealing focus.
- **Skip link** — the first Tab from page load reveals "Skip to main content" and jumps to
  `#main-content`.

**When:** before merging any admin-template change that adds or restructures the element
types above. Pure copy or style tweaks that don't change structure or semantics don't
require a manual SR pass.

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

### Row action-cell button order

In a table row's action cell, order buttons left → right by escalating effect: navigation / neutral (Open, View) → mutation (Edit) → destructive (Archive, Delete). This pairs with the destructive-last rule in §10.

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
- **Archived guard:** the Edit button in the read partial must be hidden when the entity is archived. Wrap it in `{% if not entity.archived_at %}…{% endif %}`. This applies to all inline edit buttons in all read partials — see §26 for the general rule.

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

### Adding live header sync to a new entity type

Follow these steps whenever a new entity detail page needs its `<h1>`, breadcrumb, and `document.title` to update live after an inline name edit. See §30 for the full pattern spec.

1. **JS file** — create `src/static/admin/{entity}-detail.js` listening for `update{Entity}Header` (camelCase, e.g. `updatePersonHeader`).
2. **`deps.py`** — add `{entity}_header_extra(entity_id, db)`: query the display-name view, fall back to `entity_id`, return `{"update{Entity}Header": {"display": display}}`.
3. **Mutation routes** — on every route that can change the canonical name, pass `extra=await {entity}_header_extra(entity_id, db)` to `flash_trigger()`.
4. **`base.html`** — load the JS in `base.html`'s `<head>` with `defer` (NOT the detail template's `extra_head`, which hx-boost strips on boosted navigation — see "hx-boost re-execution"). The listener is global and idempotent, so loading it site-wide is safe.
5. **Tests** — add 5 structural tests in `test_js.py` (file exists, event key, `page-heading`, `breadcrumb-current`, `document.title`). See §30 for the checklist.

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
- **`hx-swap="innerHTML"`** must be explicit when the input is inside a `<form>` with a different `hx-swap` — see §7 HTMX attribute inheritance. If the typeahead input is not inside a `<form>` element (e.g. it sits directly in a `<tr>` with standalone `hx-post` buttons), HTMX defaults to `innerHTML` and no override is needed.

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

### JavaScript — shared factory

All typeahead comboboxes use the shared factory loaded from `base.html`:

```javascript
window.initTypeaheadCombobox({
  inputId:   'my-search',
  listboxId: 'my-results',
  hiddenId:  'my-id-hidden',
});
```

The factory is defined in `src/static/admin/typeahead-combobox.js` and loaded with `defer` in `<head>`, so it is available when any inline `<script>` in `<body>` runs. If a form partial also needs extra logic (e.g. disabling a date field when a checkbox is checked), put it in a separate IIFE after the factory call — do not mix it into the combobox wiring.

### JavaScript contract

The factory implements:

| Behaviour | Detail |
|---|---|
| **Open dropdown** | `htmx:afterSwap` on the `<ul>` — position via `getBoundingClientRect`, set `aria-expanded="true"` |
| **Close dropdown** | Outside click, scroll (capture phase), Escape key, or item selection — set `aria-expanded="false"`, clear `ul` |
| **Arrow navigation** | `ArrowDown` / `ArrowUp` — cycle `.is-active` class on `<li>` items, scroll into view, set `aria-activedescendant` |
| **Enter to select** | Copy `data-id` → hidden input, `data-label` → visible input, close dropdown |
| **Escape** | Close dropdown without selection |
| **Mouse select** | `ul.addEventListener('mousedown')` + `e.preventDefault()` — delegate to `closest('[data-id]')`. `mousedown` is used instead of `click` because mousedown fires first; without `preventDefault`, the input loses focus before `click` fires and the event is swallowed or re-routed in some browsers. |
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

### Jinja2 include variable scoping

`{% include %}` shares the caller's full template context, but the partial uses variables by name. If a partial references `person_id` while the outer template's context only contains `person`, set the variable explicitly immediately before each `{% include %}`:

```html
{% set person_id = person.id %}
{% include "admin/people/partials/_name_row.html" %}
```

Do this for every `{% include %}` of that partial in the same template — Jinja2 `{% set %}` in a `{% for %}` loop does not leak out of the loop body.

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

A fixed-position page overlay for bulk-select-and-merge on tables with potentially duplicate rows. Used on the Roles table of the org detail page (`role-merge.js`), the People list (`people-merge.js`), the Organizations list (`orgs-merge.js`), and the Roles list (`roles-merge.js`).

> **Shared engine (#250):** the three **list** flows (People, Orgs, Roles) are thin consumers of `merge-mode.js`, which exposes `window.createMergeMode(config)` — one boost-safe, document-delegated implementation parameterized by `{ tableId, btnId, btnWrapId, barId, listRegionId, rowAttr, nounPlural, buildPreviewUrl, untitledLabel }` plus the optional `{ previewTarget, groupAttr, canMerge, cannotMergeLabel }` (#251/#255) for group-scoped merges and portal override. `people-merge.js` / `orgs-merge.js` / `roles-merge.js` only supply config. `role-merge.js` (org-detail roles table) predates the factory and keeps its own `init()`-per-table lifecycle (#237). Load order in `base.html` matters: `merge-mode.js` must precede its consumers (`defer` preserves document order).

> **Preview modal (#255):** the Keep buttons no longer POST the merge directly with a bare `hx-confirm`. Each supplies `buildPreviewUrl(winner, loser, winnerEntry, loserEntry)` and the factory wires the Keep buttons to **`hx-get` the entity's `merge-preview` modal into `#merge-modal-portal`** (the shared portal in `base.html`); the modal *is* the confirm step. The modal form drives the actual merge POST and, on success in the list context, swaps the list region back in and closes the portal (shared `admin/_merge_modal_script.html`). Two shapes: **Orgs & People** post to a curated `.../merge-with/...` with per-name keep/drop checkboxes (loser's canonical name/acronym default **checked** = keep, #255 — this also applies to the detail/duplicates modals, which share the template) and `return_to=list`; **Roles** have no name selection, so their modal posts to the existing `.../merge/...` route. The lossy `keep_name_ids=None` bulk branch in `_execute_merge` was also made non-lossy (demote+transfer) as defense-in-depth. **#323:** the People modal annotates each `reading`/`romanization`/`mrz` row with a `(reading of "‹parent›")` note (`reading_of_name`, LEFT JOIN parent — same enrichment as the name-management read-row), and the curated drop keeps the parent of any *kept* child even when the parent is unchecked, so an explicitly-kept reading can't cascade away via `reading_of_id ON DELETE CASCADE`.

> **Same-org predicate (#251):** role merge is org-scoped (route `/admin/orgs/{org}/roles/{winner}/merge/{loser}/`, unique per `(organization_id, lower(title))`), but the Roles **list** is cross-org. So `roles-merge.js` supplies `groupAttr: 'orgId'` (the factory captures each row's `data-org-id` into the selection entry's `.group`) and `canMerge: (a, b) => a.group === b.group`. When two selected roles span different orgs the factory shows `cannotMergeLabel` ("Roles must be in the same organization to merge") and leaves both Keep buttons disabled — no preview `hx-get` is wired, so a doomed cross-org pair can't even open the modal. `buildPreviewUrl` reads the shared org from the winner entry's `.group`. People / Orgs omit these keys and are unaffected (always-mergeable).

> **Positioning:** `.merge-bar` is `position: fixed; bottom: 3rem; left: var(--sidebar-w); right: 0` — a full-width overlay above the sticky pagination, not a block contained by its parent element. It is placed inside `table-wrapper` in the DOM for logical proximity only; the containing block has no effect on layout.

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

  <!-- Merge action bar — hidden until merge mode; fixed-position overlay, placed here for DOM proximity -->
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

> This table describes `role-merge.js` (org-detail table), which still wires `hx-post` + `hx-confirm` directly. The **list** flows differ since #255: at two selections the factory wires `hx-get` to open the preview modal instead (see the "Preview modal (#255)" note above) — no `hx-post`/`hx-confirm` on the Keep buttons.

### Exit conditions

Merge mode exits automatically after a successful merge (JS listens for the `showFlash` event dispatched by the flash system after the server responds).

### Client-side roles filter

The same `role-merge.js` also handles the roles filter input (`#roles-filter`). It filters `tr[data-title]` rows client-side by comparing `data-title.toLowerCase()` against the input value — no server round-trip.

```html
<input type="search" id="roles-filter" placeholder="Filter roles…"
       class="filter-card__search">
```

### List variants (`people-merge.js`, `orgs-merge.js`, `roles-merge.js`)

Both consume the shared `merge-mode.js` engine; the table below contrasts the list lifecycle against the org-detail roles table. Same DOM contract, five deltas:

| Delta | Roles (org detail) | People list |
|---|---|---|
| Table id / data-attrs | `#roles-table[data-org-id]`, rows carry `data-role-id` | `#people-table` (no `data-org-id`), rows carry `data-person-id` |
| Swap target | `#roles-table tbody` (rows only) | `#people-list-region` (entire region: table, caption count, sticky pagination) — keeps post-merge totals consistent |
| Sticky pagination | None on org detail roles table | `.pagination--sticky` present; JS hides it on `enterMergeMode`, restores on `exitMergeMode` / `showFlash` — single sticky slot, no overlap |
| Filter input | Inline client-side `#roles-filter` | None; the list uses server-side search via the filter card |
| Region swap survival | Roles table never swapped wholesale | Filter card swaps `#people-list-region` on every search / status / page-size change. people-merge.js uses lazy element resolution, document-level event delegation, and re-applies merge-mode visual state on `htmx:afterSwap` so the UI keeps working through any swap |
| Boost survival (#249) | `role-merge.js` re-runs an idempotent `init()` on `htmx:load`, guarded by `table.dataset.mergeBound` | Loaded **site-wide from `base.html`** (was wrongly in the People list's `extra_head`, which hx-boost strips — Merge was a silent no-op). The toggle click is document-delegated (button element is replaced on each boosted nav); a `#people-list-region` partial swap **preserves** merge mode while a boosted full-page arrival (detected via `htmx:load`, same signal as role-merge.js — its loaded subtree carries the page-header merge button, a region swap's does not) **resets** to a clean state (no stale mode/selection) |

Since #255 the Keep button opens the preview modal (`buildPreviewUrl` → `GET /admin/people/{winner_id}/merge-preview/{loser_id}/?ctx=list`); the modal then POSTs the curated merge to `/admin/people/{winner_id}/merge-with/{loser_id}/` (with `keep_name_ids` + `return_to=list`). Both that route and the bulk `person_merge` (`/merge/`) share the list-region re-render: the route ([src/api/admin/people_merge.py](../src/api/admin/people_merge.py)) detects the list flow via `HX-Target == "people-list-region"` and returns `_region.html` instead of `_duplicates_region.html`. Filter state (`q`, `status`, `page`, `page_size`) is parsed from `HX-Current-URL` so the refreshed region respects the user's active filters; the shared query helper lives in [src/api/admin/people_queries.py](../src/api/admin/people_queries.py) and is used by both the list route and the merge routes' list-flow branch.

Merge button always renders (the People list mixes active + archived via the status filter); the `_btn-wrap` shows the `not-allowed` cursor + tooltip when fewer than 2 rows are visible in the current tbody. Selection state clears on `htmx:afterSwap` (search, pagination, page-size change) — cross-page selection persistence is intentionally not implemented.

The **Orgs list** (`orgs-merge.js`) mirrors the People list exactly, with rows carrying `data-org-id`; since #255 the Keep button opens `GET /admin/orgs/{winner}/merge-preview/{loser}/?winner={winner}&ctx=list` and the modal POSTs to `org_merge_with` (`/merge-with/`, curated names/acronyms + `return_to=list`). The route ([src/api/admin/orgs_merge.py](../src/api/admin/orgs_merge.py)) detects the list flow via `HX-Target == "orgs-list-region"` and returns `_region.html` instead of `_duplicates_region.html` (shared `_render_orgs_list_region` helper, used by both `org_merge` and `org_merge_with`); the shared query helper is [src/api/admin/orgs_queries.py](../src/api/admin/orgs_queries.py). The `HX-Current-URL` filter parsing is shared with People via [src/api/admin/list_filters.py](../src/api/admin/list_filters.py) (`parse_list_filters`); each route binds its own valid-status set. One difference from People: the org status axis is three-valued (`active` / `inactive` / `archived`), so the Orgs caller passes `inactive` as a valid status — copy-pasting the People set would collapse it to `active`. Page-size bounds live in [pagination.py](../src/api/admin/pagination.py) (`PAGE_SIZE_*`), used by both the route `Query` validators and the parser. A list-flow merge that drops conflicting role assignments appends the count to the flash (`_dropped_assignments_note`, shared with the detail-flow `org_merge_with`).

The **Roles list** (`roles-merge.js`, #251) is the cross-org variant, and differs from People/Orgs in three ways:

- **Namespaced IDs.** Both the org-detail roles table and the roles list would otherwise use `#roles-table` / `#roles-merge-*`. The list uses `#roles-list-table` / `#roles-list-merge-btn` / `#roles-list-merge-bar` / `#roles-list-merge-btn-wrap` so `role-merge.js` (which binds `#roles-table`) and `roles-merge.js` never double-bind. Rows carry `data-role-id` (row identity / count via `rowAttr`) **and** `data-org-id` (the same-org key via `groupAttr`).
- **Same-org predicate.** See the "Same-org predicate (#251)" note in §24 — `canMerge` gates the 2-selection enable point; cross-org pairs show the hint and stay disabled. Since #255 the Keep button opens `GET /admin/orgs/{org}/roles/{winner}/merge-preview/{loser}/?ctx=list` (org-scoped, built from the shared `entry.group`); the confirmation-style modal then POSTs to the org-scoped merge route below.
- **Backend reuses the org-detail merge route.** The merge POST needs no curated endpoint (roles have no name/acronym selection), so the modal posts to the existing `role_merge` (`/admin/orgs/{org}/roles/{winner}/merge/{loser}/`) in [src/api/admin/orgs_roles.py](../src/api/admin/orgs_roles.py), which gains an `HX-Target == "roles-list-region"` branch returning `admin/roles/_region.html` (filters re-derived from `HX-Current-URL`); without that header it keeps returning the org-detail `_role_rows.html` partial. A read-only `role_merge_preview` GET route (#255) renders the modal. The shared query helper is [src/api/admin/roles_queries.py](../src/api/admin/roles_queries.py); filter parsing reuses `parse_list_filters` with `extra_text_params=("org_q",)` for the roles-only organization-name filter (status axis is two-valued — roles have no `active` flag).

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

---

## 26. Generic Single-Field Inline Edit Pattern

For any short text field that lives on a detail page and needs inline editing (e.g. pronouns, display label, short description), follow this pattern. The Notes variant (§15) is a special case of this general form.

### HTML structure

**Read partial** (`_{field}_read.html`):

```html
<div id="{field}-field" style="margin-top:var(--space-5)">
  <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:var(--space-3)">
    <h3 class="field-group-label">{Label}</h3>
    {% if not entity.archived_at %}
    <button type="button" class="btn btn--sm btn--secondary"
            hx-get="/admin/{entities}/{{ entity.id }}/inline/{field}/edit/"
            hx-target="#{field}-field"
            hx-swap="outerHTML">Edit</button>
    {% endif %}
  </div>
  <div style="font-size:var(--font-size-sm);color:{% if entity.{field} %}var(--color-text){% else %}var(--color-text-muted){% endif %}">
    {{ entity.{field} or '—' }}
  </div>
</div>
```

**Edit partial** (`_{field}_form.html`):

```html
<div id="{field}-field" style="margin-top:var(--space-5)">
  <form hx-post="/admin/{entities}/{{ entity.id }}/inline/{field}/"
        hx-target="#{field}-field"
        hx-swap="outerHTML">
    <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:var(--space-3)">
      <label for="{field}-input" class="field-group-label">{Label}</label>
      <div>
        <button type="submit" class="btn btn--primary btn--sm">Save</button>
        <button type="button" class="btn btn--secondary btn--sm"
                hx-get="/admin/{entities}/{{ entity.id }}/inline/{field}/"
                hx-target="#{field}-field"
                hx-swap="outerHTML">Cancel</button>
      </div>
    </div>
    <div class="form-group" style="margin-bottom:0">
      <input id="{field}-input" type="text" name="{field}"
             value="{{ entity.{field} or '' }}"
             placeholder="…">
    </div>
  </form>
</div>
```

### Rules

- `id="{field}-field"` on the outer `<div>` is the shared HTMX target for read and edit partials.
- Read partial uses `<h3 class="field-group-label">`. Edit partial replaces it with `<label for="{field}-input" class="field-group-label">` for proper screen-reader association — no `<h3>` in the edit state.
- Save and Cancel sit in the header row (same `display:flex` row as the label), not in a `form-actions` div. This prevents layout shift on toggle.
- **Archived guard:** the Edit button is wrapped in `{% if not entity.archived_at %}`. Read partials returned by route handlers (not via the full detail page) must still receive `entity` in context with `archived_at` populated.
- Empty/whitespace values saved as `NULL` (`.strip() or None`).
- Non-HTMX fallback: `POST /inline/{field}/` returns `RedirectResponse` to the detail page.

### Routes

```
GET  /admin/{entities}/{id}/inline/{field}/       → read partial
GET  /admin/{entities}/{id}/inline/{field}/edit/  → edit partial
POST /admin/{entities}/{id}/inline/{field}/       → save → read partial
```

---

## 27. Re-sort Response: `_rows.html` vs. `_row.html`

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

Even the create route uses `_rows.html` (not the new single row) when the new row must be inserted in sorted position rather than prepended. Contrast with §21 (section-level add button) where `hx-swap="afterbegin"` prepends the blank form row — create POST then replaces the full tbody to insert the saved row in its sorted position.

---

## 28. Last-Identity Guard: HTMX Response for Blocked Deletes

When a delete is blocked because it would leave the entity with no identity (e.g. deleting the only name), the server cannot return a 4xx — **HTMX ignores non-2xx responses by default** and swaps nothing, showing no feedback.

### Correct pattern

```python
# HTMX path — return 200 with empty body + flash error; row stays in DOM
if is_htmx(request):
    return HTMLResponse(
        content="",
        status_code=200,
        headers=flash_trigger("error", "Cannot remove the only name."),
    )
# Non-HTMX path — browser receives a proper error response
raise HTTPException(status_code=409, detail="Cannot remove the only name.")
```

- Empty `content=""` means the HTMX swap target is unchanged — the row stays in the DOM.
- The flash delivers the error message via `HX-Trigger: {"showFlash": {...}}` (§8).
- The `is_htmx()` check must come before the guard logic so the non-HTMX path raises a meaningful HTTP error for API callers.

### When this applies

Any route that enforces a minimum-count or canonical invariant:
- Last name on a person or org (delete)
- Last acronym on an org when no canonical name exists (delete)
- Edit route that would leave zero canonical names (see §28a below)

### Contrast with archive gate

The archive gate (§10) blocks hard delete when `archived_at IS NULL` — that can safely return 409 unconditionally because the delete button is served via HTMX but the error surface is the `delete_modal.html` which reads the response status explicitly.

---

## 28a. Canonical Invariant Guard on Edit Routes

Name edit routes (`POST /{name_id}/edit-row/`) must never allow the entity to end up with zero canonical names. The `_maybe_promote_sole_name` helper only fires when exactly one name exists; it does not cover the multi-name case where the user unchecks canonical on the only canonical row.

### Guard pattern

Check **before** the transaction whether the edit would leave zero canonical names:

```python
if is_canonical != "true" and existing["is_canonical"]:
    other_canonical = await db.fetchval(
        "SELECT id FROM person_names"
        " WHERE person_id=$1 AND is_canonical=TRUE AND id != $2",
        person_id, name_id,
    )
    if not other_canonical:
        if not is_htmx(request):
            return RedirectResponse(f"/admin/people/{person_id}/", status_code=303)
        return HTMLResponse(
            content="",
            status_code=200,
            headers=flash_trigger(
                "error",
                "Cannot remove canonical. Promote another name first.",
            ),
        )
```

- The guard fires only when the row being edited **is currently canonical** and **is_canonical is not being re-asserted**.
- HTMX path: HTTP 200, empty body, flash error (same pattern as §28).
- Non-HTMX path: `RedirectResponse` 303 (not 409 — there is no modal to surface an error, so redirect is the least-surprising degradation).
- The guard does **not** fire when editing a non-canonical row (no invariant at risk).
- The correct workflow for changing which name is canonical: promote the replacement (check its canonical toggle), which atomically demotes the current canonical via the existing `is_canonical == "true"` branch.

---

## 29. Address Confirm Flow

When an address form is submitted, the server normalizes the input and — if normalization produces a meaningful result — shows a confirm modal before persisting. This uses `HX-Retarget` (§7) to inject the modal without touching the form row.

### Two-mode POST

The address create and edit routes accept a `mode` form field:

| `mode` value | Behaviour |
|---|---|
| `confirm` (default) | Normalize input; if result differs, return the confirm modal via `HX-Retarget` |
| `save` | Skip normalization; persist immediately and return the updated row |

The form row's `hx-post` always omits `mode` (defaults to `confirm`). The confirm modal's action buttons submit `mode=save`.

### Portal div

Place an empty `<div id="address-confirm-portal"></div>` in the detail template **after** the last section and **before** the metadata footer. The server uses `HX-Retarget: #address-confirm-portal` to inject the modal here without touching the form row.

```html
<!-- detail.html — after last <section>, before metadata <p> -->
<div id="address-confirm-portal"></div>
```

### Server response when confirm needed

```python
return templates.TemplateResponse(
    request,
    "admin/{entity}/partials/_address_confirm_modal.html",
    {
        "entity_id": entity_id,
        "addr_id": addr_id,          # None for create, str for edit
        "original": original_ctx,    # dict of submitted field values
        "normalized": normalized_ctx, # dict from normalizer result
        "validation_status": ...,
        "validation_provider": ...,
    },
    headers={"HX-Retarget": "#address-confirm-portal", "HX-Reswap": "innerHTML"},
)
```

### Confirm modal structure

The modal contains two action forms that both POST to the same create/edit endpoint with `mode=save`:
- **Keep my input** — hidden inputs carry the original field values
- **Accept** — hidden inputs carry the normalized field values

Both forms include `hx-on::after-request="if (event.detail.successful) window.__pmAddrConfirmClose()"` to close the modal on success.

The modal JS (inline in the partial) handles: Escape to close, Tab/Shift-Tab focus trap, `window.__pmAddrConfirmClose()` for programmatic close, focus restoration to the triggering element.

### When normalization is skipped

If the normalizer returns no result (service unavailable, address unparseable), `_maybe_confirm()` returns `None` and the route falls through to the save path directly — no modal, no `HX-Retarget`.

---

## 30. Per-Entity Live Header Sync

Full reference for the live header sync pattern introduced in §17. Follow this checklist when adding it to a new entity type.

### Checklist

- [ ] `src/static/admin/{entity}-detail.js` — event listener
- [ ] `{entity}_header_extra()` in `src/api/admin/deps.py`
- [ ] `extra=` argument on all name-mutation routes
- [ ] `<script src defer>` in `base.html`'s `<head>` (NOT the detail template's `extra_head` — hx-boost strips it)
- [ ] 5 structural tests in `tests/api/admin/test_js.py`

### JS file (`src/static/admin/{entity}-detail.js`)

```javascript
document.addEventListener('update{Entity}Header', function (e) {
  var display = e.detail && e.detail.display ? e.detail.display : '';
  var h1 = document.getElementById('page-heading');
  var crumb = document.getElementById('breadcrumb-current');
  if (h1) h1.textContent = display;
  if (crumb) crumb.textContent = display;
  if (display) document.title = display + ' \u2014 {Entity type label}';
});
```

- Event name is `update{Entity}Header` — camelCase, e.g. `updatePersonHeader`.
- `\u2014` is an em dash — never a hyphen.

### `deps.py` helper

```python
async def {entity}_header_extra({entity}_id: str, db) -> dict:
    """Return extra dict for flash_trigger with the current {entity} display name."""
    row = await db.fetchrow(
        "SELECT display_name FROM v_{entity}_display_names WHERE {entity}_id=$1",
        {entity}_id,
    )
    display = row["display_name"] if row and row["display_name"] else {entity}_id
    return {"update{Entity}Header": {"display": display}}
```

### Mutation route usage

```python
headers=flash_trigger(
    "success",
    f"Name <strong>{escape(name)}</strong> saved.",
    extra=await {entity}_header_extra({entity}_id, db),
)
```

Pass `extra=` on every route that creates, edits, or deletes a name row — including deletes (the display name may change after a deletion removes the canonical).

### Script loading (`base.html`)

Load the listener site-wide from `base.html`'s `<head>` — never a detail template's `extra_head`, which hx-boost strips on boosted navigation (#237):

```html
<script src="/static/admin/{entity}-detail.js?v={{ asset_version }}" defer></script>
```

`?v={{ asset_version }}` is the commit-hash cache-bust injected at startup; no manual increment needed.

### Structural tests (`test_js.py`)

Add a block after the analogous org-detail block:

```python
_ENTITY_DETAIL_JS_PATH = Path("src/static/admin/{entity}-detail.js")
ENTITY_DETAIL_JS = _ENTITY_DETAIL_JS_PATH.read_text() if _ENTITY_DETAIL_JS_PATH.exists() else ""

def test_{entity}_detail_js_exists():
    assert _ENTITY_DETAIL_JS_PATH.exists()

def test_{entity}_detail_js_listens_for_update_{entity}_header():
    """Listener must be keyed to update{Entity}Header — any other name breaks sync silently."""
    assert "update{Entity}Header" in ENTITY_DETAIL_JS

def test_{entity}_detail_js_targets_page_heading():
    """Must target id='page-heading' on the <h1> — changing the ID breaks live sync."""
    assert "page-heading" in ENTITY_DETAIL_JS

def test_{entity}_detail_js_targets_breadcrumb_current():
    """Must target id='breadcrumb-current' on the breadcrumb span."""
    assert "breadcrumb-current" in ENTITY_DETAIL_JS

def test_{entity}_detail_js_updates_document_title():
    """Must update document.title — tab title sync is the third live-update target."""
    assert "document.title" in ENTITY_DETAIL_JS
```

---

## 31. Paired Date Control Pattern

For any section that displays two related date fields side-by-side on a detail page (e.g. boundary dates, service dates), follow this pattern. Both read and edit partials share the same outer `id` as their HTMX swap target.

### Section label naming

Use a descriptive two-word label that clarifies the *semantic role* of the date pair:

| Context | Section label |
|---|---|
| Role boundary dates (established / abolished) | **Boundary Dates** |
| Role assignment service dates (start / end) | **Service Dates** |

Avoid the bare label "Dates" — it gives no context.

### Read partial (`_dates_read.html`)

```html
<div id="dates-field" style="margin-top:var(--space-5)">
  <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:var(--space-3)">
    <h3 class="field-group-label">{Section Label}</h3>
    {% if not entity.archived_at %}
    <button type="button" class="btn btn--sm btn--secondary"
            hx-get="/admin/{entities}/{{ entity.id }}/inline/dates/edit/"
            hx-target="#dates-field"
            hx-swap="outerHTML">Edit</button>
    {% endif %}
  </div>
  <div style="display:flex;gap:var(--space-6);font-size:var(--font-size-sm)">
    <div>
      <div class="field-group-label" style="font-size:var(--font-size-xs)">{Field A label}</div>
      <div style="color:{% if entity.{field_a} %}var(--color-text){% else %}var(--color-text-muted){% endif %}">
        {{ entity.{field_a} or '—' }}
      </div>
    </div>
    <div>
      <div class="field-group-label" style="font-size:var(--font-size-xs)">{Field B label}</div>
      <div style="color:{% if entity.{field_b} %}var(--color-text){% else %}var(--color-text-muted){% endif %}">
        {{ entity.{field_b} or '—' }}
      </div>
    </div>
  </div>
</div>
```

- Each field renders its sub-label (e.g. "Established", "Start") as a `<div class="field-group-label">` — all-caps via CSS, xs size via inline override.
- Null values display as `—` in muted text color. Non-null values use `var(--color-text)`.
- Do **not** lump both values into a single bordered box (`start — end`) — that conflates two distinct fields and makes null states ambiguous.

### Edit partial (`_dates_form.html`)

```html
<div id="dates-field" style="margin-top:var(--space-5)">
  <form hx-post="/admin/{entities}/{{ entity.id }}/inline/dates/"
        hx-target="#dates-field"
        hx-swap="outerHTML">
    <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:var(--space-3)">
      <h3 class="field-group-label">{Section Label}</h3>
      <div>
        <button type="submit" class="btn btn--primary btn--sm">Save</button>
        <button type="button" class="btn btn--secondary btn--sm"
                hx-get="/admin/{entities}/{{ entity.id }}/inline/dates/"
                hx-target="#dates-field"
                hx-swap="outerHTML">Cancel</button>
      </div>
    </div>
    {% if error %}
    <div class="alert alert--error" role="alert" style="margin-bottom:var(--space-3)">{{ error }}</div>
    {% endif %}
    <div style="display:flex;gap:var(--space-4)">
      <div class="form-group" style="margin-bottom:0;flex:1">
        <label for="{field-a}-input" class="field-group-label" style="font-size:var(--font-size-xs)">{Field A label}</label>
        <input type="date" id="{field-a}-input" name="{field_a}" value="{{ {field_a}_input or '' }}">
      </div>
      <div class="form-group" style="margin-bottom:0;flex:1">
        <label for="{field-b}-input" class="field-group-label" style="font-size:var(--font-size-xs)">{Field B label}</label>
        <input type="date" id="{field-b}-input" name="{field_b}" value="{{ {field_b}_input or '' }}">
      </div>
    </div>
  </form>
</div>
```

### Rules

- **Section label:** `<h3 class="field-group-label">` in both read and edit states — no layout shift on toggle.
- **Field sub-labels in edit:** `<label class="field-group-label" style="font-size:var(--font-size-xs)">` — all-caps from the class, xs size from the inline override. Associates correctly with the input for screen readers.
- **`flex:1` on both form-groups** — inputs share available width equally; no hard widths.
- **No `flex-wrap`** — date inputs are compact enough to sit side-by-side at all supported viewport widths.
- **Error alert** between the header row and the inputs — same position used by the contact form inline error pattern (§ DB conventions).
- **Non-HTMX fallback:** POST returns `RedirectResponse` to the detail page.
- **Archived guard:** Edit button hidden when `entity.archived_at` is non-null.

### Routes

```
GET  /admin/{entities}/{id}/inline/dates/       → read partial
GET  /admin/{entities}/{id}/inline/dates/edit/  → edit partial
POST /admin/{entities}/{id}/inline/dates/       → save → read partial (or re-render form on error)
```

### Inline row variant — date-range labeling

When the date pair sits inside a **repeatable inline form row** (a flex action row in a
table, not a standalone detail-page section), the stacked labels above don't fit. Use a
visible prefix label + presentational separator instead. Reference:
`admin/orgs/partials/_address_form_row.html` (validity window).

```html
<label for="valid-from-{% if a and a.id %}{{ a.id }}{% else %}new{% endif %}"
       style="font-size:var(--font-size-sm);color:var(--color-text-muted);white-space:nowrap">Valid from</label>
<div class="form-group" style="margin-bottom:0">
  <input type="date" name="valid_from"
         id="valid-from-{% if a and a.id %}{{ a.id }}{% else %}new{% endif %}"
         value="…">
</div>
<span aria-hidden="true"
      style="font-size:var(--font-size-sm);color:var(--color-text-muted)">to</span>
<div class="form-group" style="margin-bottom:0">
  <input type="date" name="valid_until" aria-label="Valid until" value="…">
</div>
```

**Rules:**

- **First input:** visible `<label for>` is its accessible name — do **not** also set
  `aria-label` (conflicting double name). Label sits *outside* the `.form-group` (the
  `.form-group label` CSS rule forces `display:block`, which breaks the inline flex).
- **Label/input ids are row-scoped** (`{field}-{{ a.id }}` / `{field}-new`) — same suffix
  convention as `address-structured-fields-*`; multiple rows in edit mode must not
  produce duplicate ids.
- **Separator** (`to`, `—`) is `aria-hidden="true"` — purely presentational.
- **Second input:** keeps `aria-label` for its accessible name; its only visible
  "label" is the separator, which is not an accessible name. Screen readers announce
  the pair as "Valid from" / "Valid until", never a dangling "to".

Applied to the header-less flex form rows: `_address_form_row.html` (validity),
`people/_assignment_form_row.html` + `roles/_assignment_form_row.html` (start/end),
and `orgs/_name_form_row.html` (effective dates) — #259.

**Not** for date pairs in **table cells** under `<th>` column headers (the
assignment *edit* rows, `_assignment_edit_row.html`): the column header already
supplies visible context, so the input keeps a plain `aria-label` and no in-cell
label is added (it would duplicate the header). This variant is for flex rows where
the dates float without a header.

---

## 32. Admin Server Conventions

### Auth

exe.dev proxy injects `X-ExeDev-UserID` + `X-ExeDev-Email` headers. Missing headers → redirect to `/__exe.dev/login?redirect=<url-encoded path+query>`.

Every route handler: `user: AdminUser = Depends(get_admin_user)` — `get_admin_user` (from `src.api.admin.deps`) raises `HTTPException(307)` with `Location` header; FastAPI propagates the redirect automatically.

### Archive model

`archived_at TIMESTAMPTZ` — NULL = active, non-NULL = archived.

- Hard delete: gated on `archived_at IS NOT NULL` (returns 409 if not archived)
- `POST /{id}/unarchive/`: sets `archived_at = NULL`, preserves prior `active` state (returns 409 if not archived)
- Archive: returns 409 if already archived — enforced across all entity types (orgs, people, roles, role-assignments)
- Flash on detail pages: `org_detail`, `person_detail`, `ra_detail` accept `?flash=` param via `resolve_query_flash`; add new flash keys to the module-level `_FLASH_MESSAGES` dict

**Danger Zone interaction model (#281).** All three Danger Zone actions on entity-detail pages (orgs, people, jurisdictions) share one HTMX model. Archive / unarchive / delete are `hx-post` / `hx-delete` buttons (no `<form method="POST">`), and each route branches on `is_htmx(request)`:

- HTMX → `Response(status_code=204, headers={"HX-Location": target})` — client-side full navigation that re-renders the detail (or list, for delete) with `?flash=…`
- non-HTMX → `RedirectResponse(target, status_code=303)` — same target. **Note:** the controls are bare `hx-post`/`hx-delete` buttons (no `<form>`), so they require JS; this 303 branch serves direct/non-HTMX POST clients (API, tests), **not** JS-disabled browsers, where the buttons are inert. Whether no-JS browser support is an attainable admin-wide goal is tracked in #287.

`target` for archive/unarchive is the detail page (`/admin/{entities}/{id}/?flash=archived|unarchived`); for delete it is the list page (`?flash=deleted`). 409-on-already-in-state guards fire before the branch, so they hold for both request kinds. The org "Restore from archive" control in `orgs/partials/_active_toggle.html` follows the same `hx-post` model.

### List status filters & search discoverability (#306)

**Default status filters never silently hide search matches.** Every admin list (orgs / people / roles / role-assignments / jurisdictions) filters by a status axis defaulting to `active`; a name search under that default used to drop same-named rows sitting under another status — the dedup-hunting trap of #306 (two "WA House RSG" orgs, one `active=FALSE`, only one visible).

The pattern, shared across all five lists:

- Each `*_queries.py` declares its axis as `STATUS_PREDICATES` (status → SQL predicate, in dropdown order) and `VALID_STATUSES = set(STATUS_PREDICATES) | {"all"}`. `all` is a first-class validated status (fourth dropdown option, no predicate); an **unknown status falls back to `active`** — never to no-filter (pre-#306, `?status=banana` silently returned everything including archived).
- When a search is active (`q`; for roles also `org_q`), the count query is one grouped pass via `count_with_hidden_matches()` from `src.api.admin.list_status` — `count(*) FILTER (WHERE <predicate>)` per status — and the query helper returns `hidden_matches`: `{"status", "count"}` per non-current status holding matches. Extra list filters that aren't the status axis (jurisdictions' `type`) constrain those counts like any search condition.
- `query_*_rows` returns `(rows, count, pctx, hidden_matches)`; list routes and list-flow merge branches put `hidden_matches` in the template context.
- `_region.html` renders `admin/_hidden_matches.html` above the table: "N more matches outside the current status filter (…) — Show all". **The Show all link is a plain `<a>`, not `hx-get`** — the status dropdown lives in `list.html` outside the swap region, so only a full-page render keeps it in sync with `status=all`.
- Merge-flow `_VALID_STATUSES` duplicates are gone: `orgs_merge.py` / `people_merge.py` / `orgs_roles.py` import `VALID_STATUSES` from their `*_queries.py` so route, merge filter parsing, and query can't drift.

Deliberate exception: the public API `/search` endpoints (orgs, people) filter archived rows behind an explicit, documented `include_archived=false` query param — opt-in, not silent — and stay as they are (see `docs/CONVENTIONS.md`).

### HTMX partial responses

`is_htmx(request)` from `src.api.admin.deps` — checks `HX-Request and not HX-Boosted`. Boost sends both headers; omitting the `not HX-Boosted` guard causes boosted sidebar nav to receive bare fragments instead of full page layouts.

Always include a `RedirectResponse` fallback on mutation routes for graceful degradation without JS.

### hx-boost re-execution

`hx-boost="true"` on `admin-layout` makes navigation a boosted swap: htmx fetches the full page, then **discards the response `<head>` entirely** (its fragment parser strips `<head>…</head>`) and swaps only the `<body>` plus `<title>`. Two consequences:

- **Body `<script src>` tags re-run on every boosted navigation.** A persistent `document.addEventListener` in `<body>` accumulates duplicate listeners. For unavoidable inline body scripts use the replace-then-add idiom: `document.removeEventListener(evt, document.__pmKey); document.__pmKey = fn; document.addEventListener(evt, document.__pmKey)` — see `base.html` `aria-busy` and `__pmNavKeydown`.
- **Head `<script>` tags in a _detail template's_ `extra_head` never run when the page is reached via a boosted link** — they are stripped with the rest of `<head>`. They execute only on a full (non-boosted) page load.

So any script that must run on (or register listeners for) a detail page reached by clicking an in-app link belongs in **`base.html`'s `<head>`**, which loads once on the first full page load and persists across every boosted swap. Scripts loaded this way today: `htmx`, `dark-mode.js`, `admin-modal.js`, `flash.js`, `typeahead-combobox.js`, and the detail-interaction scripts (`org-detail.js`, `person-detail.js`, `role-merge.js`, the list-merge engine + consumers `merge-mode.js` / `people-merge.js` / `orgs-merge.js` / `roles-merge.js` (#249/#250/#251), `add-row-guard.js`, the `person-name-*` editor scripts, and `event-form-row.js` — the entity-event form-row typeahead + linked-entity scope wiring, #172). See #237.

### Page-specific scripts

Detail pages once injected their scripts via `{% block extra_head %}`. **Do not** — `extra_head` renders inside `<head>`, which hx-boost strips from boosted-navigation responses, so the script silently never runs when the page is reached by clicking a link (#237). Instead:

- **Persistent listeners, or any behavior needed on a boost-reached detail page** → load from `base.html`'s `<head>` with `defer`. Because it now loads on every admin page, make the script defensive (no-op when its target elements are absent); if it binds per-element, make it idempotent and re-bind on `htmx:load` (the boosted-swap signal) without double-binding.
- Extract inline scripts to files in `src/static/admin/` — no inline `<script>` blocks.

`{% block extra_head %}{% endblock %}` remains available for `<link>` / meta tags or page-specific assets whose effect need not survive a boosted navigation.

### Flash notifications

`flash_trigger(level, body, extra=None)` from `src.api.admin.deps` — sets `HX-Trigger: {"showFlash": {...}}`; `flash.js` injects the flash into `#flash-region`.

- Pass as `headers=flash_trigger(level, body)` to `TemplateResponse`
- For non-HTMX inline flash: `message(level, body)` from `admin/macros/flash.html`
- Levels: `success`, `info`, `warning`, `error`
- Always `markupsafe.escape()` DB-derived values before interpolating into `body`
- `extra` co-emits additional HX-Trigger events (merged into one JSON object): `flash_trigger("success", "Saved.", extra={"myEvent": {...}})`

### Page header sync

On any mutation route that may change an org's canonical name or acronym, pass `extra=await org_header_extra(org_id, db)` to `flash_trigger` (from `src.api.admin.deps`). Returns `{"updateOrgHeader": {"display": ...}}`; `org-detail.js` handles the event and updates `#page-heading`, `#breadcrumb-current`, and `document.title` in-place. Equivalent `person_header_extra` for person routes. → §30 for full client-side pattern.

### Lingering-state warnings (#307)

When an entity enters a terminal-ish state (archived / inactive / lifespan ended) while still carrying live children that now read as stale — e.g. an org past its lifespan with open role assignments — surface it twice:

- **Persistent banner** on the detail page: `alert alert--warning` block under the page header, rendered whenever `<terminal condition> AND <live-children count>`. The banner body lives in a shared partial (`admin/orgs/partials/_lifespan_banner.html`, `{% if open_assignment_count %}`) wrapped by a stable-id container (`<div id="org-lifespan-banner">`) on org detail. Banner names the count, the state, the boundary date when known, and the remedy ("close or re-home"). Persistent beats transient here — the condition outlives the mutation that created it (archive redirect, merge, external ingest).
- **In-place OOB re-render on the active toggle** (#320): the toggle is an inline HTMX POST that never reloads the page, so it must re-render the banner itself or it goes stale (banner lingers after re-activating, and never appears when deactivating). The toggle POST returns `_active_toggle_response.html` — the toggle partial (primary swap into `#active-toggle`) plus an `hx-swap-oob="true"` copy of the `#org-lifespan-banner` container. Both the detail GET and the toggle derive `(open_assignment_count, org_ended_on)` from the one helper `resolve_lifespan_banner(conn, org)` (`src.core.org_lifecycle`), so the "when to warn" gating (`archived_at OR not active OR ended`) can't drift between surfaces.
- **Warning flash** on the mutation that creates the condition: upgrade the flash to `level="warning"` and append the count + remedy (deactivate toggle), or append a `" Warning: …"` suffix to a success flash when the mutation's primary outcome succeeded (org merge into an ended/inactive winner — `_winner_lifespan_note` in `orgs_merge.py`).

Count predicates live in `src.core` next to the domain logic (`count_open_assignments`, and the banner-gating `resolve_lifespan_banner`, in `src.core.org_lifecycle`), never inlined per-route — neither the "open" definition nor the "when to warn" gating may drift between surfaces. Domain rules → `docs/CONVENTIONS.md` §"Org lifespan bounds on assignments".

### Person-name metadata controls (Phase 2a–2d, #123)

Person-name CRUD shares its router factory with org-name CRUD via `make_names_router` in `src.api.admin._names_shared`. The factory accepts `supports_person_metadata: bool = False`:

- `org_names`: leaves the default (`False`) — `organization_names` has no person metadata columns.
- `people_names`: passes `supports_person_metadata=True` — accepts `visibility`, `locale`, `script`, `sort_as` Form fields on create/edit and persists them.

A second, independent gate `supports_effective_dates: bool = False` (#239) controls the org name-validity timeline:

- `org_names`: passes `supports_effective_dates=True` — the create/edit form accepts `effective_start` / `effective_end` date inputs and writes them to `organization_names` (form-as-source-of-truth: an empty input clears the column to NULL). `_parse_optional_date` converts empty strings to None; a malformed value raises `_DateParseError` → 422/flash. The DB `chk_org_name_effective_date_order` CHECK is caught (`asyncpg.CheckViolationError`, `constraint_name` match) and surfaced as a friendly flash rather than a 500. The org name read row shows the effective range and the names table has an `Effective` column (colspan 5).
- `people_names`: leaves the default (`False`) — `person_names` has no effective-date columns; person forms are unaffected. Kept separate from `supports_person_metadata` so the two entity types stay decoupled.

Validation layering:

- Pydantic / FastAPI validates `visibility` against the `PersonNameVisibility = Literal["public","legal_only","hidden"]` from `src.core.types` at request parse — invalid values return 422.
- `_normalise_optional_str` strips whitespace and converts empty strings to None for `locale`/`script`/`sort_as` so blank inputs become NULL columns rather than ''.
- The org-vs-person divergence is the inline `vis = visibility if supports_person_metadata else None` gate at the top of each handler. The same `... if supports_person_metadata else None` shape is used for `loc`, `scr`, `sa` immediately below. Payloads sent to org_names are silently dropped.
- `_metadata_pairs(...)` returns the canonical (column, value) tuple ordering used by both builder helpers:
  - `_insert_name`: includes a column only when its value is non-None — DB defaults (`visibility='public'`, others NULL) handle the rest.
  - `_update_name(write_metadata=True)`: SETs every metadata column to the supplied value (form is the source of truth) — except visibility, which is skipped when None so the DB default + `trg_deadname_visibility` trigger keep authority.
- FK violations on `locale` / `script` are caught in both create and edit handlers; HTMX → 200 + flash trigger with column-specific message via `_fk_violation_message`; non-HTMX → 422. Never a bare 500.

Locale + script typeahead (Phase 2b):

- HTML option-list endpoints `GET /admin/people/_locale_search` and `/_script_search` (in `src.api.admin.people_locale_script_search`) return `<li role="option" data-id data-label>` partials shaped for the existing `typeahead-combobox.js` factory. Substring filter on code OR human-readable column with `escape_like` + `ESCAPE '\\'`; sorted code ASC; capped at `limit` (default 20, max 100); empty `q` returns no rows.
- The form-row template's display input mirrors its trimmed value to the hidden code field on `blur`, so typed-but-not-selected input still submits — invalid codes then trip the FK-violation flash rather than being silently discarded.

Sort + collation (Phase 2b):

- `v_person_display_names.sort_key = COALESCE(sort_as, name)`. Every person ORDER BY uses `sort_key COLLATE "und-x-icu" NULLS LAST` for diacritic-aware ordering (Å near A) with `sort_as` overrides honored.

Linked names — `reading_of_id` (Phase 2c, #123):

- Name CRUD accepts a `reading_of_id` Form field gated by `supports_person_metadata`. The column is a self-FK on `person_names` (ON DELETE CASCADE) — a `reading` / `romanization` / `mrz` row may point at the visual row it transliterates.
- Typeahead `GET /admin/people/{person_id}/_reading_target_search` returns same-person rows whose `name_type` is OUTSIDE `_READING_TYPES` (`reading`, `romanization`, `mrz`) — only visual rows are valid parents. Filters `visibility = 'public'` to mirror the default detail view; uses `escape_like` + `<> ALL($N::text[])` for the type filter.
- `_validate_reading_of_target` (in `_names_shared.py`) runs before the INSERT/UPDATE and surfaces four bypass attempts as form errors (HTMX flash; non-HTMX 422):
  1. Target row doesn't exist (DB FK catches it too — this gives a friendlier message).
  2. Target is on a *different* person (cross-person link).
  3. Target equals the editing row's own id (self-reference; `name_id` is threaded through on the edit path).
  4. Target's `name_type` is itself in `_READING_TYPES` (chain — A→B→C is rejected even if each link is technically same-person).
- The form template's reading-of block is hidden by default; inline JS shows it when `name_type ∈ _READING_TYPES`.
- Read-row template indents linked rows (`class="name-row--child"`) and renders a "↳ {name_type} of: <em>{parent_name}</em>" subtitle. The handler enriches each row with `reading_of_name` (LEFT JOIN parent) and `reading_child_count` (LATERAL count) — both the detail-page and the post-mutation tbody re-render in `_fetch_names_for_rows` carry the enrichment so cancel-from-edit + post-save look identical.
- Delete confirm text becomes "Delete this name and its N linked reading row(s)? (cascade)" when `reading_child_count > 0`.

Structured parts — `person_name_parts` (Phase 2d, #123 / Issue #127):

- Sidecar table, 1:0..1 with `person_names`, ON DELETE CASCADE.
- Server-side validation (in `upsert_or_delete_parts` helper, `src.api.admin.people_name_parts`):
  - Cap: 5 entries per array (`given_names`, `family_names`, `additional_names`). Cap is checked BEFORE trimming so the message reflects what the user typed.
  - Empty-string trim: blank entries are dropped before INSERT; `_trim_array` preserves user order.
  - `primary_identifier` allowlist matches the DB CHECK (`family` / `given` / `patronymic` / `mononym`); a blank value becomes NULL.
  - All-empty payload semantics (Issue #127): if the row already had a parts row, Save **deletes** it; if it never had one, no-op. There is no separate Remove button — clearing every parts field and clicking Save is the delete path.
- Read-row subtitle: handlers attach a `parts_summary` field via `build_parts_summary(family, given, additional)` from `src.api.admin.deps` — a "<family> · <given> · <additional>" line; None when nothing structural is set so the template's `{% if n.parts_summary %}` guard hides the row.

Person-name editor — single form / single Details disclosure / single Save (Issue #127):

- One `<form>` per name row in `_name_form_row.html` posts to `/edit-row/` (existing rows) or `/` (new rows). The earlier two-form split (outer name form + inner parts form) is gone; one Save commits both halves in one transactional upsert via `name_create` / `name_edit_row_post` calling `upsert_or_delete_parts` inside the existing `async with db.transaction():` block.
- Inline portion of the row (visible without expanding anything): name input, name_type select, is_canonical toggle, Save, Cancel.
- **`name_type` dropdown source of truth**: the `<select name="name_type">` iterates a `name_types` context variable supplied by the names-router factory. People-side routes pass `PERSON_NAME_TYPES` and orgs-side passes `ORG_NAME_TYPES` (both in `src/core/types.py`); the settings page reads from the same constants. To add a new value: update `src/core/types.py` (Literal + tuple) and the schema CHECK in lockstep — the dropdown, settings badges, and parametrized round-trip tests pick it up automatically. `tests/core/test_types.py` enforces tuple ↔ Literal ↔ schema parity and fails on drift. The form handlers also reject unknown `name_type` values with a 422 / flash before they reach the DB CHECK, so a stale cached form surfaces a friendly error instead of a raw `CheckViolationError`.
- A single `<details>` "Details" disclosure (rendered by `_name_parts_editor.html`) holds, in order:
  1. **Metadata**: visibility / sort_as / locale / script / reading_of_id (from `_name_metadata_fields.html` — order paired so flex-wrap puts visibility+sort_as on one row and locale+script on the next at most viewport widths; updated in #131).
  2. `<hr>` separator.
  3. **Name parts**: primary_identifier, the given/family/additional CardStack inputs (`person-name-parts-cardstack.js`), and honorific prefix/suffix.
- Auto-open predicate (in `_name_parts_editor.html`): the disclosure renders with `open` when any non-default metadata or parts value is present on the editing row:
  ```jinja
  {%- set _meta_set = n and (
      (n.visibility and n.visibility != 'public') or
      n.locale or n.script or n.sort_as or n.reading_of_id
  ) -%}
  {%- set _parts_set = parts is not none -%}
  ```
  The `n and (...)` guard is defensive — `_name_form_row.html` only includes the parts editor when `n` is set, but the predicate stays safe if that gate ever changes.
- New-name form (`n is None`) does not render the Details disclosure (no `name_id` to attach parts to). To keep metadata fields reachable when creating a row, `_name_form_row.html` includes `_name_metadata_fields.html` directly inline in that branch — same markup, different host.
- Typeahead init `<script>` (locale / script / reading_of_id) lives in `_name_form_row.html` AFTER the include so it runs once the inputs (which may be inside the disclosure) are in the DOM. Browsers query elements inside `<details>` regardless of open state.
- The standalone `POST /parts/` and `POST /parts/delete/` routes are deleted — `_summary_oob_fragment` and `_ensure_name_belongs_to_person` are gone with them. The unified Save flow re-renders the whole tbody so the OOB-swap pattern is no longer used.

Issue #131 follow-ups (lookup bug fix + redesign II):

- **Typeahead query parameter**: locale / script / reading-of inputs must send `q={value}` to their search endpoints. The display inputs are unnamed (so they don't pollute the parent Save POST) and use `hx-vals='js:{q: (event && event.target ? event.target.value : "")}'` to set the request param. The earlier shape (`name="q_locale"` + `hx-params="q"`) silently sent no `q` because no input was named `q` — the filter dropped everything. Don't reintroduce `hx-params="q"` here.
- **Per-row ID namespacing**: every typeahead element id (`locale-search-display`, `locale-search-results`, `locale-hidden`, the script + reading-of equivalents, and `reading-of-block`) is suffixed with a `_uid` discriminator. `_name_metadata_fields.html` and `_name_parts_editor.html` agree on `{%- set _uid = n.id if n else 'new' -%}`; the form row also exposes `data-name-row-typeahead data-uid="{{ _uid }}"` on its `<tr>` for the per-row JS module to discover. This lets an open Edit drawer and the inline new-name form coexist on the page without `getElementById` collisions.
- **Per-row JS wiring**: typeahead init + reading-of-block toggle live in `src/static/admin/person-name-row-typeahead.js`. The module hooks `DOMContentLoaded` for server-rendered rows and `htmx:afterSwap` for HTMX-injected rows, scans the swap target for `[data-name-row-typeahead]`, and reads `data-uid` to compose the namespaced element ids. No inline `<script>` in `_name_form_row.html`.
- **+ Add duplicate-row guard (`add-row-guard.js`, #238)**: every inline "+ Add" button that prepends an unsaved `<tr id="<entity>-row-new">` is disabled while that row exists, so a double-click can't create two colliding `#<entity>-row-new` rows (the id-collision class #131 / #237 fought on person names and events). A button opts in with **`data-new-row-id="<tr-id>"`** (e.g. `name-row-new`, `acronym-row-new`, `email-row-new`). `src/static/admin/add-row-guard.js` is loaded site-wide from `base.html` (#237), registers document-level listeners once (`htmx:afterSwap`, `htmx:load`, `powerMap:newRowClosed`), and `sync()` scans `button[data-new-row-id]` on every call — disabling each iff its row is present. Document-scoped (not table-scoped) so it survives hx-boost and catches outerHTML row swaps; each `<entity>-row-new` is page-unique, so a global id check is correct. This one guard replaced the per-feature `person-detail-add-name-guard.js` and `event-add-guard.js`, and uniquely handles pages with **multiple** add-buttons (org detail) that a single-button-by-id guard could not. New-row Cancel handlers must dispatch `powerMap:newRowClosed` (they remove the row client-side with no HTMX round-trip); existing-row Cancel uses an `hx-get` read-row swap and needs no dispatch.

  **Two race windows, two owners — don't cross them.** Double-clicking "+ Add" is two separate races: (A) a second request fired *while the first is in flight*, before any row exists; and (B) a deliberate second click *after* the row is rendered. The guard owns **B** — it's a UI invariant over DOM state ("disabled while `#<entity>-row-new` exists"), and it is the **sole writer of `disabled`**. Window **A** is a request-lifecycle concern and belongs to htmx: every guarded button carries **`hx-sync="this:drop"`**, so htmx drops a second concurrent request from the same element. Do **not** use `hx-disabled-elt="this"` here: htmx re-enables a disabled-elt after the swap (`htmx:afterRequest`, after `htmx:afterSwap`), which clobbers the guard's disable and reopens window B (#238 CR). Because `hx-sync` never touches `disabled`, the two mechanisms compose without conflict and without depending on htmx event ordering. In-flight *visual* feedback needs no per-button `hx-indicator`: htmx adds `htmx-request` to the requesting button by default, and the global loading rule (`admin.css` — `.htmx-request { opacity:.6; cursor:wait; pointer-events:none }`) dims it for the request's duration. That rule is CSS-only, so it too leaves `disabled` to the guard.
- **`powerMap:` custom event prefix**: project-wide convention for client-side custom DOM events that don't go through HTMX's `HX-Trigger` header (those follow the `update{Entity}Header` camelCase shape — see [§JS file](#js-file-srcstaticadminentity-detailjs)). Today's only `powerMap:` event is `powerMap:newRowClosed` (dispatched by every new-row inline Cancel; #238); future custom events use the same prefix to avoid colliding with browser/library events. Page-wide `powerMap:*` events are dispatched on `document` and listened on `document` (matches the page-wide `htmx:afterSwap` listener convention used by `person-name-deadname-confirm.js` and `person-name-parts-cardstack.js`); element-scoped events should target the relevant element directly.
- **Hint-as-placeholder convention**: locale/script/sort_as/honorific-prefix/honorific-suffix carry concrete examples in their `placeholder` attributes (e.g. `Locale` → `BCP 47 — e.g. en, en-US, ja-JP`). The previous below-control `<small>` helpers under honorific prefix/suffix are removed; the placeholder is the single source of truth for one-line guidance. Primary Identifier is the exception: its multi-line cultural-context help (`<small>` with `family in Japan; patronymic in Iceland; mononym ...`) sits between the label and the `<select>` — placeholders can't hold that much text.
- **Cardstack inputs full-size**: each card in the given/family/additional CardStacks wraps its `<input>` in `<div class="form-group" style="margin-bottom:0;flex:1">` so the input inherits the baseline `.form-group input` rule (font-size, padding, `min-height: 44px`). A bare `<input style="flex:1">` falls back to browser-default text input styling and renders visibly smaller than the rest of the form.
- **Reorder focus-follows-value (#145)**: after a ↑/↓ click on a cardstack arrow, `person-name-parts-reorder.js` moves focus to the neighbor card's same-direction button so repeated keypresses walk the value through the stack. At the boundary (neighbor's same-direction button is disabled), focus falls back to the neighbor's input — the cell that just received the value. Lookups are scoped to the neighbor element (form-scoped via `cardsIn(stack)`), so concurrent reorder in one form never moves focus out of that form.

### Roles — structural fields (#264)

The roles admin surfaces a role's structural fields (`role_type_id` / `jurisdiction_id` / `qualifier`, #261) under the **Role type** label. Rules that keep the admin from becoming a title-drift vector (#267):

- **One structural block, not three independent fields.** The three columns are constraint-coupled, so they're edited together via a single inline read/edit pair (`_structural_read.html` / `_structural_form.html`) on `#structural-field`, and as one `<fieldset>` on the new-role form. Both use **progressive disclosure**: role-type `<select>` (from `role_types`) → reveal jurisdiction typeahead → reveal qualifier. Selecting role type = "none" clears the jurisdictional sub-fields (demotes it to a plain role). The list + detail **badge shows the role-type name** (`badge--role-type`), never a generic composite label.
- **Jurisdiction typeahead**: `GET /admin/jurisdictions/search/` (`src.api.admin.jurisdictions`) is a read-only `<li role="option" data-id data-label>` fragment for the shared `typeahead-combobox.js` factory. Mirrors `/admin/orgs/search/`. (Jurisdictions also have a full admin surface — see "Jurisdictions — admin surface" below.)
- **Title is PM-curated for a role with a jurisdiction.** Both create and inline structural-save **always synthesize** the canonical title via `src.core.role_title.synthesize_role_title` for a fully-qualified role (WA legislative only) — any supplied title is ignored so the admin can't diverge from the canonical form. A manual title is kept/required only when synthesis is unavailable (non-WA jurisdictions). The free-text title editor is **gated** when a role has a role type: `_title_read.html` shows "Curated from the role type" instead of Edit, and `POST /inline/title/` refuses to retitle a role with a `role_type_id`.
- **Validation mirrors the DB.** Both handlers reproduce `chk_role_qualifier_needs_jurisdiction` (qualifier ⇒ jurisdiction) and `chk_role_jurisdiction_needs_role_type` (jurisdiction ⇒ role type) with clear flash errors, and catch `UniqueViolationError` for both `uq_role_structural` and `uq_role_org_title`.
- **Known gap:** admin `role_create` / inline structural-save write the row directly (no `resolve_role`, so no outbox/entity_changes emission) — consistent with the rest of the admin, unlike the observation path.

### Dup count cache

`count_org_duplicates(db)` in `src.api.admin.org_dups` and `count_person_duplicates(db)` in `src.api.admin.people_dups` are TTL-cached (5 min, process-local). Call `invalidate_dup_count_cache()` from the appropriate module after any merge or dismiss. All people and org routes inject both counts via deps; sidebar badges use these template vars directly (no HTMX XHR).

Caveat: cache is not shared across gunicorn workers — counts may lag by up to 5 min per worker.

### Jurisdictions — admin surface (#275)

`src.api.admin.jurisdictions` surfaces jurisdictions as a first-class managed entity (sidebar link, Entities-index card, dashboard count):

- **List** (`GET /admin/jurisdictions/`) via `query_jurisdictions_rows` (`jurisdictions_queries.py`). Search is **ILIKE on name/slug** — jurisdictions have no `search_tsv` (unlike orgs/people, which use `pm_prefix_tsquery` — last-token prefix FTS, #316). Type filter from `jurisdiction_types`; three-value status axis **active / superseded / archived** partitioning on `(archived_at, superseded_at)` (a superseded row keeps `archived_at IS NULL` — supersession is not soft-delete). HTMX region swap mirrors orgs.
- **Create** (`GET/POST /admin/jurisdictions/new/`): slug/name/type required, slug-uniqueness (422) + validity-range validation, unknown-type guard. Direct `INSERT` (no `resolve_entity`); the `updated_at` + `entity_changes` triggers emit the change feed with no extra plumbing (unlike the roles admin's known outbox gap).
- **Detail** (`GET /admin/jurisdictions/{id}/`): a single inline **Edit details** form (name/slug/type/validity/notes) — slug carries a public-`/resolve`-key caveat + 422s on collision; empty `type_id` keeps the current type. A name change emits `updateJurisdictionHeader` and `jurisdiction-detail.js` (loaded site-wide, boost-safe) updates the heading in place. **Archive/unarchive/delete** mirror the orgs lifecycle (delete requires archived; FK-guarded 409 when referenced by a role/relationship/affiliation). **Attachment panels** (identifiers/links/contacts via the shared factory routers `jurisdictions_{contacts,links,identifiers}.py`; addresses hand-built in `jurisdictions_addresses.py`) are interactive (+ Add / inline edit / delete). **Referencing roles** (= the reciprocal of the role picker) and **Lineage** stay read-only (derived views).
- **Graph editing (Phase 3)**:
  - **Relationship edges** (`jurisdictions_relationships.py`) — interactive Relationships panel: add a typed edge (target typeahead reuses `/admin/jurisdictions/search/`; `rel_type` grouped by category; a **direction** toggle for asymmetric types with a live phrase preview — symmetric types store once, read both ways; validity + notes), inline-edit validity/notes (the temporal *end*), hard-delete (no `archived_at` — the table has none). DB guards `chk_no_self_rel` / `chk_rel_valid_range` surfaced as 422. **Lineage-category** edges are creatable here but render in the read-only Lineage panel, not Relationships (the detail query filters `category <> 'lineage'`). **Category display labels** render via the `rel_category_label` Jinja filter — source of truth `RELATIONSHIP_CATEGORY_LABELS` in `src.core.jurisdictions`, injected on all admin template envs at startup (`inject_rel_category_label_into_admin_templates`), sync with the schema CHECK enum test-enforced (#278).
  - **Change feed**: `jurisdiction_relationships` had no trigger, so a `touch_parent_jurisdiction()` trigger (mirrors `touch_parent_org`) touches both endpoints' `updated_at` on any edge INSERT/UPDATE/DELETE → fires `trg_entity_changes_jurisdictions` on each. Keeps jurisdictions outbox-gap-free.
  - **Org affiliations** (`jurisdictions_affiliations.py`, two routers over one table) — **bidirectional**: the "Affiliated organizations" panel on jurisdiction detail (org typeahead + type) *and* a reciprocal "Affiliated jurisdictions" panel on **org detail** (jurisdiction typeahead + type). Unique `(org, jur, type)` → 409. Affiliation writes touch **both** sides' change feed — the org via `trg_touch_org_on_affiliation_change` and the jurisdiction via `trg_touch_jurisdiction_on_affiliation_change` (#275 Phase 3) — so a subscriber on either entity sees the edit.
- **No dup surface** — jurisdictions have no dup tables (no merge/dismiss, no dup badge).
- **Shared lineage helper**: the recursive lineage CTE lives once in `src.core.jurisdictions.fetch_lineage`, shared by the public lineage endpoint (#168) and the admin detail — anchor on a resolved id (callers resolve slug→id first).

### Person voice-embeddings section (#284)

`src.api.admin.people_embeddings` adds a **read-only** "Voice Embeddings" section to the Person detail view. No create/paste-in (the row's NOT-NULL voice provenance + `created_by_key_id` FK make console entry impractical); no metadata edit; no similarity search.

- **Registry-driven, multi-model**: `fetch_person_embeddings(db, registry, person_id, include_archived=…)` loops `app.state.embedding_registry.all()` and unions rows across every model table, tagging each with its `model_id`. Table names come **only from the registry** (never user input) — same injection-safe pattern as the public embeddings API. Loaded in the `person_detail` handler and rendered server-side like Identifiers.
- **Vector column**: only a preview (`left(embedding::text, 10)`) is rendered in-page; the full 256-float literal is fetched on demand from `GET …/{model_id}/{eid}/vector/` (`PlainTextResponse`) by `embedding-copy.js` (document-delegated, site-wide, boost-safe), which writes it to the clipboard and fires a `showFlash` event.
- **Archived toggle**: `?show_archived_embeddings=1` full-page reload (mirrors the Names `show_historical` pattern) reveals archived rows dimmed; the toggle label shows the archived count.
- **Lifecycle** (archive-model conventions): Delete soft-archives (409 if already archived); Restore clears `archived_at` (409 if already active); **Delete permanently** hard-deletes and **requires the row be archived first** (409 otherwise). Each write is guarded on `archived_at IS [NOT] NULL … RETURNING id` to close the check-then-act window; a matched-nothing write re-checks existence to report 404 (row gone) vs 409 (wrong state). Mutations re-render the **whole** `#person-embeddings-section` (header + table) via `_embeddings_section.html` — not just the tbody — so the "Show archived (N)" toggle refreshes with a fresh count; the current `show_archived_embeddings` state passes through a query param so the swap stays in the same mode; all carry `flash_trigger` + a `RedirectResponse` non-HTMX fallback.

### Citations indicator on entity rows (#341)

Rows whose entity carries active citations surface a compact count so sub-entity citations (esp. `role_assignment`) are discoverable without opening the row.

- **Count lives in the row-fetch SQL, never a side dict.** Every query that feeds a row partial joins `citation_count_lateral(entity_type, id_expr)` (from `src.api.admin._citations_shared`) — one `LEFT JOIN LATERAL count(*)` probe per row on `idx_citations_entity`, active rows only (`archived_at IS NULL`). Rationale: row partials re-render standalone via single-row HTMX routes (read-row Cancel, archive, edit-save); a template-context dict would have to be recomputed on every such path or the indicator silently disappears after a swap. Single-row handlers that build a dict context (e.g. names factory `name_read_row`) may instead attach one scalar `count(*)` — one row, not an N+1.
- **Rendering — two affordance shapes:**
  - Rows *without* an inline Cite drawer (assignment rows, org-roles rows): the `citation_indicator(count, href=…)` macro (`admin/macros/citation_indicator.html`) — `📚 N`, emoji `aria-hidden`, wrapper `aria-label="N citation(s)"`, count as visible text (never emoji-only); pass `href` to the page hosting the citations panel (assignment detail / role detail). Renders nothing at 0.
  - Rows *with* the #319 Cite drawer-toggle button (person-name rows, event rows): the count renders **inside the existing button**, held in a `<span id="cite-count-<entity_id>">` so in-drawer mutations can refresh it — `citation_create`/`citation_delete` in the citations factory emit an `hx-swap-oob` fragment (`admin/citations/partials/_cite_count_oob.html`) with the fresh active count; htmx silently drops the fragment for panel-hosted entity types that have no row button. The button `aria-label` stays **count-free** (a stale label is worse than none; the count is a visual supplement, and the open drawer lists the rows).
- **Scope:** citable sub-entities shown as rows. Top-level entity lists (orgs / people / jurisdictions) are out — those surface citations on their own detail panel. `org_name` is **not** a citable type; org name rows never get a count.
- **Tests:** indicator renders with count ≥ 1, absent at 0, archived citations excluded, and the single-row re-render path keeps the indicator (the regression the SQL-embedding rule exists to prevent).

---

## 33. Vitest test conventions

JS test files in `tests/js/` mount admin scripts via `eval(scriptCode)` against a happy-dom DOM. The scripts are IIFEs that auto-attach listeners on load — there is no exported teardown hook. The conventions below normalize how stubs are built and how listener leaks are prevented across tests.

### `vi.fn()` vs `vi.spyOn()`

- **`vi.fn()`** — a fresh function with no original behavior. Use for stubs that *replace* a function (no call-through). Example: stubbing `window.initTypeaheadCombobox` so the script-under-test reaches it without us caring what the real combobox factory does.
- **`vi.spyOn(obj, 'method')`** — wraps the existing method, calls through, and records every invocation in `.mock.calls`. Use when you need the real behavior plus call inspection.

Both surface `.mock.calls` (array of `[arg0, arg1, ...]` per call) and `.mock.results`. Prefer Vitest helpers over hand-rolled `const calls = []; fn = (x) => calls.push(x)` accumulators — the helpers also restore cleanly via `mockRestore()` / `vi.restoreAllMocks()`.

### Listener cleanup is mandatory for `eval()`-mounted scripts

The `eval(scriptCode)` mount pattern re-runs the IIFE on every test. Every `document.addEventListener(...)` call inside the IIFE attaches a *new* handler — and there is no teardown hook to remove it. Without explicit cleanup the handlers accumulate across tests: a single `document.dispatchEvent(...)` in test N triggers N listener firings, which silently inflates call counts (or papers over real bugs by way of duplicate idempotent handlers).

Spy on `document.addEventListener` in `beforeEach`, then in `afterEach` walk the spy's `.mock.calls` and `removeEventListener` each `(type, fn)` pair before restoring the spy.

### Cleanup template

Add to every test file whose script-under-test attaches `document` listeners:

```js
let addSpy;

beforeEach(() => {
  addSpy = vi.spyOn(document, 'addEventListener');
});

afterEach(() => {
  for (const [type, fn] of addSpy.mock.calls) {
    document.removeEventListener(type, fn);
  }
  addSpy.mockRestore();
  document.body.innerHTML = '';
});
```

Reference implementation: `tests/js/person-name-row-typeahead.test.js:63-87`.

Files that do NOT need this block:

- Pure expression-extractor tests that never `eval` script source or attach DOM listeners (e.g. `tests/js/name-typeahead-hx-vals.test.js`).
- Factory-style scripts where the test cleans up via the script's own teardown path (e.g. dispatching Escape to invoke the factory's `closeDropdown` removes the document-level listeners it registered) — but the spy-based block is still preferred for symmetry and to catch listeners the factory itself doesn't track.

---

## 34. Multi-instance form-row partials: row-key contract

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
- The standard inline-add row renders `id="<entity>-row-new"` (the `'new'` default). Its "+ Add" button opts into the duplicate-row guard via `data-new-row-id="<entity>-row-new"` (+ `hx-sync="this:drop"`), and the new-row Cancel dispatches `powerMap:newRowClosed` — see §32 ("+ Add duplicate-row guard"). Wire all three when adding a new multi-instance partial.

### Singleton-only partials

Some partials are guaranteed singletons (one parent per org, one org field per role, one open merge modal at a time). They DO NOT need the row-key dance — but the audit conclusion belongs in the partial's top-of-file comment so future contributors don't copy the singleton pattern into a multi-instance flow:

- `src/templates/admin/orgs/partials/_parent_form.html` — singleton (swap target `#parent-row`)
- `src/templates/admin/roles/partials/_org_form.html` — singleton (swap target `#org-field`)
- `src/templates/admin/orgs/_merge_search_modal.html` — modal portal pattern (one open at a time)

### Test coverage

`tests/js/typeahead-row-key-collision.test.js` is the regression guard. It builds two forms in the same DOM with distinct row-keys, evals the real `typeahead-combobox.js` factory, and asserts a selection in form B never mutates form A's hidden field — and that `aria-controls` on each input points at its own listbox. Add cases there when introducing a new multi-instance partial.

## 35. Activity › API Requests screens (#260)

Observability of public API traffic, under the **Activity** section
(`src/api/admin/activity_requests.py`, templates under
`src/templates/admin/activity/requests/`). Read-only over `api_request_log` — see
`docs/CONVENTIONS.md` § "API request log" for the capture/data contract.

- **Landing card** — `admin/activity/index.html` gains an "API Requests" card
  with a 24h pulse subtitle (`N requests · M rejected`) linking to the list, plus
  a "Busiest key (24h)" line (#294) so a runaway client is visible from the
  landing page.
- **Dashboard panel** — `admin/dashboard.html` Activity grid gains an
  "API Activity (24h)" card: total requests, observation dispositions (new /
  attached / **rejected**), changes polls/rows, error rate, last-request time.
  Rejections and a non-zero error rate render in `--color-danger`.
- **Per-key panel** (#294) — on the list page, between the stats strip and the
  filter form: one row per key active in the window (hottest first, from
  `src.core.anomaly.key_activity`), showing label (links to the key-filtered
  list), request count, req/hr, 429 count (danger-colored when non-zero), and
  last-seen. Rows at/above `API_ANOMALY_HOURLY_THRESHOLD` req/hr get the problem
  tint + a "hot" badge (threshold `<= 0` disables highlighting). The req/hr rate
  is a **window-average** — a short burst dilutes across a wide window, so the
  hourly anomaly timer, not this panel, is the burst detector. NULL-key traffic
  aggregates into a "— (no key)" row (`data-key-row="anon"`). Same 24h/7d window
  as the stats strip.
- **List** — `/admin/activity/requests/`. Stats strip (24h/7d window toggle via
  `request.url.include_query_params`), filter form (endpoint group defaulting to
  observations+changes, key **by label**, status class, disposition, free-text
  on path/reason), empty `/changes` polls hidden by default with a "Show empty
  polls" toggle. Rows with `status_code >= 400` or `disposition='rejected'` are
  tinted `rgba(220,38,38,0.06)`. Filters live in query params so a filtered view
  is shareable. Offset pagination (consistent with Import History), keyset on
  `id DESC`.
- **Detail** — `/admin/activity/requests/{id}/`. Metadata table + pretty-printed
  request/response JSON (`<pre>`, monospace). `result_entity_id` resolves to an
  admin entity link (`person` → `/admin/people/`, `organization` →
  `/admin/orgs/`) with a "(removed)" fallback when the entity no longer exists;
  the resolved key label + scopes are shown. `active_section = 'activity'` on all
  three routes so the sidebar Activity link stays highlighted.
- **PII** — the list shows metadata only; raw bodies live on the detail view
  (admin-authed). No live auto-refresh — reload to refresh.
