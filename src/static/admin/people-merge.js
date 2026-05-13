/**
 * people-merge.js — loaded via <script src defer> in extra_head on the People list page.
 *
 * Manages merge mode for the people list table: toggle button, checkbox
 * selection, and a sticky action bar that swaps in for `.pagination--sticky`
 * while merge mode is active (no overlap, single sticky slot).
 *
 * Lives in <head> via the extra_head block so hx-boost never re-executes it.
 *
 * Structural parallel of role-merge.js. Diff:
 *   - no `data-org-id` — URL is `/admin/people/{a}/merge/{b}/`
 *   - hides/restores `.pagination--sticky` on mode toggle (roles page has none)
 *   - hx-target is `#people-table-body` (signals the list-flow branch on the route)
 *   - no inline client-side filter (search is server-side via the filter card)
 */
(function () {
  var table = document.getElementById('people-table');
  var mergeBtn = document.getElementById('people-merge-btn');
  var mergeBtnWrap = document.getElementById('people-merge-btn-wrap');
  var mergeBar = document.getElementById('people-merge-bar');
  if (!table || !mergeBtn || !mergeBar) return;

  var checked = []; // ordered list of {id, title} for checked people

  function setMergeColVisibility(visible) {
    table.querySelectorAll('.merge-col').forEach(function (col) {
      col.style.display = visible ? '' : 'none';
    });
  }

  function setPaginationVisibility(visible) {
    // Swap pagination ↔ merge bar within the same sticky slot. No-op if
    // pagination is absent (short lists hide it via {% if total_pages > 1 %}).
    document.querySelectorAll('.pagination--sticky').forEach(function (el) {
      el.style.display = visible ? '' : 'none';
    });
  }

  function enterMergeMode() {
    table.dataset.mergeMode = 'true';
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
    delete table.dataset.mergeMode;
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
    if (table.dataset.mergeMode === 'true') {
      exitMergeMode();
    } else {
      enterMergeMode();
    }
  }

  function updateCheckboxes() {
    var cbs = table.querySelectorAll('input[name="merge-select"]');
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
    if (table.dataset.mergeMode !== 'true') {
      mergeBar.style.display = 'none';
      return;
    }

    mergeBar.style.display = 'flex';

    var label = mergeBar.querySelector('.merge-bar__label');
    var btnA = mergeBar.querySelector('.merge-bar__keep-a');
    var btnB = mergeBar.querySelector('.merge-bar__keep-b');

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
      btnA.setAttribute('hx-target', '#people-table-body');
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
      btnB.setAttribute('hx-target', '#people-table-body');
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
    if (!cb.matches || !cb.matches('input[name="merge-select"]')) return;

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
    var rows = table.querySelectorAll('tbody tr[data-person-id]');
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

  // Listen for checkbox changes via delegation on the table
  table.addEventListener('change', onCheckboxChange);

  // Toggle button
  mergeBtn.addEventListener('click', toggleMergeMode);

  // Exit merge mode after successful merge (HTMX swap replaces tbody)
  document.addEventListener('showFlash', function () {
    if (table.dataset.mergeMode === 'true') {
      exitMergeMode();
    }
  });

  // Re-evaluate button state after any tbody swap (search, pagination, merge)
  table.addEventListener('htmx:afterSwap', syncMergeBtn);

  syncMergeBtn();
})();
