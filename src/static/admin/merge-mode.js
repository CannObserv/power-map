/**
 * merge-mode.js — shared list-merge factory (#250).
 *
 * Extracted from people-merge.js (#249) so the People and Orgs list-merge
 * flows share one implementation instead of forking a near-identical copy per
 * entity. `role-merge.js` (org-detail roles table) uses the older #237
 * init-per-table lifecycle and is intentionally NOT migrated here yet; the
 * Roles *list* merge (#251) is a list flow and will consume this factory,
 * adding a same-org predicate at the 2-selection enable point.
 *
 * `window.createMergeMode(config)` wires merge mode for one list table:
 *   - toggle button, checkbox selection, sticky "Keep A / Keep B" action bar.
 *
 * Boost-safe by construction (same rationale as #249): the admin shell is
 * `hx-boost="true"`, and hx-boost strips <head> from boosted responses, so a
 * script bound to specific elements at eval time would be an inert no-op when
 * the list is reached by clicking the sidebar. Therefore every consumer:
 *   - resolves element refs lazily (re-getElementById on each use),
 *   - registers listeners once at the document level via delegation, and
 *   - keeps merge-mode state in closure scope, re-applied after swaps.
 *
 * config:
 *   tableId        table element id (e.g. 'people-table')
 *   btnId          toggle button id
 *   btnWrapId      wrapper span id (carries the disabled cursor/title)
 *   barId          action-bar id
 *   listRegionId   the hx swap region id; a partial swap of it PRESERVES merge
 *                  mode (clearing selection), and Keep buttons target it
 *   rowAttr        per-row id attribute used to count rows (e.g. 'data-org-id')
 *   nounPlural     entity noun for labels (e.g. 'organizations')
 *   buildMergeUrl  (winnerId, loserId) => POST url for the merge
 *   untitledLabel  fallback row label when data-title is absent ('(unnamed)')
 */
(function () {
  function createMergeMode(config) {
    var tableId = config.tableId;
    var btnId = config.btnId;
    var btnWrapId = config.btnWrapId;
    var barId = config.barId;
    var listRegionId = config.listRegionId;
    var rowAttr = config.rowAttr;
    var nounPlural = config.nounPlural;
    var buildMergeUrl = config.buildMergeUrl;
    var untitled = config.untitledLabel || '(unnamed)';

    var listRegionSelector = '#' + listRegionId;
    var rowSelector = 'tbody tr[' + rowAttr + ']';

    // Lazy resolvers — never cache; every target lives inside the
    // boost-swappable <body> / list region.
    function getMergeBtn() {
      return document.getElementById(btnId);
    }
    function getMergeBtnWrap() {
      return document.getElementById(btnWrapId);
    }
    function getTable() {
      return document.getElementById(tableId);
    }
    function getMergeBar() {
      return document.getElementById(barId);
    }

    // Closure-scope state — persists across region swaps.
    var inMergeMode = false;
    var checked = []; // ordered list of {id, title}

    function setMergeColVisibility(visible) {
      var t = getTable();
      if (!t) return;
      t.querySelectorAll('.merge-col').forEach(function (col) {
        col.style.display = visible ? '' : 'none';
      });
    }

    function setPaginationVisibility(visible) {
      // No-op if pagination is absent (short lists hide it server-side).
      document.querySelectorAll('.pagination--sticky').forEach(function (el) {
        el.style.display = visible ? '' : 'none';
      });
    }

    // Re-apply the current merge-mode state to whatever list is mounted. Safe
    // no-op when this list isn't on the page (other admin pages).
    function applyMergeModeState() {
      var t = getTable();
      if (!t) return;
      var btn = getMergeBtn();
      if (inMergeMode) {
        t.dataset.mergeMode = 'true';
        if (btn) {
          btn.textContent = 'Cancel merge';
          btn.classList.remove('btn--secondary');
          btn.classList.add('btn--ghost');
        }
        setMergeColVisibility(true);
        setPaginationVisibility(false);
      } else {
        delete t.dataset.mergeMode;
        if (btn) {
          btn.textContent = 'Merge';
          btn.classList.remove('btn--ghost');
          btn.classList.add('btn--secondary');
        }
        setMergeColVisibility(false);
        setPaginationVisibility(true);
      }
      updateBar();
      updateCheckboxes();
      syncMergeBtn();
    }

    function enterMergeMode() {
      inMergeMode = true;
      checked = [];
      applyMergeModeState();
    }

    function exitMergeMode() {
      inMergeMode = false;
      checked = [];
      applyMergeModeState();
    }

    function toggleMergeMode() {
      if (inMergeMode) {
        exitMergeMode();
      } else {
        enterMergeMode();
      }
    }

    function updateCheckboxes() {
      var t = getTable();
      if (!t) return;
      var cbs = t.querySelectorAll('input[name="merge-select"]');
      var checkedIds = checked.map(function (c) {
        return c.id;
      });
      var atMax = checked.length >= 2;
      cbs.forEach(function (cb) {
        var isChecked = checkedIds.indexOf(cb.value) !== -1;
        cb.checked = isChecked;
        cb.disabled = atMax && !isChecked;
      });
    }

    function updateBar() {
      var bar = getMergeBar();
      if (!bar) return;
      if (!inMergeMode) {
        bar.style.display = 'none';
        return;
      }

      bar.style.display = 'flex';

      var label = bar.querySelector('.merge-bar__label');
      var btnA = bar.querySelector('.merge-bar__keep-a');
      var btnB = bar.querySelector('.merge-bar__keep-b');

      if (checked.length === 0) {
        if (label) label.textContent = 'Select 2 ' + nounPlural + ' to merge:';
        if (btnA) {
          btnA.textContent = '—';
          btnA.disabled = true;
          btnA.removeAttribute('hx-post');
          btnA.removeAttribute('hx-confirm');
        }
        if (btnB) {
          btnB.textContent = '—';
          btnB.disabled = true;
          btnB.removeAttribute('hx-post');
          btnB.removeAttribute('hx-confirm');
        }
        return;
      }

      if (checked.length === 1) {
        var a = checked[0];
        if (label) label.textContent = 'Select 1 more:';
        if (btnA) {
          btnA.textContent = 'Selected: "' + a.title + '"';
          btnA.disabled = true;
          btnA.removeAttribute('hx-post');
          btnA.removeAttribute('hx-confirm');
        }
        if (btnB) {
          btnB.textContent = '—';
          btnB.disabled = true;
          btnB.removeAttribute('hx-post');
          btnB.removeAttribute('hx-confirm');
        }
        return;
      }

      // Two selected. Extension point (#251): a same-org predicate would gate
      // the enable/hx-post wiring here, showing a disabled-state hint instead
      // when the two rows can't be merged together.
      var rowA = checked[0];
      var rowB = checked[1];
      if (label) label.textContent = 'Merge ' + nounPlural + ':';

      if (btnA) {
        btnA.textContent = 'Keep "' + rowA.title + '"';
        btnA.disabled = false;
        btnA.setAttribute('hx-post', buildMergeUrl(rowA.id, rowB.id));
        btnA.setAttribute('hx-target', listRegionSelector);
        btnA.setAttribute('hx-swap', 'innerHTML');
        btnA.setAttribute(
          'hx-confirm',
          'Merge "' + rowB.title + '" into "' + rowA.title + '"? This cannot be undone.',
        );
      }
      if (btnB) {
        btnB.textContent = 'Keep "' + rowB.title + '"';
        btnB.disabled = false;
        btnB.setAttribute('hx-post', buildMergeUrl(rowB.id, rowA.id));
        btnB.setAttribute('hx-target', listRegionSelector);
        btnB.setAttribute('hx-swap', 'innerHTML');
        btnB.setAttribute(
          'hx-confirm',
          'Merge "' + rowA.title + '" into "' + rowB.title + '"? This cannot be undone.',
        );
      }

      if (typeof htmx !== 'undefined') {
        if (btnA) htmx.process(btnA);
        if (btnB) htmx.process(btnB);
      }
    }

    function onCheckboxChange(e) {
      var cb = e.target;
      if (!cb || !cb.matches) return;
      // Document-level delegation — match only checkboxes inside this table.
      if (!cb.matches('#' + tableId + ' input[name="merge-select"]')) return;

      var rowId = cb.value;
      var row = cb.closest('tr');
      var title = row ? row.dataset.title || untitled : untitled;

      if (cb.checked) {
        checked.push({ id: rowId, title: title });
      } else {
        checked = checked.filter(function (c) {
          return c.id !== rowId;
        });
      }

      updateCheckboxes();
      updateBar();
    }

    function syncMergeBtn() {
      var btn = getMergeBtn();
      if (!btn) return;
      var t = getTable();
      var rows = t ? t.querySelectorAll(rowSelector) : [];
      var canMerge = rows.length >= 2;
      btn.disabled = !canMerge;
      var wrap = getMergeBtnWrap();
      if (wrap) {
        wrap.style.cursor = canMerge ? '' : 'not-allowed';
        if (canMerge) {
          wrap.removeAttribute('title');
        } else {
          wrap.title = 'At least 2 ' + nounPlural + ' required to merge';
        }
      }
    }

    // A list-region partial swap (search / filter / page change / post-merge
    // refresh) replaces the table but PRESERVES merge mode. The new tbody may
    // not contain the previously selected rows, so selection is cleared.
    function onRegionSwap() {
      checked = [];
      applyMergeModeState();
    }

    // A boosted full-page arrival of the list starts CLEAN — merge mode is a
    // transient mode that should not carry across navigations, and a stale
    // selection would point at rows from the previous view.
    function onFreshArrival() {
      inMergeMode = false;
      checked = [];
      applyMergeModeState();
    }

    // ── Document-level listeners — registered once, survive every swap ────────

    // Toggle button: delegated (the button element is replaced on each boosted
    // nav, so a direct binding would go stale).
    document.addEventListener('click', function (e) {
      if (e.target && e.target.closest && e.target.closest('#' + btnId)) {
        toggleMergeMode();
      }
    });

    document.addEventListener('change', onCheckboxChange);

    document.addEventListener('showFlash', function () {
      if (inMergeMode) exitMergeMode();
    });

    // htmx:afterSwap bubbles to document; listen there so test cleanup (which
    // spies on document.addEventListener) can drain it between tests.
    document.addEventListener('htmx:afterSwap', function (e) {
      var t = e.target;
      if (!t) return;
      // Partial region swap (search / filter / page / post-merge refresh): the
      // target is (or is inside) the list region. Preserves merge mode.
      if (t.id === listRegionId || (t.closest && t.closest(listRegionSelector))) {
        onRegionSwap();
      }
    });

    // Boosted full-page arrival: hx-boost swaps <body> and htmx fires htmx:load
    // on the new content (the proven #237 boost signal). Only a full-page nav
    // carries the page-header merge button in its loaded subtree; a list-region
    // partial swap does not — so this resets merge mode on a genuine
    // navigation, never on a search / filter / pagination swap.
    document.addEventListener('htmx:load', function (e) {
      var el = (e.detail && e.detail.elt) || e.target;
      if (!el) return;
      if (el.id === btnId || (el.querySelector && el.querySelector('#' + btnId))) {
        onFreshArrival();
      }
    });

    // First full page load (direct URL / hard refresh): reconcile visual state
    // to the list already in the DOM, independent of htmx:load timing. No-op
    // off this list.
    applyMergeModeState();
  }

  window.createMergeMode = createMergeMode;
})();
