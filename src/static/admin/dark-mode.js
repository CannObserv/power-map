/* Power-Map Admin — color-scheme toggle (three-state: light / system / dark)
 * Reads/writes localStorage key 'pm-color-scheme'.
 *   'light'  → force light (html.light)
 *   'dark'   → force dark  (html.dark)
 *   absent   → follow OS prefers-color-scheme (system; no class, media query governs)
 * Clicking #theme-toggle cycles the stored preference: light → system → dark → light.
 * The "system" state is the absent key, so reaching it clears localStorage —
 * which keeps the FOUC <head> script (base.html) unchanged: absent already
 * means follow-OS there.
 *
 * The cycle is driven off the *stored* preference, not the rendered html class:
 * `system` and explicit `light` both render as light and can't be told apart
 * by class alone.
 *
 * FOUC prevention is handled by an inline <script> in base.html <head>.
 * Uses document-level delegation so the handler survives HTMX boost body swaps.
 */
(function () {
  var KEY = 'pm-color-scheme';

  /* Successor of each state in the cycle ring. */
  var NEXT = { light: 'system', system: 'dark', dark: 'light' };

  /* Button affordance per state — current-state convention: the icon shows the
   * active state; the label names it and the next action. */
  var META = {
    light: { icon: '☀', label: 'Color theme: Light. Activate for System.' },
    system: { icon: '◑', label: 'Color theme: System. Activate for Dark.' },
    dark: { icon: '☽', label: 'Color theme: Dark. Activate for Light.' },
  };

  /* Stored preference → 'light' | 'dark' | 'system'. Anything else (absent,
   * legacy, junk) means follow OS. */
  function storedState() {
    var v = localStorage.getItem(KEY);
    return v === 'light' || v === 'dark' ? v : 'system';
  }

  function applyState(state) {
    var html = document.documentElement;
    html.classList.toggle('dark', state === 'dark');
    html.classList.toggle('light', state === 'light');
    if (state === 'system') localStorage.removeItem(KEY);
    else localStorage.setItem(KEY, state);
    syncBtn();
  }

  document.addEventListener('click', function (e) {
    if (!e.target.closest('#theme-toggle')) return;
    applyState(NEXT[storedState()]);
  });

  /* Sync button icon/label with the stored preference.
   * Called on load and after any HTMX swap (htmx:afterSettle). */
  function syncBtn() {
    var btn = document.getElementById('theme-toggle');
    if (!btn) return;
    var m = META[storedState()];
    btn.setAttribute('aria-label', m.label);
    var icon = btn.querySelector('[data-theme-icon]');
    if (icon) icon.textContent = m.icon;
  }
  syncBtn();
  document.addEventListener('htmx:afterSettle', syncBtn);
})();
