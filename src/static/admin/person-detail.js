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
  var display = e.detail && e.detail.display ? e.detail.display : '';
  var h1 = document.getElementById('page-heading');
  var crumb = document.getElementById('breadcrumb-current');
  if (h1) h1.textContent = display;
  if (crumb) crumb.textContent = display;
  if (display) document.title = display + ' \u2014 Person \u2014 Power Map';
});
