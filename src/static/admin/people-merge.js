/**
 * people-merge.js — loaded via <script src defer> in extra_head on the People list page.
 *
 * Manages merge mode for the people list table: toggle button, checkbox
 * selection, and a sticky action bar that swaps in for `.pagination--sticky`
 * while merge mode is active (no overlap, single sticky slot).
 *
 * Lives in <head> via the extra_head block so hx-boost never re-executes it.
 *
 * Swap-resilient: the People list's filter card swaps the entire
 * `#people-list-region` on search/status/page-size change. Element refs
 * inside the region (table, merge bar, pagination strip) are re-resolved
 * on demand rather than cached; `change` events are caught at the
 * document level via delegation; merge-mode visual state is re-applied
 * on `htmx:afterSwap`.
 */
(function () {
  // Stable elements (outside #people-list-region — survive region swaps).
  var mergeBtn = document.getElementById('people-merge-btn');
  var mergeBtnWrap = document.getElementById('people-merge-btn-wrap');
  if (!mergeBtn) return;

  // Volatile elements live inside #people-list-region. Re-resolve on each
  // access so a swap doesn't leave us holding detached nodes.
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

  function enterMergeMode() {
    inMergeMode = true;
    var t = getTable();
    if (t) t.dataset.mergeMode = 'true';
    mergeBtn.textContent = 'Cancel merge';
    mergeBtn.classList.remove('btn--secondary');
    mergeBtn.classList.add('btn--ghost');
    checked = [];
    setMergeColVisibility(true);
    setPaginationVisibility(false);
    updateBar();
    updateCheckboxes();
  }

  function exitMergeMode() {
    inMergeMode = false;
    var t = getTable();
    if (t) delete t.dataset.mergeMode;
    mergeBtn.textContent = 'Merge';
    mergeBtn.classList.remove('btn--ghost');
    mergeBtn.classList.add('btn--secondary');
    checked = [];
    setMergeColVisibility(false);
    setPaginationVisibility(true);
    updateBar();
    updateCheckboxes();
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
    var t = getTable();
    var rows = t ? t.querySelectorAll('tbody tr[data-person-id]') : [];
    var canMerge = rows.length >= 2;
    mergeBtn.disabled = !canMerge;
    if (mergeBtnWrap) {
      mergeBtnWrap.style.cursor = canMerge ? '' : 'not-allowed';
      if (canMerge) {
        mergeBtnWrap.removeAttribute('title');
      } else {
        mergeBtnWrap.title = 'At least 2 people required to merge';
      }
    }
  }

  // Re-apply merge-mode visuals after the region is swapped (search / filter
  // / page change / post-merge refresh). Selection is intentionally cleared
  // on swap — the new tbody may not contain the previously selected rows,
  // and v1 keeps the role-merge behaviour of starting fresh after a swap.
  function onRegionSwap() {
    checked = [];
    if (inMergeMode) {
      var t = getTable();
      if (t) t.dataset.mergeMode = 'true';
      setMergeColVisibility(true);
      setPaginationVisibility(false);
    } else {
      setMergeColVisibility(false);
      setPaginationVisibility(true);
    }
    updateBar();
    updateCheckboxes();
    syncMergeBtn();
  }

  // Document-level delegation — survives region swaps.
  document.addEventListener('change', onCheckboxChange);

  mergeBtn.addEventListener('click', toggleMergeMode);

  document.addEventListener('showFlash', function () {
    if (inMergeMode) exitMergeMode();
  });

  // htmx:afterSwap bubbles to document; listen there so test cleanup (which
  // spies on document.addEventListener) can drain it between tests.
  document.addEventListener('htmx:afterSwap', function (e) {
    var t = e.target;
    if (!t) return;
    if (t.id === 'people-list-region' || (t.closest && t.closest('#people-list-region'))) {
      onRegionSwap();
    }
  });

  syncMergeBtn();
})();
