/**
 * org-detail.js — loaded via <script src defer> in extra_head on the org detail page.
 *
 * Listens for the updateOrgHeader HTMX trigger event and updates the page heading,
 * breadcrumb trailing span, and document title without a full page reload.
 *
 * Loaded with defer so it executes after the DOM is parsed and HTMX is available.
 * Lives in <head> via the extra_head block so hx-boost never re-executes it.
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
