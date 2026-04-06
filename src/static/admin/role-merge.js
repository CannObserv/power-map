/**
 * role-merge.js — loaded via <script src defer> in extra_head on the org detail page.
 *
 * Manages merge mode for the roles table: toggle button, checkbox selection,
 * and sticky action bar with Keep A / Keep B buttons.
 *
 * Lives in <head> via the extra_head block so hx-boost never re-executes it.
 */
(function () {
  var table = document.getElementById('roles-table');
  var mergeBtn = document.getElementById('roles-merge-btn');
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
    var checkedIds = checked.map(function (c) { return c.id; });
    cbs.forEach(function (cb) {
      cb.checked = checkedIds.indexOf(cb.value) !== -1;
    });
  }

  function updateBar() {
    if (checked.length < 2) {
      mergeBar.style.display = 'none';
      return;
    }
    var a = checked[0];
    var b = checked[1];
    mergeBar.style.display = 'flex';

    var label = mergeBar.querySelector('.merge-bar__label');
    if (label) {
      label.innerHTML = 'Merge roles:';
    }

    var btnA = mergeBar.querySelector('.merge-bar__keep-a');
    var btnB = mergeBar.querySelector('.merge-bar__keep-b');
    if (btnA) {
      btnA.textContent = 'Keep "' + a.title + '"';
      btnA.setAttribute(
        'hx-post',
        '/admin/orgs/' + orgId + '/roles/' + a.id + '/merge/' + b.id + '/'
      );
      btnA.setAttribute(
        'hx-confirm',
        'Merge "' + b.title + '" into "' + a.title + '"? This cannot be undone.'
      );
    }
    if (btnB) {
      btnB.textContent = 'Keep "' + b.title + '"';
      btnB.setAttribute(
        'hx-post',
        '/admin/orgs/' + orgId + '/roles/' + b.id + '/merge/' + a.id + '/'
      );
      btnB.setAttribute(
        'hx-confirm',
        'Merge "' + a.title + '" into "' + b.title + '"? This cannot be undone.'
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
    var title = row ? (row.dataset.title || '(untitled)') : '(untitled)';

    if (cb.checked) {
      // Enforce max 2: if already 2 checked, remove oldest
      if (checked.length >= 2) {
        checked.shift();
      }
      checked.push({ id: roleId, title: title });
    } else {
      checked = checked.filter(function (c) { return c.id !== roleId; });
    }

    updateCheckboxes();
    updateBar();
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
})();
