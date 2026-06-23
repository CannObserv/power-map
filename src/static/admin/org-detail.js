/**
 * org-detail.js — loaded site-wide via <script src defer> in base.html <head>.
 *
 * Listens for the updateOrgHeader HTMX trigger event and updates the page heading,
 * breadcrumb trailing span, and document title without a full page reload.
 *
 * Loaded with defer so it executes after the DOM is parsed and HTMX is available.
 * Must load from base.html, NOT a detail-page extra_head block: hx-boost strips
 * the <head> from boosted navigation responses, so an extra_head script never
 * runs when the page is reached by clicking a link, and this listener would
 * never register (#237). Registered once at first full load on document, it is
 * a no-op on pages that never dispatch updateOrgHeader.
 */
document.addEventListener('updateOrgHeader', function (e) {
  var d = e.detail && e.detail.display;
  if (!d) return;
  var h1 = document.getElementById('page-heading');
  var bc = document.getElementById('breadcrumb-current');
  if (h1) h1.textContent = d;
  if (bc) bc.textContent = d;
  document.title = d + ' \u2014 Organization \u2014 Power Map';
});
