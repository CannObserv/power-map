# Canonical Name Edit Guard — Design

**Issue:** #74
**Date:** 2026-04-11

## Goal

Prevent `name_edit_row_post` from setting `is_canonical=FALSE` on the only canonical name, which leaves `v_person_display_names` / `v_org_display_names` returning NULL.

## Approved Approach: Reject with flash error

Before the transaction, if `is_canonical != "true"` and the existing row is currently canonical, check whether any other canonical name exists for the entity. If none, return early with a flash error: _"Cannot remove canonical — promote another name first."_

This is consistent with the existing **last-identity guard** pattern (delete blocks on last canonical name/acronym) and gives the user explicit direction.

**Correct workflow for changing which name is canonical:**
1. Edit the replacement name and check its canonical toggle (this demotes all others atomically via the existing `is_canonical == "true"` branch).
2. The old canonical name is demoted as a side-effect — no separate uncheck needed.

## Key decisions

- **Reject, don't auto-promote.** Auto-promotion is silent and surprising; the user should declare intent explicitly.
- **Guard fires only when unchecking canonical on the currently canonical row with no other canonical.** It does not block edits to non-canonical rows.
- **HTTP 200 + flash error** for HTMX path (HTMX only swaps on 2xx). Non-HTMX path: `RedirectResponse` back to the entity detail page.
- **Same logic, both entities.** `people_names.py` and `orgs_names.py` are structurally identical; fix both.

## Out of scope

- Changing the UI to hide/disable the canonical checkbox — too invasive.
- Auto-promotion logic — rejected in favor of explicit user action.
- Acronym edit routes — `orgs_acronyms.py` should be audited separately (#74 is scoped to names only).

## Testing strategy

Integration tests (both entities):
- Edit canonical name, uncheck canonical, multiple names exist → 200 + flash error, `is_canonical` unchanged.
- Edit canonical name, keep canonical checked → succeeds normally.
- Edit non-canonical name, uncheck canonical (already false) → succeeds normally.

## STYLE.md update

Document under the names editing section: edit routes must preserve the canonical invariant — if saving would leave zero canonical names, reject with flash error rather than allowing a NULL display name.
