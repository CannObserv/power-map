/* event-form-row.js — per-row wiring for the entity-event create/edit form.
 *
 * Issue #172: extracted from the inline <script> previously rendered inside
 * `admin/shared/_event_form_row.html`. The form declares
 * `<form data-event-form-row data-uid="{uid}">`; this module discovers each
 * form via DOMContentLoaded (initial render) and htmx:afterSwap (HTMX-injected
 * new/edit rows) and wires:
 *   - the Linked Entity section show/hide, driven by the event_type select's
 *     `data-requires-linked` option flag;
 *   - the linked-entity typeahead combobox (people vs orgs, scoped by the
 *     linked_entity_type select via hx-include);
 *   - the linked_entity_type select: switching scope clears any prior
 *     selection and disables the search input until a scope is chosen.
 *
 * Element ids per form are namespaced via uid (`ev.id` for an existing row,
 * `new` for the inline new-event form).
 *
 * Depends on `typeahead-combobox.js` for the combobox factory.
 */
(function () {
  function initForm(form) {
    var uid = form.dataset.uid;
    if (!uid) return;
    // Idempotency: re-running would register duplicate listeners. Tag AFTER
    // the dependency check so a missed factory doesn't permanently flag the
    // form (mirrors person-name-row-typeahead.js).
    if (form.dataset.eventRowInited) return;
    if (typeof window.initTypeaheadCombobox !== 'function') return;
    form.dataset.eventRowInited = '1';

    var eventTypeSel = form.querySelector('[name="event_type_id"]');
    var section = form.querySelector('[data-linked-entity-section]');
    var linkedTypeSel = form.querySelector('[data-linked-type]');
    var search = document.getElementById('linked-entity-search-' + uid);
    var hidden = document.getElementById('linked-entity-id-' + uid);
    if (!eventTypeSel || !section || !linkedTypeSel || !search || !hidden) return;

    function syncSection() {
      var opt = eventTypeSel.options[eventTypeSel.selectedIndex];
      section.style.display = opt && opt.dataset.requiresLinked === 'true' ? '' : 'none';
    }
    function syncSearchDisabled() {
      // No scope chosen ⇒ nothing to search against; force a choice first.
      search.disabled = !linkedTypeSel.value;
    }

    eventTypeSel.addEventListener('change', syncSection);
    linkedTypeSel.addEventListener('change', function () {
      // Switching scope invalidates any prior person/org selection.
      search.value = '';
      hidden.value = '';
      syncSearchDisabled();
    });

    window.initTypeaheadCombobox({
      inputId: 'linked-entity-search-' + uid,
      listboxId: 'linked-entity-results-' + uid,
      hiddenId: 'linked-entity-id-' + uid,
      clearButtonId: 'linked-entity-clear-' + uid,
    });

    syncSection();
    syncSearchDisabled();
  }

  function scan(root) {
    if (!root) return;
    if (root.matches && root.matches('[data-event-form-row]')) {
      initForm(root);
      return;
    }
    if (root.querySelectorAll) {
      root.querySelectorAll('[data-event-form-row]').forEach(initForm);
    }
  }

  document.addEventListener('DOMContentLoaded', function () {
    scan(document);
  });
  document.addEventListener('htmx:afterSwap', function (e) {
    scan(e.target);
  });
})();
