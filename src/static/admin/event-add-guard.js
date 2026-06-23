/* event-add-guard.js — disable the "+ Add event" button while an unsaved
 * event row is present on the page.
 *
 * Mirrors the person-detail-add-name-guard.js pattern. The button must carry:
 *   data-events-table="<table-id>"    e.g. "org-events-table"
 *   data-new-row-id="<tr-id>"         e.g. "org-event-row-new"
 *
 * Loaded site-wide from base.html <head> (#237). hx-boost strips the <head>
 * from boosted navigation responses, so a script that only ran when its
 * elements existed at load would never execute when the detail page is reached
 * by clicking a link. Instead this registers its document-level listeners once,
 * up front, and sync() re-resolves the button on every call — so it is a no-op
 * on pages without an events table and activates automatically once a boosted
 * navigation swaps one in.
 *
 * Re-sync triggers (all on document, registered once):
 *   - htmx:afterSwap — covers Save (new-row outerHTML swap) and tbody
 *     re-renders (archive/unarchive). Listening on document rather than the
 *     table catches outerHTML swaps that fire on the <tr>, not the table.
 *   - htmx:load — covers the boosted navigation that first renders the button.
 *   - powerMap:newEventRowClosed — fired by the new-event form's inline Cancel
 *     handler, which removes the row without an HTMX round-trip.
 */
(function () {
  function sync() {
    var btn = document.getElementById('add-event-btn');
    if (!btn) return;
    var table = document.getElementById(btn.dataset.eventsTable);
    var newRowId = btn.dataset.newRowId;
    if (!table || !newRowId) return;
    btn.disabled = !!document.getElementById(newRowId);
  }
  document.addEventListener('htmx:afterSwap', sync);
  document.addEventListener('htmx:load', sync);
  document.addEventListener('powerMap:newEventRowClosed', sync);
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', sync);
  } else {
    sync();
  }
})();
