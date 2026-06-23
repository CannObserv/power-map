/**
 * person-detail.js — live-update page heading, breadcrumb, and title
 * when a person's canonical name changes via HTMX inline editing.
 *
 * Listens for the updatePersonHeader custom event dispatched via HX-Trigger
 * by people_names.py mutation routes (create, edit, delete).
 *
 * Loaded site-wide via base.html <head> with defer — runs after DOM parse and
 * once HTMX is available. Must NOT live in a detail-page extra_head block:
 * hx-boost strips the <head> from boosted navigation responses, so the listener
 * would never register when the page is reached by clicking a link (#237).
 * Registered once on document, it is a no-op where updatePersonHeader is never
 * dispatched.
 */
document.addEventListener('updatePersonHeader', function (e) {
  var d = e.detail && e.detail.display;
  if (!d) return;
  var h1 = document.getElementById('page-heading');
  var bc = document.getElementById('breadcrumb-current');
  if (h1) h1.textContent = d;
  if (bc) bc.textContent = d;
  document.title = d + ' \u2014 Person \u2014 Power Map';
});
