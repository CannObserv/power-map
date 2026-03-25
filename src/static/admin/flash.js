/* flash.js — showFlash event listener for HX-Trigger-based flash messages.
 *
 * HTMX dispatches a custom DOM event named "showFlash" when it processes an
 * HX-Trigger response header containing {"showFlash": {"level": "...", "body": "..."}}.
 * This listener catches that event and injects a flash notification into #flash-region.
 */
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
    div.innerHTML =
      '<div class="flash__body">' + f.body + '</div>' +
      '<button class="flash__close" aria-label="Dismiss" onclick="this.parentElement.remove()">\u00d7</button>';

    region.appendChild(div);

    var t = setTimeout(function () { if (div.parentElement) div.remove(); }, AUTO_DISMISS_MS);
    div.addEventListener('mouseenter', function () { clearTimeout(t); });
    div.addEventListener('mouseleave', function () {
      t = setTimeout(function () { if (div.parentElement) div.remove(); }, AUTO_DISMISS_MS);
    });
  });
})();
