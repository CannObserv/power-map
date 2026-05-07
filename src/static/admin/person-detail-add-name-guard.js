/* person-detail-add-name-guard.js — disable the "+ Add name" button
 * while an unsaved `#name-row-new` row is on the page.
 *
 * Issue #131: clicking + Add name twice without saving would prepend a
 * second `<tr id="name-row-new">`, which collides on getElementById and
 * reintroduces the typeahead-id collisions the per-row namespacing fix
 * was meant to prevent. This script keeps `btn.disabled` synced against
 * the presence of `#name-row-new` so a duplicate Add can't fire.
 *
 * Re-sync triggers:
 *   - htmx:afterSwap on `#names-table` — covers Save (tbody re-render)
 *     and Edit→Cancel (read-row outerHTML swap). Listening on the table
 *     itself, not document body, avoids handling unrelated swaps from
 *     other regions of the page.
 *   - powerMap:newNameRowClosed on document — fired by the new-name
 *     form's inline Cancel handler in `_name_form_row.html`, which
 *     removes the row directly without an HTMX round-trip. Page-wide
 *     custom events follow the same `document`-targeted convention as
 *     page-wide htmx:afterSwap listeners elsewhere in this module.
 */
(function () {
  function init() {
    var btn = document.getElementById('add-name-btn');
    var table = document.getElementById('names-table');
    if (!btn || !table) return;
    function sync() {
      btn.disabled = !!document.getElementById('name-row-new');
    }
    table.addEventListener('htmx:afterSwap', sync);
    document.addEventListener('powerMap:newNameRowClosed', sync);
    sync();
  }
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
