# Accessibility Full Audit — Design Doc

**Date:** 2026-03-22
**Issue:** #24 — Accessibility full audit: contrast, screen reader, keyboard nav
**Status:** Approved and implemented

## Goal

WCAG 2.1 AA compliance audit across all admin views. Fix actionable code-level gaps identified during the audit. Document remaining manual testing steps.

## Audit findings

The audit confirmed most accessibility foundations from #19 were already solid:
skip link, aria-live on HTMX targets, table `scope="col"`, aria-modal on delete dialog,
aria-current on nav links, focus-visible rings, emoji aria-hidden, dark-mode aria-label
sync. The subagent initially flagged two false positives (prefers-reduced-motion typo and
static dark-mode aria-label) that were already correct in the codebase.

## Gaps fixed

### 1. Delete modal: focus trap + restoration
**File:** `src/templates/admin/partials/delete_modal.html`

Added an inline `<script>` block that runs when the modal is HTMX-inserted:
- Captures `document.activeElement` (the delete trigger button) before moving focus
- Focuses first interactive element in the modal on open
- Tab/Shift-Tab cycle is trapped within the modal's focusable elements
- Escape key closes the modal
- On close (Cancel or after-delete), focus is restored to the original trigger

`window.__pmCloseModal` replaces the inline `onclick` / `hx-on::after-request` calls
so both paths share the same focus-restoration logic.

### 2. Form hints: aria-describedby
**Files:** `src/templates/admin/people/form.html`, `src/templates/admin/orgs/form.html`

Added `id` attributes to hint `<div>` elements and matching `aria-describedby` on inputs:
- `personal_pronouns` → `personal_pronouns-hint`
- `acronym` → `acronym-hint`
- Active checkbox → `active-hint`

Form-level error alerts (role_assignments/form.html) already use `role="alert"` which
provides immediate announcement — no additional `aria-describedby` needed since errors
are not field-specific.

### 3. HTMX loading state: aria-busy
**File:** `src/templates/admin/base.html`

Added global `htmx:beforeRequest` / `htmx:afterSettle` listeners that set/remove
`aria-busy="true"` on the HTMX swap target. Screen readers can announce the loading
state on the affected region.

### 4. Pagination disabled state: `<button disabled>`
**Files:** `src/templates/admin/macros/pagination.html`, `src/static/admin/admin.css`

Changed disabled Prev/Next from `<span>` to `<button disabled aria-disabled="true">`.
Native `disabled` attribute correctly removes the element from the tab order and
announces it as disabled. Added `.btn:disabled` CSS rule for visual consistency.

## Out of scope / manual testing checklist

These items require human testing with assistive technology and cannot be automated:

- **Contrast audit:** Run WCAG contrast checker against live pages in both light and dark
  mode. Focus areas: muted text (`--color-text-muted`), badge text, flash notification text.
- **Screen reader pass (NVDA + VoiceOver):** All list views, detail pages, and forms.
  Verify table navigation, form announcements, flash notifications, HTMX live region updates.
- **Keyboard navigation pass:** Tab order on all list + form pages. Verify skip link
  lands on `#main-content`. Verify modal focus trap feels natural. Verify sidebar mobile
  nav focus trap (already implemented in base.html JS).
- **HTMX live region testing:** Trigger search/filter/pagination updates and verify
  screen reader announces the result count change.

## Key decisions

- **Focus capture timing:** `document.activeElement` captured synchronously at modal
  script execution time (before the script moves focus). HTMX inserts content and runs
  inline scripts while focus is still on the trigger — this is reliable across tested
  browsers.
- **`window.__pmCloseModal` global:** Simple approach for sharing close logic between
  the Cancel `onclick` and the HTMX `hx-on::after-request` handler. Acceptable given
  only one modal can be open at a time.
- **`aria-busy` on HTMX target:** Chosen over `aria-busy` on `<body>` to scope
  announcements to the affected region. Polite aria-live already exists on list regions;
  `aria-busy` is complementary.
- **Form-level errors:** `role="alert"` is sufficient for form-level errors — no
  `aria-describedby` wiring needed since errors are not tied to individual fields.
