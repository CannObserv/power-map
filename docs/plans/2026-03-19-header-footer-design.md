# Header & Footer Branding

**Date:** 2026-03-19

## Goal

Add Cannabis Observer branding to the admin dashboard header and footer.

## Approved Approach

### Header

Replace the plain-text brand in `.admin-topbar__brand` with an icon + "Power Map":

```html
<a class="admin-topbar__brand" href="/admin/">
  <img src="/static/images/cannabis_observer-icon-square.svg"
       alt="" aria-hidden="true" class="admin-topbar__brand-icon">
  Power Map
</a>
```

Icon size: 28px × 28px (matches `font-size-lg` visual weight).

### Footer

Add `<footer class="admin-footer">` inside `.admin-layout` after `<main>`, spanning both grid columns:

```html
<footer class="admin-footer">
  A project of
  <img src="/static/images/cannabis_observer-icon-square.svg"
       alt="" aria-hidden="true" class="admin-footer__icon">
  <a href="https://cannabis.observer/" target="_blank" rel="noopener noreferrer">Cannabis Observer</a>
  🌱🏛️🔍
</footer>
```

Icon size: 18px × 18px. Centered, muted colour, small font.

### CSS changes

- `.admin-layout` grid-template-rows: `auto 1fr` → `auto 1fr auto`
- Add `.admin-footer`: `grid-column: 1 / -1`, `text-align: center`, `font-size: var(--font-size-sm)`, `color: var(--color-text-muted)`, `padding: var(--space-4)`, `border-top: 1px solid var(--color-border)`
- Add `.admin-topbar__brand-icon`: `height: 28px; width: 28px; vertical-align: middle; margin-right: var(--space-2);`
- Add `.admin-footer__icon`: `height: 18px; width: 18px; vertical-align: middle; margin: 0 var(--space-1);`

## Key Decisions

- **Icon is decorative** — `alt=""` + `aria-hidden="true"`; screen readers read "Power Map" / "Cannabis Observer" text instead.
- **Footer link** opens in new tab with `rel="noopener noreferrer"` (external site).
- **Grid row** extended to `auto` for footer; does not affect sidebar/main sizing.
- **Page `<title>`** unchanged (`… — power-map`); only visible brand name updates.

## Files Changed

- `src/templates/admin/base.html`
- `src/static/admin/admin.css`

## Out of Scope

- Changing the page `<title>` tag
- Adding branding to any non-admin pages
- Responsive footer behaviour beyond what flex/grid already provides
