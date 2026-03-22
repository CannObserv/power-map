# Style Guide — Design Doc

**Date:** 2026-03-22
**Issue:** #19 — docs: create project style guide (docs/STYLE.md)
**Status:** Approved

## Goal

Create `docs/STYLE.md` as a single authoritative style reference, and implement the accompanying changes: co-purple brand color, class-based dark mode toggle, accessibility gap fixes, and i18n groundwork.

## Approved approach

Six workstreams delivered together under issue #19.

---

## Workstream 1: Color system

Replace the current blue brand accent with co-purple, aligning power-map with the Cannabis Observer brand family.

### Token changes (`src/static/admin/admin.css`)

| Token | Before | After |
|---|---|---|
| `--color-brand` | `#2563eb` | `#6d4488` (co-purple) |
| `--color-brand-hover` | `#1d4ed8` | `#5a3870` (co-purple-700) |
| `--color-border-focus` | `#2563eb` | `#6d4488` |

New tokens:

| Token | Value | Purpose |
|---|---|---|
| `--color-brand-subtle` | `#f5f0f8` | Highlight panel backgrounds (co-purple-50) |
| `--color-brand-subtle-border` | `#ebe1f1` | Highlight panel borders (co-purple-100) |
| `--color-green` | `#8cbe69` | co-green — reserved, not used as UI accent |

Dark mode overrides use a lighter purple (`#a78bc4`) for sufficient contrast on dark surfaces.

### Rationale

Blue is not part of the Cannabis Observer brand palette. All other CannObserv projects (wslcb-licensing-tracker, address-validator) use co-purple as the primary UI accent. Sidebar active border, flash info accent, focus rings, and pagination all reference `--color-brand` — a single token change cascades everywhere.

---

## Workstream 2: Dark mode toggle

Add a user-controllable dark mode that respects `prefers-color-scheme` as the default and persists the user's choice in localStorage.

### CSS strategy

Keep the existing `@media (prefers-color-scheme: dark)` as a no-JS fallback. Add `.dark :root { … }` overrides that win when JS applies the class. Same token names — no new variables needed.

### FOUC prevention

Inline `<script>` in `<head>` (before the CSS `<link>`) reads `localStorage.getItem('pm-color-scheme')`, falls back to `matchMedia('prefers-color-scheme: dark')`, and sets `document.documentElement.classList` synchronously before first paint.

### Toggle button

Sun/moon icon button placed on the right of the topbar, between breadcrumb and user info. ARIA: `aria-label="Switch to dark mode"` / `"Switch to light mode"`, updated on toggle.

### JS file

`src/static/admin/dark-mode.js` — click handler updates localStorage, swaps `.dark` class on `<html>`, updates `aria-label`. Loaded with `defer`.

### localStorage key

`pm-color-scheme` — values `"dark"` | `"light"` | absent (use system default).

---

## Workstream 3: Accessibility gaps

### Standards (documented in STYLE.md)

WCAG 2.1 AA as the baseline. Key rules:
- Decorative emojis: `<span aria-hidden="true">emoji</span>` — never bare in template output
- Focus rings: `outline: 2px solid var(--color-border-focus)` with `outline-offset: 2px` on all interactive elements; `:focus-visible` (not `:focus`)
- Muted text: minimum `--color-text-muted` on all secondary/muted UI text
- ARIA on HTMX targets: `aria-live="polite" aria-atomic="false"` on all primary swap targets
- Icon-only buttons: always `aria-label`
- `title` attributes: do not use — inaccessible to keyboard/touch users

### Fixes in this issue

- Wrap footer emojis (`🌱🏛️🔍`) in `<span aria-hidden="true">` in `base.html` (currently bare)
- Verify skip link HTML element present in `base.html` ✓ (already present)
- Verify `aria-current="page"` on active sidebar links ✓ (already present)
- Verify hamburger `aria-label`, `aria-expanded` ✓ (already present)
- Audit all templates for bare emojis and fix
- Verify `aria-live` on HTMX swap targets across all list views

### Deferred (new issue)

Full contrast audit, screen reader testing, keyboard navigation pass, form error association.

---

## Workstream 4: i18n groundwork

Prepare the codebase for future full i18n (Babel/gettext) without implementing the translation infrastructure yet.

### Standards (documented in STYLE.md)

- **String externalization:** No hardcoded UI text in Python route handlers. User-facing strings belong in templates. Future: `_()` wrapper via Babel.
- **Date/number formatting:** Always format dates and numbers through a template filter or helper, never inline string concatenation. Document the `babel.dates.format_date` / `babel.numbers.format_number` pattern to adopt when Babel is added.
- **RTL-safe CSS:** Prefer CSS logical properties for all new CSS written in this issue: `margin-inline-start` not `margin-left`, `padding-block` not `padding-top/bottom`, `border-inline-start` not `border-left`, `inset-inline` not `left/right`. Existing properties left as-is; tracked in the follow-on issue.
- **`dir` attribute:** `<html lang="en" dir="ltr">` already present ✓. Document how to make `lang`/`dir` dynamic when a language switcher is added.
- **Unicode:** `<meta charset="utf-8">` required and already present ✓. Document NFC normalization expectation for DB text fields.

### Fixes in this issue

- Verify `<meta charset="utf-8">` and `<html lang="en" dir="ltr">` in `base.html` ✓ (already present)
- Use CSS logical properties in all new CSS written for this issue (dark mode tokens, color updates)

### Deferred (new issue)

Babel/gettext setup, `.pot`/`.po` translation files, language switcher UI, locale middleware, `format_date`/`format_number` Jinja2 filters.

---

## Workstream 5: docs/STYLE.md

Single authoritative reference covering:
- Brand assets and color palette
- Dark mode conventions
- CSS design token system and how to extend it
- Layout conventions (admin grid, scroll container, dvh)
- Responsive breakpoints (768px mobile nav, 640px action stacking)
- HTMX patterns (`_is_htmx`, OOB flash, mutation forms, loading states)
- Flash/notification UX (levels, auto-dismiss, persistent banners, XSS escaping)
- Pagination conventions (sticky footer, page-size select)
- Destructive actions (archive-gate, flash confirmation)
- Dedup workflow
- Accessibility rules (WCAG 2.1 AA baseline, emoji, focus, ARIA)
- i18n groundwork (logical properties, string externalization, date/number patterns)
- Performance rules (no CDN, cache-busting, deferred JS)

---

## Out of scope

- **Responsiveness** — table overflow, touch targets, detail-grid stacking: separate issue
- **i18n infrastructure** — Babel, translation files, language switcher: separate issue
- **Accessibility full audit** — contrast, screen reader, keyboard nav: separate issue

---

## Key decisions

| Decision | Rationale |
|---|---|
| co-purple replaces blue | Align with CannObserv brand family; single token cascades everywhere |
| `prefers-color-scheme` kept as CSS fallback | No-JS users and initial load before script runs still get correct theme |
| FOUC prevention via inline script | Synchronous execution before first paint; acceptable given small size |
| Logical properties only in new CSS | Minimizes diff; existing properties tracked in i18n follow-on |
| Emoji `aria-hidden` fix scoped to templates | Screen readers announce emoji as verbose descriptions; wrapping is silent |
