/**
 * role-merge.js — loaded via <script src defer> in extra_head on the org detail page.
 *
 * Manages merge mode for the roles table: toggle button, checkbox selection,
 * and fixed floating action bar with progressive disclosure and Keep A / Keep B buttons.
 *
 * Lives in <head> via the extra_head block so hx-boost never re-executes it.
 */
(function () {
  var table = document.getElementById('roles-table');
  var mergeBtn = document.getElementById('roles-merge-btn');
  var mergeBtnWrap = document.getElementById('roles-merge-btn-wrap');
  var mergeBar = document.getElementById('roles-merge-bar');
  if (!table || !mergeBtn || !mergeBar) return;

  var orgId = table.dataset.orgId;
  var checked = []; // ordered list of {id, title} for checked roles

  function setMergeColVisibility(visible) {
    var cols = table.querySelectorAll('.merge-col');
    cols.forEach(function (col) {
      col.style.display = visible ? '' : 'none';
    });
  }

  function enterMergeMode() {
    table.dataset.mergeMode = 'true';
    mergeBtn.textContent = 'Cancel merge';
    mergeBtn.classList.remove('btn--secondary');
    mergeBtn.classList.add('btn--ghost');
    checked = [];
    setMergeColVisibility(true);
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
      if (label) label.textContent = 'Select 2 roles to merge:';
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

    var roleA = checked[0];
    var roleB = checked[1];
    if (label) label.textContent = 'Merge roles:';

    if (btnA) {
      btnA.textContent = 'Keep "' + roleA.title + '"';
      btnA.disabled = false;
      btnA.setAttribute(
        'hx-post',
        '/admin/orgs/' + orgId + '/roles/' + roleA.id + '/merge/' + roleB.id + '/',
      );
      btnA.setAttribute(
        'hx-confirm',
        'Merge "' + roleB.title + '" into "' + roleA.title + '"? This cannot be undone.',
      );
    }
    if (btnB) {
      btnB.textContent = 'Keep "' + roleB.title + '"';
      btnB.disabled = false;
      btnB.setAttribute(
        'hx-post',
        '/admin/orgs/' + orgId + '/roles/' + roleB.id + '/merge/' + roleA.id + '/',
      );
      btnB.setAttribute(
        'hx-confirm',
        'Merge "' + roleA.title + '" into "' + roleB.title + '"? This cannot be undone.',
      );
    }

    // Re-process HTMX attributes on dynamically updated buttons
    if (typeof htmx !== 'undefined') {
      if (btnA) htmx.process(btnA);
      if (btnB) htmx.process(btnB);
    }
  }

  function onCheckboxChange(e) {
    var cb = e.target;
    if (!cb.matches || !cb.matches('input[name="merge-select"]')) return;

    var roleId = cb.value;
    var row = cb.closest('tr');
    var title = row ? row.dataset.title || '(untitled)' : '(untitled)';

    if (cb.checked) {
      checked.push({ id: roleId, title: title });
    } else {
      checked = checked.filter(function (c) {
        return c.id !== roleId;
      });
    }

    updateCheckboxes();
    updateBar();
  }

  function syncMergeBtn() {
    var roleRows = table.querySelectorAll('tbody tr[data-role-id]');
    var canMerge = roleRows.length >= 2;
    mergeBtn.disabled = !canMerge;
    // cursor/title go on the wrapper — .btn:disabled sets pointer-events:none
    // on the button itself, which would swallow hover events otherwise
    if (mergeBtnWrap) {
      mergeBtnWrap.style.cursor = canMerge ? '' : 'not-allowed';
      if (canMerge) {
        mergeBtnWrap.removeAttribute('title');
      } else {
        mergeBtnWrap.title = 'At least 2 roles required to merge';
      }
    }
  }

  // Listen for checkbox changes via delegation on the table
  table.addEventListener('change', onCheckboxChange);

  // Roles filter
  var filterInput = document.getElementById('roles-filter');
  if (filterInput) {
    filterInput.addEventListener('input', function () {
      var v = this.value.toLowerCase();
      table.querySelectorAll('tbody tr[data-title]').forEach(function (r) {
        r.style.display = r.dataset.title.toLowerCase().includes(v) ? '' : 'none';
      });
    });
  }

  // Toggle button
  mergeBtn.addEventListener('click', toggleMergeMode);

  // Exit merge mode after successful merge (HTMX swap replaces tbody)
  document.addEventListener('showFlash', function () {
    if (table.dataset.mergeMode === 'true') {
      exitMergeMode();
    }
  });

  // Re-evaluate button state after any tbody swap (role added or merged)
  table.addEventListener('htmx:afterSwap', syncMergeBtn);

  syncMergeBtn();
})();
