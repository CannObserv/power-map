# Org Detail — Header, Title, and Breadcrumb Sync on Name/Acronym Mutation

**Date:** 2026-03-29

## Goal

When a canonical name or canonical acronym is created, edited, or deleted on the Organization Detail screen, the `<h1>` page heading, HTML `<title>`, and breadcrumb trailing `<span>` should update in-place without a full page reload.

## Context

The detail page computes `_display` at render time:

```jinja
{% set _display = (names[0].name if names else (acronyms[0].acronym if acronyms else None)) %}
```

`names[0]` is the canonical name (ORDER BY `is_canonical DESC`). The HTMX inline-edit rows swap only the `<tbody>` contents, leaving the already-rendered `<h1>`, `<title>`, and breadcrumb stale.

`v_org_display_names` returns the authoritative display name (canonical name → canonical acronym → NULL). Since #49, orgs are guaranteed to always have at least one display identifier (either a canonical name or a canonical acronym), so NULL from the view is an edge case (multiple names, none canonical) rather than a routine state.

## Approved Approach — HX-Trigger + JS listener

After any name or acronym mutation, query `v_org_display_names` for the new display name and emit an `updateOrgHeader` event alongside the existing `showFlash` event in the `HX-Trigger` response header.

A JS listener in `<head>` handles `updateOrgHeader` and updates three DOM targets:
- `document.title`
- `#page-heading` — the `<h1>`
- `#breadcrumb-current` — the trailing `<span>` in the breadcrumb

### Key decisions

**Why HX-Trigger + JS over HTMX OOB swap?**
`document.title` cannot be updated by HTMX OOB swap — it requires JS regardless. Using the same `HX-Trigger` mechanism for all three targets keeps the implementation uniform and avoids mixing two update paths.

**Why query `v_org_display_names`?**
The view already encodes the canonical name → canonical acronym → NULL fallback and is authoritative. Re-deriving the logic in application code would duplicate it.

**Fallback value:** `display_name or org.id` — consistent with the existing template.

**Listener placement:** `<head>` block override in `detail.html` (not base). Same named-function guard pattern as `flash.js` to survive hx-boost re-execution (`document.removeEventListener` / re-assign / `document.addEventListener`).

## Scope

### Backend
- `flash_trigger` in `deps.py` — add optional `extra: dict` param merged into the trigger JSON
- 6 mutation routes query `v_org_display_names` after the DB write and emit `updateOrgHeader`:
  - `orgs_names.py`: `name_create`, `name_edit_row_post`, `name_delete`
  - `orgs_acronyms.py`: `acronym_create`, `acronym_edit_row_post`, `acronym_delete`

### Templates
- `detail.html`:
  - Add `id="page-heading"` to the `<h1>`
  - Add `id="breadcrumb-current"` to the trailing breadcrumb `<span>`
  - Add `updateOrgHeader` JS listener in a `{% block extra_head %}` override

### Tests
- Unit test for `flash_trigger` with `extra` param
- Integration tests: after each mutation route, assert the `HX-Trigger` header contains `updateOrgHeader` with the correct display name

## Out of Scope

- Acronym delete currently returns a bare `HTMLResponse("")` — the route will be updated to return a proper `TemplateResponse` with the full trigger header, consistent with the other mutation routes
- Breadcrumb links (Dashboard, Organizations) — static, not affected
- People or Roles detail pages — separate concern
