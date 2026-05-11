---
date: 2026-05-11
topic: name-editor follow-ups
status: planned
issues: [126, 128, 130, 139]
closed_in_fact: [129]
---

# Name-editor follow-ups — prioritized backlog clearance

## Goal

Clear the open follow-up issues that accumulated against the person-name editor after #123 (i18n) and #127 (editor redesign) shipped. Four issues across one parallel batch and two sequential single-agent batches, with one issue (#129) confirmed closed-in-fact by prior work and removed from scope.

## Approved approach

- Hybrid parallelism: parallel agents within a batch on disjoint files; sequential gates between batches where files contend.
- Pre-production deployment context: refactors and footguns land freely; correctness fixes still lead their own slot but don't dominate sequencing.
- Three-dimension rubric, default weights.
- One critical-path chain on `_name_parts_editor.html`: **#128 → #126 → #139**. One isolated parallel issue (#130) pairs with #128 in Batch A.
- Regular merge commits (preserves per-agent history; matches `Merge batch/2026-05-09-a` precedent).

## Prioritization rubrics

| Dimension | 1 | 2 | 3 |
|---|---|---|---|
| **Foundation Leverage** | Standalone improvement | 1–2 other issues benefit | Multiple issues depend on or are simplified by this |
| **Correctness Risk** | Cosmetic / organizational | Edge-case incorrect behavior, runtime failure risk | Data loss, race conditions, silent failures |
| **Scope Clarity** | Requires design discovery | Clear direction, minor decisions needed | Mechanical — implementation is obvious from the issue |

**Formula:** `Score = (Foundation × 2) + (Correctness × 2) + Scope`, max 15.
**Blast radius** (files touched across issues) drives *sequencing*, not score.

## Scored backlog

| # | Title | F×2 | C×2 | S | **Score** | Blast |
|---|-------|:---:|:---:|:---:|:---:|:---:|
| **#129** | Scope typeahead ids per-form (multi-row edit safety) | 2 | 6 | 2 | **10** | — (closed-in-fact, see below) |
| **#128** | DRY `ARRAY_CAP` through template context | 4 | 2 | 3 | **9** | Med |
| **#130** | E2E test for new-name metadata POST | 2 | 4 | 3 | **9** | Low |
| **#139** | "Suggest decomposition" button on name editor | 4 | 4 | 1 | **9** | High |
| **#126** | Reorder arrows for `person_name_parts` arrays | 2 | 2 | 3 | **7** | Low |

### Closed-in-fact: #129

All element ids called out in the issue body — `locale-search-display`, `locale-hidden`, `script-search-display`, `script-hidden`, `reading-of-display`, `reading-of-hidden`, and all three `*-results` listboxes — are already `{{ _uid }}`-suffixed in [src/templates/admin/people/partials/_name_metadata_fields.html](../../src/templates/admin/people/partials/_name_metadata_fields.html). The file's own header comment credits **issue #131** for the fix. Regression coverage exists at [tests/js/typeahead-row-key-collision.test.js](../../tests/js/typeahead-row-key-collision.test.js) and [tests/js/person-name-row-typeahead.test.js](../../tests/js/person-name-row-typeahead.test.js). The issue should be closed with a comment pointing at #131.

## Conflict zones

| File | Touched by | Required merge order |
|---|---|---|
| `src/templates/admin/people/partials/_name_parts_editor.html` | **#128, #126, #139** | #128 (1-line cap interpolation) → #126 (arrow buttons in array rows) → #139 (Suggest button near parts subheading; likely adds HTMX wrapper) |
| `tests/api/admin/test_people_name_templates.py` | #128 (assert), #126 (optional) | small assertions; either order — separated by batch gate anyway |
| `src/templates/admin/people/partials/_name_form_row.html` | #139 only | n/a |
| `tests/api/admin/test_people_names.py` | #130 only | n/a |

**Why this order on `_name_parts_editor.html`:**
- **#128 first** — mechanical substitution `data-cardstack-cap="5"` → `data-cardstack-cap="{{ ARRAY_CAP }}"`. Mechanical refactors should land before structural additions.
- **#126 second** — adds ↑/↓ buttons inside each `data-cardstack` array row. Structural UX addition.
- **#139 last** — Suggest button near the parts subheading, likely needs an HTMX-target wrapper around the parts block, and is the largest/most-design-ambiguous change. Inherits final structure.

## Dependency graph

```
#130 (test-only) ──────────────────────────────────► no gates, parallel-safe with everything

#128 ──► #126 ──► #139
(cap   (arrow    (Suggest button + endpoint)
 line)  buttons)

Critical path: #128 → #126 → #139  (all on _name_parts_editor.html)
```

## Batch execution plan

| Batch | Issues | Agents | Branch | Gate |
|---|---|---|---|---|
| **A** | #128, #130 | 2 (parallel) | `batch/2026-05-11-a` | Start immediately |
| **B** | #126 | 1 | `feature/126-reorder-buttons` | After A merged to `main` |
| **C** | #139 | 1 | `feature/139-suggest-decomposition` | After B merged to `main` |

### Batch A — files & scope

**A1 — #128 (DRY `ARRAY_CAP`)**
- `src/api/admin/people_name_parts.py` — no change to source; `ARRAY_CAP = 5` is the existing single Python truth
- `src/api/admin/assets.py` (or `_names_shared.py`) — expose `ARRAY_CAP` to Jinja `env.globals` alongside the existing `asset_version` pattern at [src/api/admin/assets.py:63](../../src/api/admin/assets.py#L63)
- `src/templates/admin/people/partials/_name_parts_editor.html:96` — interpolate `{{ ARRAY_CAP }}`
- `src/static/admin/person-name-parts-cardstack.js` — drop the `|| 5` fallback; require the data attribute (fail loud)
- `tests/api/admin/test_people_name_templates.py` — assert rendered `data-cardstack-cap` matches `ARRAY_CAP`

**A2 — #130 (E2E new-name metadata test)**
- `tests/api/admin/test_people_names.py` — one new `@pytest.mark.integration` test that POSTs `/admin/people/{pid}/names/` with `visibility=legal_only`, `locale=es-MX`, `script=Latn`, `sort_as` set, and asserts the row was written with those values. Recipe is in the issue body verbatim.

### Batch B — files & scope

**B1 — #126 (reorder arrows)**
- `src/templates/admin/people/partials/_name_parts_editor.html` — add `<button type="button">` ↑/↓ pair next to each input inside each `data-cardstack` array row; disabled-state when no neighbor
- `src/static/admin/person-name-parts-reorder.js` (new) — click handler that swaps adjacent input `value`s
- `tests/js/person-name-parts-reorder.test.js` (new) — vitest swap-logic + disabled-state edges. Follow STYLE.md §33 (vi.fn / vi.spyOn).
- Optional: `tests/api/admin/test_people_name_templates.py` — assert arrow markup is present on each row.
- Server contract unchanged: `upsert_or_delete_parts` already reads inputs in document order.

### Batch C — files & scope

**C1 — #139 (Suggest decomposition)**
- New endpoint: `GET /admin/people/{person_id}/names/{name_id}/suggest-parts/` (most natural home: `src/api/admin/people_names.py` or a sibling under `src/api/admin/`)
- New template: `src/templates/admin/people/partials/_name_parts_suggestion.html`
- `src/templates/admin/people/partials/_name_parts_editor.html` — add the "Suggest decomposition" button near the parts subheading; HTMX target the parts block; likely needs a wrapper `<div id="...">` so the returned partial can replace pre-fill content
- `src/templates/admin/people/partials/_name_form_row.html` — minor: pass any new context required by the suggestion endpoint (e.g. `name_id` already available)
- New unit + integration + template tests
- Wires `src.core.normalizers.person_name.suggest_parts(...)` to the UI. Suggest-only — `upsert_or_delete_parts` stays single writer.
- Edge cases to design during agent work: empty name, NULL script, existing parts overwrite confirm, honorific extraction pre-fill, hide button for `name_type ∈ {initials, mrz, reading, romanization}`.

## Key decisions

- **#129 removed pre-batch** — verified closed-in-fact during conflict analysis. Pattern to keep using: grep contested symbols *before* presenting the score table (per 2026-05-09 session log learning #3).
- **Three sequential commits on `_name_parts_editor.html`, not one bundled agent.** Bundling #128 + #126 + #139 in one agent saves orchestration but creates heterogeneous review (mechanical refactor + UX affordance + design-heavy UX with new endpoint). Three batches give the user three clean review surfaces and contain failure: if #139 stalls during design discovery, #126 has already landed.
- **#130 paired with #128 in Batch A.** Pure test-only issue; running it alongside the mechanical refactor maximizes Batch A's value (two issues land instead of one). Zero file overlap with A1.
- **Regular merge commits** — matches `Merge batch/2026-05-09-a — ...` precedent in recent history.

## Deferred items

None. All five originally-listed issues are accounted for: four batched, one closed-in-fact.

## Out of scope

- Drag-and-drop reordering for `person_name_parts` (#126 ships button-based; drag-drop remains YAGNI per parent #123 design doc).
- Refactoring `initTypeaheadCombobox` to take a root element instead of element ids (option (b) in #129 body). Closed-in-fact via id-suffix approach (option (a)) — no remaining work.
- Per-locale CLDR Constants overlay for `suggest_parts` (#139 issue body — defer until first non-en-US Latin locale ingestion).
- `chinese-names` / Hant-Hans script support (#139 issue body — defer until first non-Latn ingestion).
- Surfacing nameparser's `nickname` field as auto-created variant rows (#139 issue body — operator-manual until needed).
