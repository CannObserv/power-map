/**
 * jurisdiction-detail.js — loaded site-wide via <script src defer> in base.html.
 *
 * Listens for the updateJurisdictionHeader HTMX trigger event and updates the
 * page heading, breadcrumb trailing span, and document title in place after an
 * inline details save (#275), without a full page reload.
 *
 * Mirrors org-detail.js. Must load from base.html, NOT a detail-page extra_head
 * block: hx-boost strips <head> from boosted navigation responses, so an
 * extra_head script never runs when the page is reached by clicking a link
 * (#237). Registered once on document; a no-op on pages that never dispatch the
 * event.
 */
document.addEventListener('updateJurisdictionHeader', function (e) {
  var d = e.detail && e.detail.display;
  if (!d) return;
  var h1 = document.getElementById('page-heading');
  var bc = document.getElementById('breadcrumb-current');
  if (h1) h1.textContent = d;
  if (bc) bc.textContent = d;
  document.title = d + ' — Jurisdiction — Power Map';
});
