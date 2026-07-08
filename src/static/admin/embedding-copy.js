/**
 * embedding-copy.js — copy a person's full voice-embedding vector to the clipboard.
 *
 * The Voice Embeddings section (#284) renders only a truncated preview in the
 * page; the full 256-float vector is fetched on demand from the row's
 * data-embedding-vector-url endpoint, then written to the clipboard. Success /
 * failure surfaces through the shared flash.js `showFlash` event.
 *
 * Loaded site-wide via base.html with defer (per the #237 hx-boost note) and
 * delegated from document, so it survives HTMX tbody swaps and is a no-op on
 * pages without a Copy button.
 */
(function () {
  function flash(level, body) {
    document.dispatchEvent(new CustomEvent('showFlash', { detail: { level: level, body: body } }));
  }

  document.addEventListener('click', function (e) {
    var btn = e.target.closest('[data-embedding-vector-url]');
    if (!btn) return;
    e.preventDefault();
    var url = btn.getAttribute('data-embedding-vector-url');
    fetch(url, { headers: { 'HX-Request': 'true' } })
      .then(function (r) {
        if (!r.ok) throw new Error('HTTP ' + r.status);
        return r.text();
      })
      .then(function (text) {
        return navigator.clipboard.writeText(text);
      })
      .then(function () {
        flash('success', 'Vector copied to clipboard.');
      })
      .catch(function () {
        flash('error', 'Could not copy vector.');
      });
  });
})();
