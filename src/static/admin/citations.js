/* citations.js — one-at-a-time toggle for the inline citations sub-row (#319).
 *
 * A name/event row's "Cite" button opens that entity's citations panel as a
 * full-width sub-row (hx-get + hx-swap="afterend"). This makes it a toggle and
 * enforces one-open-at-a-time: before every such request, close any open
 * citations sub-row; if the button's OWN sub-row was the one open, cancel the
 * request (preventDefault on the cancelable htmx:beforeRequest) so a second
 * click closes it instead of re-fetching.
 *
 * Opt in per button with:
 *
 *     data-citations-toggle="<own-subrow-id>"   e.g. "citations-subrow-<entityId>"
 *
 * A delegated document-level listener (registered once, up front) rather than a
 * per-button inline handler — the add-row-guard.js model: hx-boost strips the
 * <head> from boosted navigation responses, so binding at load would miss
 * detail pages reached by clicking a link. dispatchEvent runs listeners
 * synchronously, so a preventDefault here lands before htmx evaluates the
 * event's result and the request is cancelled. Reference: add-row-guard.js.
 */
(function () {
  document.addEventListener('htmx:beforeRequest', function (evt) {
    var btn =
      evt.target && evt.target.closest ? evt.target.closest('[data-citations-toggle]') : null;
    if (!btn) return;
    var ownId = btn.getAttribute('data-citations-toggle');
    var wasOpen = ownId && document.getElementById(ownId);
    document.querySelectorAll('tr.citations-subrow').forEach(function (el) {
      el.remove();
    });
    if (wasOpen) evt.preventDefault(); // toggle closed → don't re-fetch
  });
})();
