# Org Detail UI — Round 2 Design

**Date:** 2026-03-23
**Reference:** WSLCB org record as test case

---

## Goal

Fix four UI issues on the Organization detail screen: sidebar contrast bugs, missing `btn--secondary` style, core-fields card restructure, and inline edit form class mismatch.

---

## Approved Changes

### 1. Sidebar — adaptive light/dark background

**Problem:** Sidebar uses `--color-surface-2` which is hardcoded dark in all modes, causing two bugs:
- Light mode hover: global `a:hover { color: var(--color-brand-hover) }` bleeds into sublinks (dark purple on dark sidebar)
- Dark mode default: `.admin-sidebar__sublink` uses `color: var(--color-text-inverse)` which resolves to near-black on a near-black sidebar — invisible until hover

**Decision:** Make sidebar white in light mode and dark in dark mode. Eliminates both bugs via correct token semantics rather than per-case hacks.

**New tokens** (defined in all four color contexts: `:root`, `html.dark`, `html.light`, `@media prefers-color-scheme: dark`):

| Token | Light | Dark |
|---|---|---|
| `--color-sidebar-bg` | `#ffffff` | `#0f172a` |
| `--color-sidebar-text` | `#64748b` | `#cbd5e1` |
| `--color-sidebar-text-active` | `#6d4488` | `#ffffff` |
| `--color-sidebar-hover-bg` | `#f5f0f8` | `#ffffff14` |

**CSS changes:**
- `.admin-sidebar`: `background: var(--color-sidebar-bg)` + `border-inline-end: 1px solid var(--color-border)` (provides visual separation on white; subtle on dark)
- `.admin-sidebar__link`: `color: var(--color-sidebar-text)`; hover/active: `background: var(--color-sidebar-hover-bg); color: var(--color-sidebar-text-active)`
- `.admin-sidebar__sublink`: `color: var(--color-sidebar-text)`; hover/active: `background: var(--color-sidebar-hover-bg); color: var(--color-sidebar-text-active)`

**Out of scope:** `--color-surface-2` retained as-is for any future use; sidebar now uses `--color-sidebar-bg` instead.

---

### 2. `btn--secondary` — new button variant

**Problem:** `btn--secondary` is referenced throughout the codebase (Edit button, all "+ Add" buttons) but never defined in CSS. Browser renders default grey button styles.

**Decision:** Subtle brand-tinted fill — between `btn--ghost` (transparent) and `btn--primary` (solid brand) in visual weight.

```css
.btn--secondary { background: var(--color-brand-subtle); color: var(--color-brand); border-color: var(--color-brand-subtle-border); }
.btn--secondary:hover { background: var(--color-brand-subtle-border); color: var(--color-brand-hover); border-color: var(--color-brand-subtle-border); }
```

Token-based; adapts automatically to light/dark mode.

---

### 3. Core-fields card restructure

**Problems:**
- Edit button at bottom-left of card with no styling (undefined `btn--secondary`)
- Status (`Active: Yes/No`) shown as a row, duplicating the badge in the `<h1>` header
- Card has no section heading, inconsistent with other sections on the page

**Decisions:**

**Structure:** Wrap core-fields in `<section class="entity-section">` with `<h2>Details</h2>` above the card — consistent with Names, Addresses, etc.

**Edit button:** Upper right of card via new `.entity-card__actions` CSS class:
```css
.entity-card__actions { display: flex; justify-content: flex-end; margin-bottom: var(--space-3); }
```
Button uses `btn--secondary btn--sm`.

**Status row:** Remove `<dt>Active</dt><dd>Yes/No</dd>` from `_core_fields_read.html`. Status is already conveyed by the badge in `<h1>`. The `active` checkbox remains in the edit form (`_core_fields_form.html`).

---

### 4. Inline edit form class fix (separate commit)

**Problem:** `_core_fields_form.html` uses `field-group` (undefined) instead of `form-group` (defined, with `min-height: 44px`, focus ring, etc.).

**Decision:** Replace all `field-group` with `form-group`. Checkbox label already wrapped correctly for `form-group label:has(input[type=checkbox])` touch target rule. Committed separately so it can be reverted if the `form-group` sizing causes layout issues in the card context.

---

---

### Amendment: Details section — row-level-everywhere

**Original design** had a card-level Edit toggle for the Details card. **Revised** during implementation after recognising that mixing row-level HTMX (immediate, persistent) with a card-level buffered Save creates incoherent Cancel semantics.

**Decision:** No card-level toggle. Everything in the Details section uses row-level or field-level editing:

- **Names table** — row-level HTMX (existing pattern via `orgs_names.py`), moved from standalone section into Details card
- **Acronyms table** — new, same row-level pattern; new `orgs_acronyms.py` router
- **Active** — toggle checkbox with `hx-post` on change, auto-saves immediately; no Cancel needed
- **Notes** — inline field-level edit: read partial → edit form → save returns read partial

**Removed:** `inline/core/` routes (name/acronym handled by row tables; active/notes have dedicated field routes). `_core_fields_read.html` and `_core_fields_form.html` obsoleted.

**Standalone Names section removed** from `detail.html` — consolidated into Details card.

---

## Out of Scope

- STYLE.md updates — deferred to end of work session
- Row-level inline edit form standardization (addresses, contacts, links, identifiers)
- Other detail pages (people, roles, role-assignments)
