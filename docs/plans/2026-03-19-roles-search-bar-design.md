# Roles Search Bar Enhancement

**Date:** 2026-03-19

## Goal

Replace the single-field (title-only) search bar on the Roles list screen with a three-control flat row that also filters by organization name.

## Approved Approach

Flat search toolbar: `[Organization] [Title] [Status]` in the existing `search-bar` flex div.

## Key Decisions

- **Layout:** Flat row (not column-aligned). Column-aligned inputs are brittle (table column widths vary by content, collapse on mobile).
- **Org filter:** Free-text `ILIKE` on `organization_names.name` (canonical name), same debounce pattern (300ms) as the title field.
- **HTMX:** Each control's `hx-include` covers all three siblings so every interaction re-queries with the full filter state.
- **New query param:** `org_q` (keeps `q` for title to avoid breaking existing bookmarks/URLs).
- **CSS:** No changes — `search-bar` already flex-wraps; existing `min-width: 240px` on inputs is sufficient.

## Out of Scope

- Column-aligned inputs
- Autocomplete/typeahead for org search
- Rolling the same pattern to People or Orgs screens (they lack the org dimension)
