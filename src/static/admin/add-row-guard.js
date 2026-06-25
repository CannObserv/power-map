/* add-row-guard.js — disable a "+ Add …" button while its own unsaved
 * new-row is on the page.
 *
 * Issue #131 / #238: clicking a "+ Add" button twice without saving prepends a
 * second `<tr id="<entity>-row-new">`, colliding on getElementById and
 * reintroducing the per-row typeahead id-collisions the namespacing fixes were
 * meant to prevent. Each guarded button opts in with
 *
 *     data-new-row-id="<tr-id>"    e.g. "name-row-new", "acronym-row-new"
 *
 * and is disabled iff `getElementById(<that id>)` resolves. sync() scans
 * `button[data-new-row-id]`, so a page with many add-buttons (org detail:
 * names, acronyms, contacts, addresses, links, identifiers, roles, …) guards
 * each independently — the capability the earlier single-button-by-id guards
 * (person-detail-add-name-guard.js, event-add-guard.js, both retired by #238)
 * could not provide.
 *
 * Scope: this guard owns only the "stay disabled while an unsaved row exists"
 * invariant (the second-deliberate-click case) and is the SOLE writer of the
 * button's `disabled`. The other race — a second request fired while the first
 * is still in flight (before any row exists) — belongs to htmx and is handled
 * declaratively by `hx-sync="this:drop"` on each button. `hx-disabled-elt` is
 * deliberately NOT used: htmx re-enables it after the swap, clobbering this
 * guard's disable (#238 CR / STYLE.md §32).
 *
 * Loaded site-wide from base.html <head> (#237). hx-boost strips the <head>
 * from boosted navigation responses, so a script that bound to its elements at
 * load would never run when a detail page is reached by clicking a link.
 * Instead the document-level listeners are registered once, up front, and
 * sync() re-resolves buttons on every call — a no-op on pages without any
 * guarded button, activating once a boosted navigation swaps one in.
 *
 * Re-sync triggers (all on document, registered once):
 *   - htmx:afterSwap — covers the add itself (afterbegin new-row swap), Save
 *     (tbody re-render) and Edit→Cancel (read-row outerHTML swap). Each
 *     `<entity>-row-new` is unique page-wide, so a global id check is correct
 *     and reacting to unrelated swaps is a harmless idempotent re-check.
 *   - htmx:load — covers the boosted navigation that first renders the buttons.
 *   - powerMap:newRowClosed — fired by each new-row form's inline Cancel
 *     handler, which removes the row without an HTMX round-trip.
 */
(function () {
  function sync() {
    var btns = document.querySelectorAll('button[data-new-row-id]');
    for (var i = 0; i < btns.length; i++) {
      var btn = btns[i];
      btn.disabled = !!document.getElementById(btn.dataset.newRowId);
    }
  }
  document.addEventListener('htmx:afterSwap', sync);
  document.addEventListener('htmx:load', sync);
  document.addEventListener('powerMap:newRowClosed', sync);
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', sync);
  } else {
    sync();
  }
})();
