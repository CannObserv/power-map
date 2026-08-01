/* person-name-row-typeahead.js — per-row wiring for the locale, script,
 * and reading-of typeaheads on the person-name editor form row, plus the
 * reading-of-block visibility toggle driven by the row's name_type select.
 *
 * Issue #131: extracted from inline `<script>` previously rendered inside
 * `_name_form_row.html`. The form row template now declares
 * `<tr data-name-row-typeahead data-uid="{uid}">` and this module discovers
 * each row via DOMContentLoaded (initial render) and htmx:afterSwap
 * (HTMX-injected rows from + Add and Edit flows).
 *
 * Element ids per row are namespaced via the row's uid (`n.id` for an
 * existing row, `new` for the inline new-name form) — see
 * `_name_metadata_fields.html`.
 *
 * Depends on `typeahead-combobox.js` for the combobox factory.
 */
(function () {
  function initRow(row) {
    var uid = row.dataset.uid;
    if (!uid) return;
    // Idempotency: re-running initTypeaheadCombobox on the same input
    // would register duplicate listeners. Tag the row AFTER the
    // dependency check so a missed factory (combobox script not yet
    // loaded) doesn't flag the row as inited and prevent a later retry
    // from succeeding. Re-discovery on a tagged row is a no-op
    // (DOMContentLoaded + htmx:afterSwap, or a future hx-swap-oob path
    // that preserves the <tr>).
    if (row.dataset.typeaheadInited) return;
    if (typeof window.initTypeaheadCombobox !== 'function') return;
    row.dataset.typeaheadInited = '1';
    window.initTypeaheadCombobox({
      inputId: 'locale-search-display-' + uid,
      listboxId: 'locale-search-results-' + uid,
      hiddenId: 'locale-hidden-' + uid,
      clearButtonId: 'locale-clear-' + uid,
    });
    window.initTypeaheadCombobox({
      inputId: 'script-search-display-' + uid,
      listboxId: 'script-search-results-' + uid,
      hiddenId: 'script-hidden-' + uid,
      clearButtonId: 'script-clear-' + uid,
    });
    window.initTypeaheadCombobox({
      inputId: 'reading-of-display-' + uid,
      listboxId: 'reading-of-results-' + uid,
      hiddenId: 'reading-of-hidden-' + uid,
      clearButtonId: 'reading-of-clear-' + uid,
    });
    // Reading-of block visibility: shown only when name_type is one of
    // {reading, romanization, mrz} — matches the person_names.name_type
    // CHECK constraint and the typeahead endpoint's NOT IN filter.
    var sel = row.querySelector('select[name="name_type"]');
    var block = document.getElementById('reading-of-block-' + uid);
    if (!sel || !block) return;
    var READING_TYPES = ['reading', 'romanization', 'mrz'];
    function sync() {
      block.style.display = READING_TYPES.indexOf(sel.value) === -1 ? 'none' : '';
    }
    sel.addEventListener('change', sync);
    sync();
  }

  function scan(root) {
    if (!root) return;
    if (root.matches && root.matches('[data-name-row-typeahead]')) {
      initRow(root);
      return;
    }
    if (root.querySelectorAll) {
      root.querySelectorAll('[data-name-row-typeahead]').forEach(initRow);
    }
  }

  // Page-wide DOMContentLoaded + htmx:afterSwap listeners on `document`
  // match the existing convention (see `person-name-deadname-confirm.js`
  // and `person-name-parts-cardstack.js`); `scan()` filters per-row via
  // the `[data-name-row-typeahead]` selector.
  document.addEventListener('DOMContentLoaded', function () {
    scan(document);
  });
  document.addEventListener('htmx:afterSwap', function (e) {
    scan(e.target);
  });
})();
