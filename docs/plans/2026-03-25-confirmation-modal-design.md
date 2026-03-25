# Confirmation Modal — Design Doc

**Date:** 2026-03-25
**Status:** Approved

---

## Goal

Replace browser-native `window.confirm()` dialogs (triggered by HTMX's `hx-confirm` attribute) with a styled, accessible in-page confirmation modal. Establish this as the project-wide pattern for all destructive confirmation prompts.

---

## Approved Approach: Global `htmx:confirm` override

A single JS file (`admin-modal.js`) intercepts the `htmx:confirm` event, suppresses the browser dialog, and renders a custom modal using the existing `.modal-backdrop` / `.modal` CSS. On user confirmation it calls `event.detail.issueRequest()` to proceed with the original HTMX request.

---

## Author-facing API

Declarative — no JS at the call site:

```html
<button hx-post="..."
        hx-confirm="Remove parent organization?"
        data-confirm-label="Unlink">
  Unlink
</button>
```

| Attribute | Required | Default | Purpose |
|---|---|---|---|
| `hx-confirm` | yes | — | Modal message body text |
| `data-confirm-title` | no | `"Are you sure?"` | Modal heading (`<h2>`) |
| `data-confirm-label` | no | `"Confirm"` | Confirm button text |
| `data-confirm-variant` | no | `"danger"` | Confirm button variant (`danger` or `primary`) |

**Default variant is `danger`** — confirmations in this admin are almost exclusively destructive. Non-destructive prompts opt in to `primary`.

---

## Implementation

### New file: `src/static/admin/js/admin-modal.js`

Behavior:
1. Listen on `document` for `htmx:confirm`
2. `event.preventDefault()` — suppresses browser `confirm()`
3. Build modal DOM from existing CSS classes (no new styles)
4. Append to `document.body`; focus Cancel button (safe default — avoids accidental confirm on Enter)
5. Tab / Shift-Tab trapped within modal focusable elements
6. Escape / Cancel: remove modal, restore focus to trigger
7. Confirm: remove modal, restore focus to trigger, call `event.detail.issueRequest()`

Uses a closure-scoped `close()` — does not touch `window.__pmCloseModal` (which belongs to the delete modal).

### CSS changes

None. `.modal-backdrop`, `.modal`, `.modal__actions`, and `.btn` already exist in `admin.css`.

### `base.html` change

Add one line before `</body>`:
```html
<script src="/static/admin/js/admin-modal.js" defer></script>
```

### `_parent_form.html` change

Add `data-confirm-label="Unlink"` to the Unlink button. The existing `hx-confirm` attribute stays as-is.

---

## Accessibility

Mirrors the `delete_modal.html` pattern (STYLE.md §12):
- Capture `document.activeElement` (trigger) before opening
- On open: focus first focusable element (Cancel)
- Tab / Shift-Tab: cycle within modal focusable elements
- Escape: close, restore focus to trigger
- Cancel / Confirm: close, restore focus to trigger

---

## Testing

- Template test in `tests/api/admin/test_orgs_templates.py`: assert Unlink button has `hx-confirm` and `data-confirm-label="Unlink"`
- No new Python routes → no new integration tests
- JS has no pytest coverage (consistent with project conventions)

---

## Out of scope

- Refactoring `delete_modal.html` to use this pattern (it has additional logic: inline error display, HTTP 409 handling, `window.__pmHandleDeleteResult` callback)
- Native `<dialog>` element migration
- STYLE.md §16 update (deferred to end of session per project convention)
