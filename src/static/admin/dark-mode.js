/* Power-Map Admin — dark mode toggle
 * Reads/writes localStorage key 'pm-color-scheme'.
 * FOUC prevention is handled by an inline <script> in base.html <head>.
 * Uses document-level delegation so the handler survives HTMX boost body swaps.
 */
(function () {
  var KEY = 'pm-color-scheme';

  function isDark() {
    return document.documentElement.classList.contains('dark');
  }

  function applyTheme(dark) {
    var html = document.documentElement;
    html.classList.toggle('dark', dark);
    html.classList.toggle('light', !dark);
    localStorage.setItem(KEY, dark ? 'dark' : 'light');
    syncBtn();
  }

  document.addEventListener('click', function (e) {
    if (!e.target.closest('#theme-toggle')) return;
    applyTheme(!isDark());
  });

  /* Sync button label/icon with current theme state.
   * Called on load and after any HTMX swap (htmx:afterSettle). */
  function syncBtn() {
    var btn = document.getElementById('theme-toggle');
    if (!btn) return;
    btn.setAttribute('aria-label', isDark() ? 'Switch to light mode' : 'Switch to dark mode');
    var icon = btn.querySelector('[data-theme-icon]');
    if (icon) icon.textContent = isDark() ? '\u2600' : '\u263D';
  }
  syncBtn();
  document.addEventListener('htmx:afterSettle', syncBtn);
})();
