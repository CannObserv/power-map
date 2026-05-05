/* person-name-deadname-confirm.js — admin-side confirmation for deadname rows.
 *
 * When the editor selects `name_type=deadname` on a person-name form, set
 * hx-confirm + data-confirm-* attributes on the form so the existing
 * admin-modal.js handler renders a styled confirmation dialog before the
 * row is saved. Selecting any other type clears those attributes.
 *
 * Why: `trg_deadname_visibility` (DB) auto-coerces public→legal_only on
 * deadname rows. The confirmation surface makes that side effect explicit
 * to the admin instead of letting it happen silently.
 *
 * Hooks: DOMContentLoaded for initial scan, htmx:afterSwap for HTMX-injected
 * forms, change events on select[name="name_type"] inside person-name forms.
 */
(function () {
  var CONFIRM_MSG =
    'Saving as deadname will hide this row from public views ' +
    '(visibility coerced to legal_only). Continue?';
  var CONFIRM_TITLE = 'Save as deadname?';
  var CONFIRM_VARIANT = 'danger';
  var CONFIRM_LABEL = 'Save deadname';

  function isPersonNameForm(form) {
    if (!form || form.tagName !== 'FORM') return false;
    var hxPost = form.getAttribute('hx-post') || '';
    return /\/admin\/people\/[^/]+\/names\//.test(hxPost);
  }

  function syncForm(form) {
    if (!isPersonNameForm(form)) return;
    var select = form.querySelector('select[name="name_type"]');
    if (!select) return;
    if (select.value === 'deadname') {
      form.setAttribute('hx-confirm', CONFIRM_MSG);
      form.dataset.confirmTitle = CONFIRM_TITLE;
      form.dataset.confirmVariant = CONFIRM_VARIANT;
      form.dataset.confirmLabel = CONFIRM_LABEL;
    } else {
      form.removeAttribute('hx-confirm');
      delete form.dataset.confirmTitle;
      delete form.dataset.confirmVariant;
      delete form.dataset.confirmLabel;
    }
  }

  function scanRoot(root) {
    if (!root || !root.querySelectorAll) return;
    var forms = root.querySelectorAll('form[hx-post*="/names/"]');
    forms.forEach(syncForm);
  }

  // Delegated change listener — survives HTMX swaps.
  document.addEventListener('change', function (e) {
    var target = e.target;
    if (!target || target.name !== 'name_type') return;
    var form = target.closest('form');
    syncForm(form);
  });

  // Initial scan + post-swap rescan for HTMX-injected rows.
  document.addEventListener('DOMContentLoaded', function () {
    scanRoot(document);
  });
  document.addEventListener('htmx:afterSwap', function (e) {
    var root = (e.detail && e.detail.target) || document;
    scanRoot(root);
  });

  // Run an immediate scan for forms already in the DOM at script-eval time
  // (covers test fixtures that eval the script after building DOM, plus
  // hx-boost re-execution in <head>-loaded mode).
  scanRoot(document);
})();
