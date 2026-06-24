/* person-detail-add-name-guard.js — disable the "+ Add name" button
 * while an unsaved `#name-row-new` row is on the page.
 *
 * Issue #131: clicking + Add name twice without saving would prepend a
 * second `<tr id="name-row-new">`, which collides on getElementById and
 * reintroduces the typeahead-id collisions the per-row namespacing fix
 * was meant to prevent. This script keeps `btn.disabled` synced against
 * the presence of `#name-row-new` so a duplicate Add can't fire.
 *
 * Loaded site-wide from base.html <head> (#237). hx-boost strips the <head>
 * from boosted navigation responses, so a script that bound to #names-table
 * at load would never run when the person detail page is reached by clicking
 * a link. Instead the document-level listeners are registered once, up front,
 * and sync() re-resolves the button on every call — a no-op on pages without
 * an add-name button, activating once a boosted navigation swaps one in.
 *
 * Re-sync triggers (all on document, registered once):
 *   - htmx:afterSwap — covers Save (tbody re-render) and Edit→Cancel
 *     (read-row outerHTML swap). Document-scoped (like event-add-guard.js):
 *     #name-row-new is unique page-wide, so a global id check is correct and
 *     reacting to unrelated swaps is a harmless idempotent re-check.
 *   - htmx:load — covers the boosted navigation that first renders the button.
 *   - powerMap:newNameRowClosed — fired by the new-name form's inline Cancel
 *     handler in `_name_form_row.html`, which removes the row without an HTMX
 *     round-trip.
 */
(function () {
  function sync() {
    var btn = document.getElementById('add-name-btn');
    var table = document.getElementById('names-table');
    if (!btn || !table) return;
    btn.disabled = !!document.getElementById('name-row-new');
  }
  document.addEventListener('htmx:afterSwap', sync);
  document.addEventListener('htmx:load', sync);
  document.addEventListener('powerMap:newNameRowClosed', sync);
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', sync);
  } else {
    sync();
  }
})();
