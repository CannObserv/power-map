# JS Test Infrastructure Design

**Date:** 2026-04-07
**Issue:** #66

## Goal

Add minimal JS test infrastructure for admin dashboard scripts, with `role-merge.js` as the first covered file.

## Approved Approach

**Vitest + jsdom.** Minimal `package.json` at repo root — `vitest` only, no bundler. `vitest.config.js` sets `environment: 'jsdom'` and `include: ['tests/js/**/*.test.js']`. Test files mirror the existing `tests/` convention under `tests/js/`.

## Test Pattern for IIFEs

All admin JS files are IIFEs that query the DOM immediately on execution and export nothing. Testing pattern:

1. `beforeEach` — set `document.body.innerHTML` to required DOM fixture
2. `readFileSync` the script → `eval()` it in the jsdom context (listeners attach)
3. Assert DOM state or simulate events via `dispatchEvent`
4. `afterEach` — clear `document.body.innerHTML`

`eval()` is contained inside test infrastructure only. This is the standard approach for non-module IIFE scripts.

## Key Decisions

- **`tests/js/`** — test location, mirrors existing `tests/` convention alongside Python tests
- **No refactoring of IIFEs** — the IIFE pattern is correct for these scripts; tests adapt to it
- **`role-merge.js` first** — highest complexity, as specified in the issue

## `role-merge.js` Test Coverage

Priority cases:
- Checkbox cap: checking a 3rd box is rejected; unchecking one re-enables disabled boxes
- Merge mode toggle: `data-merge-mode` attribute, button text, `.merge-col` visibility
- Bar state at 0 / 1 / 2 selections: label text, button disabled state
- URL construction at 2 selections: `hx-post` attribute values on both Keep A / Keep B buttons

## Out of Scope

- `admin-modal.js` — focus trap worth testing, but separate issue
- `org-detail.js` — too trivial (18 lines, one conditional)
- `dark-mode.js` — localStorage toggle only, marginal value
- `flash.js` — timer logic is optional; not planned for this issue
