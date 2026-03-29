# Org Canonical Identity Guard

**Issue:** #49
**Date:** 2026-03-29

## Goal

Prevent orgs from losing all display identifiers (canonical name + canonical acronym) through admin UI deletions. Also add auto-promotion parity for acronyms.

## Approved Approach

Three targeted changes to `orgs_names.py` and `orgs_acronyms.py`:

### 1. `_maybe_promote_sole_acronym` (new, `orgs_acronyms.py`)

Mirrors `_maybe_promote_sole_name`. If the org has exactly one acronym and it is not canonical, promote it. Called inside the `acronym_delete` transaction.

### 2. Last-name guard (`name_delete`)

Before deleting, check:
- `count(all organization_names for this org) == 1` — this is the last name
- `count(canonical organization_acronyms for this org) == 0` — no acronym fallback

If both true: return a user-visible error (flash + 409 for non-HTMX). The name is not deleted.

### 3. Last-acronym guard (`acronym_delete`)

Before deleting, check:
- `count(all organization_acronyms for this org) == 1` — this is the last acronym
- `count(canonical organization_names for this org) == 0` — no name fallback

If both true: return a user-visible error. The acronym is not deleted.

## Error response pattern

- HTMX: HTTP 200, flash `error` level, no swap (empty `HTMLResponse` with flash header)
- Non-HTMX: `RedirectResponse` back to org detail (flash is not available; rely on existing page state)

## Key decisions

- Guard fires on **last item of its type** (not "last canonical") because `_maybe_promote_sole_*` runs after deletion — a non-canonical sole survivor would have been promoted, so "last name" and "last canonical name" are equivalent post-deletion.
- No DB trigger (Rec 3) — application-level guards cover the admin UI path; trigger adds migration complexity without proportional benefit.
- Ingestion pipeline gap is out of scope for this issue.

## Out of scope

- Ingestion pipeline canonical-name enforcement
- DB-level trigger (`v_org_display_names IS NOT NULL` constraint)
