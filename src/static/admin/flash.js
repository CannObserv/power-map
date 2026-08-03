/* flash.js — showFlash event listener for HX-Trigger-based flash messages.
 *
 * HTMX dispatches a custom DOM event named "showFlash" when it processes an
 * HX-Trigger response header containing {"showFlash": {"level": "...", "body": "..."}}.
 * This listener catches that event and injects a flash notification into #flash-region.
 */

/* Strip a consumed ?flash= param from the URL on load (#379).
 *
 * A full-page navigation that lands on a flash-bearing URL — the HX-Redirect
 * delete→list landing (#376) or a non-HTMX 303 fallback — leaves ?flash=<key>
 * in the address bar. The flash is already server-rendered into #flash-region,
 * so removing the param here only stops a manual refresh from re-showing it.
 * Boosted navigations are handled server-side by HX-Replace-Url (htmx honors
 * that header only on htmx-initiated requests), so this covers the hard-nav gap.
 */
(function () {
  try {
    var url = new URL(window.location.href);
    if (!url.searchParams.has('flash')) return;
    url.searchParams.delete('flash');
    var qs = url.searchParams.toString();
    window.history.replaceState(
      window.history.state,
      '',
      url.pathname + (qs ? '?' + qs : '') + url.hash,
    );
  } catch {
    /* no-op: URL / history API unavailable */
  }
})();

(function () {
  var AUTO_DISMISS_MS = 4000;

  document.addEventListener('showFlash', function (e) {
    var f = e.detail;
    if (!f) return;

    var region = document.getElementById('flash-region');
    if (!region) return;

    var div = document.createElement('div');
    div.className = 'flash flash--' + f.level;
    div.setAttribute('role', 'alert');
    // f.body is server-composed HTML with markupsafe.escape() applied to all
    // DB-derived values — innerHTML here is intentional, not an XSS risk.
    div.innerHTML =
      '<div class="flash__body">' +
      f.body +
      '</div>' +
      '<button class="flash__close" aria-label="Dismiss" onclick="this.parentElement.remove()">\u00d7</button>';

    region.appendChild(div);

    var t = setTimeout(function () {
      if (div.parentElement) div.remove();
    }, AUTO_DISMISS_MS);
    div.addEventListener('mouseenter', function () {
      clearTimeout(t);
    });
    div.addEventListener('mouseleave', function () {
      t = setTimeout(function () {
        if (div.parentElement) div.remove();
      }, AUTO_DISMISS_MS);
    });
  });
})();
