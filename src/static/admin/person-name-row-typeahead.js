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
    if (typeof window.initTypeaheadCombobox !== 'function') return;
    window.initTypeaheadCombobox({
      inputId: 'locale-search-display-' + uid,
      listboxId: 'locale-search-results-' + uid,
      hiddenId: 'locale-hidden-' + uid,
    });
    window.initTypeaheadCombobox({
      inputId: 'script-search-display-' + uid,
      listboxId: 'script-search-results-' + uid,
      hiddenId: 'script-hidden-' + uid,
    });
    window.initTypeaheadCombobox({
      inputId: 'reading-of-display-' + uid,
      listboxId: 'reading-of-results-' + uid,
      hiddenId: 'reading-of-hidden-' + uid,
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

  document.addEventListener('DOMContentLoaded', function () {
    scan(document);
  });
  document.addEventListener('htmx:afterSwap', function (e) {
    scan(e.target);
  });
})();
