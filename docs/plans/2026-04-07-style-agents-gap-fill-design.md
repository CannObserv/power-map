# Design: STYLE.md / AGENTS.md Gap Fill

**Date:** 2026-04-07
**Status:** Approved

## Goal

Document patterns established in the Organization list and detail screens that are absent from STYLE.md and AGENTS.md. Ensures future agents and contributors can replicate these patterns consistently without reading implementation code.

## Approved Approach

Audit identified 10 undocumented patterns. All except #9 (address confirmation modal) are in scope.

Changes split across two files:
- **STYLE.md** — new sections for reusable UI patterns
- **AGENTS.md** — cross-reference pointers to STYLE.md sections; no duplication of content

## Scope

### New STYLE.md sections

**§16 → §17 renumber** — "Confirmation Modals" stays §16; new sections follow.

| § | Pattern | Priority |
|---|---|---|
| §17 | Page header pattern (list vs. detail variants) | High |
| §18 | Typeahead / combobox | High |
| §19 | Empty-state table rows | High |
| §20 | Toggle in inline form rows (non-auto-save) | High |
| §21 | Section-level add button (h2-level, outside entity-card) | Medium |
| §22 | Metadata footer | Medium |
| §23 | `hx-push-url` on list filters | Medium |
| §24 | Merge bar pattern | Medium |
| §25 | Clipboard copy button | Low |

### AGENTS.md cross-references

Add `→ STYLE.md §N` pointers after each of these inline descriptions:
- Row-level HTMX editing pattern → §15 (entity card subsection) + §20 (toggle in form rows)
- Notes inline edit pattern → §15
- Auto-saving toggle pattern → §15
- Child org scoped search → §18

## Out of Scope

- §9 (address confirmation modal / multi-step form) — deferred
- CSS implementation changes — documentation only
- JS implementation changes — documentation only
- Any new UI patterns not already in the codebase

## Key Decisions

- Content in STYLE.md; AGENTS.md only adds short `→ STYLE.md §N` pointers
- Typeahead section documents the full ARIA + JS contract so it can be re-implemented on any new screen without reading `_parent_form.html`
- Merge bar section describes the `role-merge.js` interface (data attributes, DOM IDs) so it can be reused on future list screens
