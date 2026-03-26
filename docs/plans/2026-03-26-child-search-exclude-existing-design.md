# Child Org Search — Exclude Existing Children

**Date:** 2026-03-26
**Scope:** `orgs.py`, `_child_form_row.html`, tests

## Goal

When using "Add child" on the Org detail screen, the search typeahead currently returns orgs that are already children of the current org. These should be excluded so the user cannot accidentally re-link an already-linked child.

## Approved Approach

Add a scoped search endpoint `GET /{org_id}/children/search/?q=...` that excludes:
- `o.id = org_id` (self — already rejected at POST; cleaner to exclude from typeahead)
- `o.parent_id = org_id` (orgs already linked as children of this org)

`_child_form_row.html` changes its `hx-get` from the generic `/admin/orgs/search/` to the new `/admin/orgs/{org_id}/children/search/`. Generic `/search/` is untouched.

## Key Decisions

- **New route, not a shared param** — keeps generic `/search/` clean; child-specific exclusion logic is isolated to a child-specific endpoint.
- **Exclude self in typeahead** — consistent with the POST guard; avoids a confusing result that would 422 on submit.
- **No JS or CSS changes** — the `<ul>` structure, dropdown behaviour, and `selectItem` logic are unchanged.

## Out of Scope

- Circular-hierarchy detection (e.g. excluding descendant orgs)
- Disabling rather than excluding results
