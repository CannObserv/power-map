/**
 * people-merge.js — merge mode for the People list table: toggle button,
 * checkbox selection, and a sticky action bar that swaps in for
 * `.pagination--sticky` while merge mode is active (no overlap, single sticky
 * slot).
 *
 * Loaded site-wide from base.html <head> (#249). The admin shell is
 * `hx-boost="true"`, and hx-boost strips <head> from boosted responses — a
 * script in the People list's extra_head never ran when the list was reached
 * by clicking the sidebar, so Merge was an inert no-op (no console error, the
 * script simply wasn't there). Loading site-wide fixes that, but means the
 * IIFE evaluates once, on whatever page first loads — often without the People
 * list. So the script must NOT bind to specific elements at eval time:
 *
 *   - All element refs are resolved lazily (re-`getElementById` on each use),
 *     so a boosted nav or a #people-list-region swap never leaves us holding a
 *     detached node.
 *   - Every listener is registered once, at the document level, via delegation
 *     (click on the toggle, change on the checkboxes, showFlash, htmx:afterSwap,
 *     htmx:load) — so they fire for elements that arrive later via a boosted nav.
 *   - Merge-mode state lives in module scope and is re-applied after swaps:
 *       · a #people-list-region partial swap (search / filter / page / post-
 *         merge refresh) PRESERVES merge mode, clearing only the selection;
 *       · a boosted full-page arrival of the list (detected via htmx:load, the
 *         proven #237 boost signal — same as role-merge.js) starts CLEAN (fresh
 *         mode off, no stale selection pointing at people from the previous view).
 */
(function () {
  // Lazy resolvers — never cache; every target lives inside the boost-swappable
  // <body> / #people-list-region.
  function getMergeBtn() {
    return document.getElementById('people-merge-btn');
  }
  function getMergeBtnWrap() {
    return document.getElementById('people-merge-btn-wrap');
  }
  function getTable() {
    return document.getElementById('people-table');
  }
  function getMergeBar() {
    return document.getElementById('people-merge-bar');
  }

  // Module-scope state — persists across region swaps.
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

  // Re-apply the current merge-mode state to whatever People list is mounted.
  // Safe no-op when the People list isn't on the page (non-People admin pages).
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
      if (label) label.textContent = 'Select 2 people to merge:';
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

    var personA = checked[0];
    var personB = checked[1];
    if (label) label.textContent = 'Merge people:';

    if (btnA) {
      btnA.textContent = 'Keep "' + personA.title + '"';
      btnA.disabled = false;
      btnA.setAttribute('hx-post', '/admin/people/' + personA.id + '/merge/' + personB.id + '/');
      btnA.setAttribute('hx-target', '#people-list-region');
      btnA.setAttribute('hx-swap', 'innerHTML');
      btnA.setAttribute(
        'hx-confirm',
        'Merge "' + personB.title + '" into "' + personA.title + '"? This cannot be undone.',
      );
    }
    if (btnB) {
      btnB.textContent = 'Keep "' + personB.title + '"';
      btnB.disabled = false;
      btnB.setAttribute('hx-post', '/admin/people/' + personB.id + '/merge/' + personA.id + '/');
      btnB.setAttribute('hx-target', '#people-list-region');
      btnB.setAttribute('hx-swap', 'innerHTML');
      btnB.setAttribute(
        'hx-confirm',
        'Merge "' + personA.title + '" into "' + personB.title + '"? This cannot be undone.',
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
    // Document-level delegation — match only checkboxes inside #people-table.
    if (!cb.matches('#people-table input[name="merge-select"]')) return;

    var personId = cb.value;
    var row = cb.closest('tr');
    var title = row ? row.dataset.title || '(unnamed)' : '(unnamed)';

    if (cb.checked) {
      checked.push({ id: personId, title: title });
    } else {
      checked = checked.filter(function (c) {
        return c.id !== personId;
      });
    }

    updateCheckboxes();
    updateBar();
  }

  function syncMergeBtn() {
    var btn = getMergeBtn();
    if (!btn) return;
    var t = getTable();
    var rows = t ? t.querySelectorAll('tbody tr[data-person-id]') : [];
    var canMerge = rows.length >= 2;
    btn.disabled = !canMerge;
    var wrap = getMergeBtnWrap();
    if (wrap) {
      wrap.style.cursor = canMerge ? '' : 'not-allowed';
      if (canMerge) {
        wrap.removeAttribute('title');
      } else {
        wrap.title = 'At least 2 people required to merge';
      }
    }
  }

  // A #people-list-region partial swap (search / filter / page change / post-
  // merge refresh) replaces the table element but PRESERVES merge mode. The new
  // tbody may not contain the previously selected rows, so selection is cleared.
  function onRegionSwap() {
    checked = [];
    applyMergeModeState();
  }

  // A boosted full-page arrival of the People list starts CLEAN — merge mode is
  // a transient mode that should not carry across navigations, and a stale
  // selection would point at people from the previous view.
  function onFreshArrival() {
    inMergeMode = false;
    checked = [];
    applyMergeModeState();
  }

  // ── Document-level listeners — registered once, survive every swap ──────────

  // Toggle button: delegated (the button element is replaced on each boosted
  // nav, so a direct binding would go stale).
  document.addEventListener('click', function (e) {
    if (e.target && e.target.closest && e.target.closest('#people-merge-btn')) {
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
    // target is (or is inside) #people-list-region. Preserves merge mode.
    if (t.id === 'people-list-region' || (t.closest && t.closest('#people-list-region'))) {
      onRegionSwap();
    }
  });

  // Boosted full-page arrival: hx-boost swaps <body> and htmx fires htmx:load on
  // the new content (the proven #237 boost signal — same as role-merge.js). Only
  // a full-page nav carries the page-header merge button in its loaded subtree; a
  // #people-list-region partial swap does not — so this resets merge mode on a
  // genuine navigation, never on a search / filter / pagination swap.
  document.addEventListener('htmx:load', function (e) {
    var el = (e.detail && e.detail.elt) || e.target;
    if (!el) return;
    if (
      el.id === 'people-merge-btn' ||
      (el.querySelector && el.querySelector('#people-merge-btn'))
    ) {
      onFreshArrival();
    }
  });

  // First full page load (direct URL / hard refresh): reconcile visual state to
  // the list already in the DOM, independent of htmx:load timing. No-op off the
  // People list.
  applyMergeModeState();
})();
