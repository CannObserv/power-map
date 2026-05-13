/**
 * person-detail.js — live-update page heading, breadcrumb, and title
 * when a person's canonical name changes via HTMX inline editing.
 *
 * Listens for the updatePersonHeader custom event dispatched via HX-Trigger
 * by people_names.py mutation routes (create, edit, delete).
 *
 * Loaded via {% block extra_head %} with defer — runs after DOM parse,
 * HTMX is available, and hx-boost never re-executes head scripts.
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
