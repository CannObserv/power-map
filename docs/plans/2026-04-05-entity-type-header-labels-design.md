# Entity Type Header Labels

## Goal

Add a subtle entity type label above the `<h1>` name on each detail page (Person, Organization, Role, Role Assignment) for orientation and polish.

## Approved approach

Small uppercase muted text above the heading — no badge, no JS, no backend changes.

### HTML pattern

Wrap type label + `<h1>` in a `<div>` inside `.page-header` so they stack vertically while the Edit button stays right-aligned:

```html
<div class="page-header">
  <div>
    <span class="page-header__type">Person</span>
    <h1>Jane Doe</h1>
  </div>
  <a href="..." class="btn btn--primary">Edit</a>
</div>
```

### CSS

```css
.page-header__type {
  display: block;
  font-size: var(--font-size-xs);
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: var(--color-text-muted);
  margin-bottom: var(--space-1);
}
```

### Labels per entity

| Detail page       | Label            |
|--------------------|------------------|
| People             | Person           |
| Organizations      | Organization     |
| Roles              | Role             |
| Role Assignments   | Role Assignment  |

## Key decisions

- Singular noun (not plural) — describes the individual record
- `align-items: baseline` on `.page-header` already handles vertical alignment; wrapping div keeps the two-line stack together
- No color differentiation per type — muted text only, keeps it subtle

## Out of scope

- Colored badges or pills per entity type
- Type labels on list pages
- Any backend or JS changes
