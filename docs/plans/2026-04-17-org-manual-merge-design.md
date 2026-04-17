# Org Manual Merge Design

**Date:** 2026-04-17
**Issue:** TBD

## Goal

Enable merging of organizations that are not surfaced by auto-duplicate detection (e.g. a rebrand where names differ significantly). Add a dry-run confirmation step that replaces the current `hx-confirm` dialog in the duplicates-review merge flow.

## Problem

Auto-detection uses trigram similarity > 0.85 on display names. Orgs with significantly different names (e.g. a renamed/rebranded org tracked as two separate records) are never surfaced as candidates and have no path to the merge UI.

## Approved Approach

### Entry point: "Merge with…" on org detail page

A "Merge with…" button in the org detail page's action area (alongside archive/delete). Only shown for non-archived orgs.

### Two-step modal flow

**Step 1 — Search:** Modal opens with a typeahead search for the target org. Excludes the current org and archived orgs.

**Step 2 — Preview:** Selecting a target org swaps the modal content to a preview. Shows:
- Winner/loser toggle: "Keep [Org A] / Keep [Org B]" — clicking re-fetches the preview with IDs flipped (server-rendered swap, no client-side state)
- **Names:** loser's canonical name → dropped; non-canonical names → transferred to winner as additional names
- **Acronyms:** same pattern as names
- **Counts:** roles, contacts, links, addresses, identifiers to be reassigned (e.g. "3 roles will be reassigned")
- **Conflict warning:** if both orgs have an active role with the same title, surfaced explicitly — user must resolve before merge can proceed (this is a latent bug in the existing merge SQL that would hit the unique constraint; the preview makes it visible)
- Survivor marker: winner is highlighted; loser is marked "will be permanently deleted"

Execute button POSTs merge and redirects to winner's detail page.

### Preview replaces hx-confirm

The HTMX modal preview replaces the existing `hx-confirm` dialog in the duplicates-review merge flow as well, giving consistent UX across both entry points.

## New Routes (all in `orgs_merge.py`)

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/admin/orgs/{id}/merge-search/` | Modal fragment: typeahead to find target org |
| `GET` | `/admin/orgs/{id_a}/merge-preview/{id_b}/` | Preview modal content; `?winner=<id>` controls direction; defaults to `id_a` as winner |
| `POST` | `/admin/orgs/{winner_id}/merge-with/{loser_id}/` | Executes merge; redirects to winner detail page |

The new POST route shares the merge transaction logic with the existing duplicates-page route (extracted to a shared function). The existing duplicates-page route is updated to use the preview modal instead of `hx-confirm`, but retains its inline HTMX response (updated pair list).

## New Templates

- `src/templates/admin/orgs/_merge_search_modal.html` — step 1: typeahead search
- `src/templates/admin/orgs/_merge_preview_modal.html` — step 2: winner/loser toggle, impact summary, conflict warnings, Execute/Cancel

## Out of Scope

- Merge initiation from the org list page (pagination and merge bar conflicts make this a poor fit)
- People merge equivalent (separate effort)
- Surfacing role title conflicts as auto-resolvable (user resolves manually before merge)
