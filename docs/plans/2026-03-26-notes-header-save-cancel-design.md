# Notes Field — Lift Save/Cancel to Header Row

**Date:** 2026-03-26
**Scope:** `_notes_form.html`, `STYLE.md §15`

## Goal

Fix layout jank on the Notes inline-edit field in the Org detail screen. When the user clicks Edit, the flex header collapses (Edit button disappears, label loses its container), compressing the space between label and field.

## Approved Approach

Move Save/Cancel from `form-actions` (below the textarea) into the header row, replacing the slot where Edit sits in the read partial. Both states share the same flex header structure:

- **Read:** `[Notes h3] ··· [Edit]`
- **Edit:** `[Notes label] ··· [Save] [Cancel]`

`<form>` wraps the entire edit partial (header + textarea). `<label for="notes-textarea">` stays in the header left for screen-reader association. `form-actions` div removed.

Spacing preserved: `margin-top: var(--space-5)` on `#notes-field`, `margin-bottom: var(--space-3)` on the header div.

## Key Decisions

- **Form wraps everything** — allows Save (`type="submit"`) and Cancel (`hx-get`) to live in the header while remaining inside the form element. Valid HTML.
- **No backend changes** — routes, partials served, and swap targets are unchanged.
- **STYLE.md §15 updated** — note that Save/Cancel live in the header row, not in `form-actions`.

## Out of Scope

- People/roles Notes fields (if they exist) — separate issue
- Always-editable pattern (option b) — not chosen
