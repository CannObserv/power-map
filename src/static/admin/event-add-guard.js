/* event-add-guard.js — disable the "+ Add event" button while an unsaved
 * event row is present on the page.
 *
 * Mirrors the person-detail-add-name-guard.js pattern. The button must carry:
 *   data-events-table="<table-id>"    e.g. "org-events-table"
 *   data-new-row-id="<tr-id>"         e.g. "org-event-row-new"
 *
 * Re-sync triggers:
 *   - htmx:afterSwap on document — covers Save (new-row outerHTML swap) and
 *     tbody re-renders (archive/unarchive). Listening on document rather than
 *     the table catches outerHTML swaps that fire on the <tr>, not the table.
 *   - powerMap:newEventRowClosed on document — fired by the new-event form's
 *     inline Cancel handler, which removes the row without an HTMX round-trip.
 */
(function () {
  function init() {
    var btn = document.getElementById('add-event-btn');
    if (!btn) return;
    var table = document.getElementById(btn.dataset.eventsTable);
    var newRowId = btn.dataset.newRowId;
    if (!table || !newRowId) return;
    function sync() {
      btn.disabled = !!document.getElementById(newRowId);
    }
    document.addEventListener('htmx:afterSwap', sync);
    document.addEventListener('powerMap:newEventRowClosed', sync);
    sync();
  }
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
