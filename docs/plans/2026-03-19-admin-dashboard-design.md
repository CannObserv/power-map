# Admin Dashboard — Design

**Date:** 2026-03-19
**Status:** Approved

## Goal

Server-rendered administrator dashboard at `/admin/` for managing all power-map entities. Authentication delegates to the exe.dev proxy; any authenticated exe.dev user is an admin.

## Approved Approach

Modular FastAPI routers (one per entity type) + Jinja2 templates + HTMX for partial updates + custom CSS design-token system. No JS framework. No build step.

## Module Layout

```
src/api/admin/
  __init__.py
  deps.py               — AdminUser dependency (reads exe.dev headers, redirects if absent)
  router.py             — mounts all sub-routers under /admin/
  orgs.py
  people.py
  roles.py
  role_assignments.py
  lookups.py            — platforms, url_types, entity_identifier_types
  imports.py            — import_batches, provenance, field_confidence (read-only)

src/templates/admin/
  base.html             — layout shell: sidebar + topbar + content slot
  partials/             — HTMX response fragments (table bodies, form fields, flash msgs)
  orgs/  people/  roles/  role_assignments/  lookups/  imports/

src/static/admin/
  admin.css             — design token system + all component styles
```

## Authentication

- Reads `X-ExeDev-UserID` and `X-ExeDev-Email` headers injected by the exe.dev proxy.
- Missing headers → `RedirectResponse("/__exe.dev/login?redirect=/admin/")`.
- Returns `AdminUser(id, email)` dataclass via FastAPI `Depends` on all admin routes.
- For local dev: `mitmdump` reverse proxy injects headers (see `docs/COMMANDS.md`).
- Authorization: any authenticated user. No allowlist.

## Entity Management

### Core entities

| Entity | List | Detail | Create/Edit | Archive | Hard Delete |
|---|---|---|---|---|---|
| Organizations | search, filter active/inactive/archived | names, addresses, contacts, URLs, social, identifiers, child orgs, roles | ✓ | ✓ | after archive only |
| People | search, filter archived | names, contacts, addresses, social, identifiers, role assignments timeline | ✓ | ✓ | after archive only |
| Roles | search, grouped by org | org, title, notes, URLs, assignment history | ✓ | ✓ | after archive only |
| Role Assignments | filter by person/role, current/historical | person + role + dates + is_current, contacts, URLs, social, identifiers, notes | ✓ | ✓ | after archive only |

### Lookup tables

platforms, url_types, entity_identifier_types: list / create / edit / hard delete (with confirmation). No archival step.

### Import History (read-only)

- Batch list with row/loaded/error counts.
- Provenance drill-down per batch.
- Field confidence viewer per entity.

## Archive Model

Schema migration: add `archived_at TIMESTAMPTZ` to `organizations`, `people`, `roles`, `role_assignments`.

| State | Condition | Display |
|---|---|---|
| Active | `archived_at IS NULL`, `active = TRUE` (or N/A) | Normal |
| Inactive | `archived_at IS NULL`, `active = FALSE` (orgs only) | Muted badge; `active` means historical, not archived |
| Archived | `archived_at IS NOT NULL` | Strikethrough row; hidden from default list views |

- Archive sets `archived_at = NOW()`.
- Hard delete button is only rendered when `archived_at IS NOT NULL`.
- Hard delete on an archived record removes the row permanently.

## HTMX Patterns

- **Paginated lists:** `hx-get` returns `<tbody>` partial on page/filter change.
- **Search/filter:** `hx-trigger="input delay:300ms"` on search inputs.
- **Inline field edit:** click → `hx-get` returns edit form → `hx-patch` saves → returns updated display fragment.
- **Archive:** `hx-delete` with `hx-confirm` dialog.
- **Hard delete:** modal with typed confirmation before `hx-delete`.
- **Flash messages:** out-of-band `hx-swap-oob` on every mutating response; `aria-live="polite"` region.

## Style System

### Design tokens (CSS custom properties on `:root`)

```
Color:    --color-brand, --color-brand-hover
          --color-surface-{0,1,2}  (page / card / sidebar)
          --color-text, --color-text-muted, --color-text-inverse
          --color-border, --color-border-focus
          --color-success, --color-warning, --color-danger, --color-inactive

Spacing:  --space-{1–8}  (4px base × 2 scale)
Shape:    --radius-{sm,md,lg}
Type:     --font-size-{sm,md,lg,xl}
          --font-family-base  (system font stack: -apple-system, BlinkMacSystemFont, …)
```

`@media (prefers-color-scheme: dark)` overrides palette tokens — all components adapt automatically.
`@media (prefers-reduced-motion: reduce)` disables all transitions and animations.

### Layout

- CSS Grid: `sidebar (240px) + main`; collapses to stacked on narrow viewports.
- Sidebar: entity nav groups (People, Organizations, Roles, Assignments, Lookups, Import History).
- Topbar: breadcrumb + logged-in email + logout.

### Components (BEM naming)

```
.admin-layout, .admin-sidebar, .admin-main
.data-table                  — wraps in scroll container on small screens
.entity-card                 — detail view container
.badge --active/--inactive/--archived
.btn, .btn--primary, .btn--danger, .btn--ghost
.form-group                  — label + input + error message
.alert --success/--error     — flash, injected via hx-swap-oob
.modal                       — hard-delete typed confirmation
```

### Accessibility

- Skip-to-content link at top of every page.
- All interactive elements keyboard-navigable; visible `:focus-visible` ring.
- WCAG AA contrast enforced via token values.
- `aria-live="polite"` region for HTMX flash messages.
- Semantic HTML throughout (`<nav>`, `<main>`, `<section>`, `<table>` with `<caption>`).

### Internationalization

- `<html lang="en">` — easily overridden per locale.
- `[dir="rtl"]` variants for sidebar and flex layouts where order flips.
- All user-facing strings in templates; no hardcoded UI text in Python view logic.
- Dates rendered server-side in ISO 8601; no client-side locale formatting.

## Out of Scope

- Role-based access control (any authenticated exe.dev user is an admin).
- CSV import triggering from the dashboard (import remains a CLI operation).
- Data export / reporting views.
- Real-time / WebSocket updates.
