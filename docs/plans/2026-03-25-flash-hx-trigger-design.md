# Flash Messages via HX-Trigger — Design

**Date:** 2026-03-25
**Issue:** #32 (root cause), new issue TBD (this work)

## Goal

Eliminate the OOB-div-adjacent-to-tr parsing fragility by decoupling flash delivery from the HTMX swap target. Server sets an `HX-Trigger` response header; a JS listener injects the flash imperatively. Response bodies stay clean.

## Background

`flash.oob()` emits a bare `<div>` alongside the swap content. When the swap target is a `<tr>`, browsers strip or misplace the `<div>` before HTMX can act — invalid HTML at parse time. Wrapping in `<template>` (attempted in #31) did not resolve the issue in HTMX 1.9.12. The pattern works correctly only when the swap target is a `<div>`. This is not an HTMX bug or version gap; it is a fundamental HTML parser constraint.

## Approved Approach

### 1. Python helper — `flash_trigger(level, body)`

Location: `src/api/admin/deps.py`

Returns a dict suitable for spreading into `TemplateResponse(headers=...)`:

```python
{"HX-Trigger": json.dumps({"showFlash": {"level": level, "body": body}})}
```

Routes call this instead of passing `flash_level`/`flash_body` to template context.

### 2. `flash.js` — `src/static/admin/flash.js`

Listens for the `showFlash` custom event that HTMX dispatches when it processes the `HX-Trigger` header. Imperatively constructs the flash `<div>` and prepends it into `#flash-region`. Mirrors the auto-dismiss and hover-pause behavior of the existing `message()` macro.

Key behavioral anchors (tested in `test_js.py`):
- Listens for `showFlash` event (not a generic HTMX event)
- Guards on missing `e.detail` — prevents throwing on unrelated triggers
- Targets `#flash-region`
- Auto-dismisses via `setTimeout`
- Pauses dismiss on `mouseenter`, resumes on `mouseleave`

### 3. Template changes

- `flash.html`: retire `oob()` macro; `message()` stays for any future inline (non-HTMX) usage
- `_duplicates_region.html`: remove `{% from ... import oob %}` and `{% if flash_level %}` block
- `base.html`: add `<script src="/static/admin/flash.js?v=1" defer>` alongside existing static JS imports; remove any OOB-related inline script if present

### 4. Route migration

`orgs.py` merge and dismiss routes: replace `flash_level`/`flash_body` context vars with `headers=flash_trigger(...)` on the `TemplateResponse`.

## Testing

- `test_js.py`: structural anchors for `flash.js` (event name, null guard, `#flash-region` target, setTimeout, mouseenter/mouseleave)
- `tests/api/admin/test_orgs.py`: integration tests for merge and dismiss routes asserting `HX-Trigger` header present with correct JSON structure and `hx-swap-oob` absent from response body
- Unit test for `flash_trigger()` helper: correct key, valid JSON, correct payload shape

## Out of scope

- HTMX upgrade to 2.x (tracked in #33)
- Self-hosting HTMX (tracked in #33)
- Parent org flash (#31) — will be added after this lands; unblocked by this change
- Session-based flash for non-HTMX paths

## Key decisions

| Decision | Choice | Rationale |
|---|---|---|
| Flash HTML in JS vs. dedicated endpoint | Inline in JS | ~5 lines; endpoint is overkill |
| `message()` macro | Keep | Useful for future inline/non-HTMX flash; trivial to retain |
| Listener location | `flash.js` static file | Consistent with `admin-modal.js` pattern; enables structural pytest anchors |
| Header helper location | `deps.py` | Existing home for admin-layer utilities |
| Non-HTMX path | Unchanged `RedirectResponse` | Full page reload needs no flash header |
