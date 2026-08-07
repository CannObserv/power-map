/**
 * Tests for src/static/admin/orgs-merge.js (#250).
 *
 * Mirrors tests/js/people-merge.test.js. orgs-merge.js is a thin consumer of
 * the shared `window.createMergeMode` factory (merge-mode.js); these tests
 * exercise the factory through the Orgs config:
 *   - root #orgs-table (row id attr data-org-id)
 *   - URL: /admin/orgs/{a}/merge/{b}/
 *   - labels: "Select 2 organizations to merge:" / "Select 1 more:" /
 *     "Merge organizations:"
 *   - pagination-swap: .pagination--sticky hidden in merge mode, restored on exit
 *
 * Listener cleanup pattern same as people-merge.test.js — see docs/TESTING.md § Vitest test conventions
 */
import { readFileSync } from 'node:fs';
import { resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';

const __dirname = dirname(fileURLToPath(import.meta.url));
const scriptCode = readFileSync(
  resolve(__dirname, '../../src/static/admin/orgs-merge.js'),
  'utf-8',
);
// orgs-merge.js calls window.createMergeMode; define it once. The factory
// definition registers no listeners on its own.
const mergeModeCode = readFileSync(
  resolve(__dirname, '../../src/static/admin/merge-mode.js'),
  'utf-8',
);
eval(mergeModeCode);

let addSpy;

beforeEach(() => {
  addSpy = vi.spyOn(document, 'addEventListener');
});

afterEach(() => {
  for (const [type, fn] of addSpy.mock.calls) {
    document.removeEventListener(type, fn);
  }
  addSpy.mockRestore();
  document.body.innerHTML = '';
});

function setup({ numOrgs = 3 } = {}) {
  const rows = Array.from({ length: numOrgs }, (_, i) => {
    const n = i + 1;
    return `<tr data-title="Org ${n}" data-org-id="org-${n}">
      <td class="merge-col"><input type="checkbox" name="merge-select" value="org-${n}"></td>
      <td>Org ${n}</td>
    </tr>`;
  }).join('');

  document.body.innerHTML = `
    <span id="orgs-merge-btn-wrap" style="display:inline-block"><button id="orgs-merge-btn" class="btn btn--secondary">Merge</button></span>
    <div id="orgs-list-region">
      <table id="orgs-table">
        <thead><tr><th class="merge-col">Sel</th><th>Name</th></tr></thead>
        <tbody id="orgs-table-body">${rows}</tbody>
      </table>
      <div class="pagination--sticky">pagination here</div>
      <div id="orgs-merge-bar" style="display:none">
        <span class="merge-bar__label">Merge organizations:</span>
        <button class="merge-bar__keep-a" type="button"></button>
        <button class="merge-bar__keep-b" type="button"></button>
      </div>
    </div>
  `;

  eval(scriptCode); // no-eval disabled for test files in eslint.config.js
}

function checkboxes() {
  return Array.from(document.querySelectorAll('input[name="merge-select"]'));
}

function check(cb) {
  cb.checked = true;
  cb.dispatchEvent(new Event('change', { bubbles: true }));
}

function uncheck(cb) {
  cb.checked = false;
  cb.dispatchEvent(new Event('change', { bubbles: true }));
}

// ---------------------------------------------------------------------------
// Merge mode toggle
// ---------------------------------------------------------------------------

describe('merge mode toggle', () => {
  beforeEach(() => setup());

  it('starts outside merge mode', () => {
    expect(document.getElementById('orgs-table').dataset.mergeMode).toBeUndefined();
  });

  it('enters merge mode on button click', () => {
    const btn = document.getElementById('orgs-merge-btn');
    btn.click();
    expect(document.getElementById('orgs-table').dataset.mergeMode).toBe('true');
    expect(btn.textContent).toBe('Cancel merge');
    expect(btn.classList.contains('btn--ghost')).toBe(true);
    expect(btn.classList.contains('btn--secondary')).toBe(false);
  });

  it('exits merge mode on second click', () => {
    const btn = document.getElementById('orgs-merge-btn');
    btn.click();
    btn.click();
    expect(document.getElementById('orgs-table').dataset.mergeMode).toBeUndefined();
    expect(btn.textContent).toBe('Merge');
    expect(btn.classList.contains('btn--secondary')).toBe(true);
    expect(btn.classList.contains('btn--ghost')).toBe(false);
  });

  it('shows .merge-col cells in merge mode', () => {
    document.getElementById('orgs-merge-btn').click();
    document.querySelectorAll('.merge-col').forEach((col) => {
      expect(col.style.display).toBe('');
    });
  });

  it('hides .merge-col cells after exiting merge mode', () => {
    const btn = document.getElementById('orgs-merge-btn');
    btn.click();
    btn.click();
    document.querySelectorAll('.merge-col').forEach((col) => {
      expect(col.style.display).toBe('none');
    });
  });
});

// ---------------------------------------------------------------------------
// Merge bar visibility
// ---------------------------------------------------------------------------

describe('merge bar visibility', () => {
  beforeEach(() => {
    setup();
    document.getElementById('orgs-merge-btn').click();
  });

  it('bar is visible in merge mode', () => {
    expect(document.getElementById('orgs-merge-bar').style.display).toBe('flex');
  });

  it('bar is hidden after exiting merge mode', () => {
    document.getElementById('orgs-merge-btn').click();
    expect(document.getElementById('orgs-merge-bar').style.display).toBe('none');
  });
});

// ---------------------------------------------------------------------------
// Pagination swap
// ---------------------------------------------------------------------------

describe('pagination swap', () => {
  beforeEach(() => setup());

  it('hides .pagination--sticky when entering merge mode', () => {
    expect(document.querySelector('.pagination--sticky').style.display).toBe('');
    document.getElementById('orgs-merge-btn').click();
    expect(document.querySelector('.pagination--sticky').style.display).toBe('none');
  });

  it('restores .pagination--sticky when exiting merge mode', () => {
    const btn = document.getElementById('orgs-merge-btn');
    btn.click();
    btn.click();
    expect(document.querySelector('.pagination--sticky').style.display).toBe('');
  });

  it('restores .pagination--sticky on showFlash exit', () => {
    document.getElementById('orgs-merge-btn').click();
    document.dispatchEvent(new CustomEvent('showFlash'));
    expect(document.querySelector('.pagination--sticky').style.display).toBe('');
  });

  it('does not crash when .pagination--sticky is absent (short lists)', () => {
    document.body.innerHTML = '';
    document.body.innerHTML = `
      <span id="orgs-merge-btn-wrap"><button id="orgs-merge-btn" class="btn btn--secondary">Merge</button></span>
      <table id="orgs-table">
        <tbody id="orgs-table-body">
          <tr data-title="O1" data-org-id="o-1"><td class="merge-col"><input type="checkbox" name="merge-select" value="o-1"></td></tr>
          <tr data-title="O2" data-org-id="o-2"><td class="merge-col"><input type="checkbox" name="merge-select" value="o-2"></td></tr>
        </tbody>
      </table>
      <div id="orgs-merge-bar" style="display:none">
        <span class="merge-bar__label"></span>
        <button class="merge-bar__keep-a"></button>
        <button class="merge-bar__keep-b"></button>
      </div>
    `;
    eval(scriptCode);
    expect(() => document.getElementById('orgs-merge-btn').click()).not.toThrow();
  });
});

// ---------------------------------------------------------------------------
// Checkbox cap at 2
// ---------------------------------------------------------------------------

describe('checkbox cap at 2', () => {
  beforeEach(() => {
    setup({ numOrgs: 3 });
    document.getElementById('orgs-merge-btn').click();
  });

  it('allows checking 2 boxes', () => {
    const [cb1, cb2] = checkboxes();
    check(cb1);
    check(cb2);
    expect(cb1.checked).toBe(true);
    expect(cb2.checked).toBe(true);
  });

  it('disables remaining unchecked boxes once 2 are selected', () => {
    const [cb1, cb2, cb3] = checkboxes();
    check(cb1);
    check(cb2);
    expect(cb3.disabled).toBe(true);
  });

  it('re-enables boxes after unchecking one', () => {
    const [cb1, cb2, cb3] = checkboxes();
    check(cb1);
    check(cb2);
    uncheck(cb1);
    expect(cb3.disabled).toBe(false);
  });

  it('clears all checks on exit from merge mode', () => {
    const [cb1, cb2] = checkboxes();
    check(cb1);
    check(cb2);
    document.getElementById('orgs-merge-btn').click();
    checkboxes().forEach((cb) => expect(cb.checked).toBe(false));
  });
});

// ---------------------------------------------------------------------------
// Bar label + button state at 0 / 1 / 2 selections
// ---------------------------------------------------------------------------

describe('bar at 0 selections', () => {
  beforeEach(() => {
    setup();
    document.getElementById('orgs-merge-btn').click();
  });

  it('shows "Select 2 organizations to merge:" label', () => {
    expect(document.querySelector('.merge-bar__label').textContent).toBe(
      'Select 2 organizations to merge:',
    );
  });

  it('both keep buttons are disabled with placeholder text', () => {
    const btnA = document.querySelector('.merge-bar__keep-a');
    const btnB = document.querySelector('.merge-bar__keep-b');
    expect(btnA.disabled).toBe(true);
    expect(btnB.disabled).toBe(true);
    expect(btnA.textContent).toBe('—');
    expect(btnB.textContent).toBe('—');
  });
});

describe('bar at 1 selection', () => {
  beforeEach(() => {
    setup();
    document.getElementById('orgs-merge-btn').click();
    check(checkboxes()[0]);
  });

  it('shows "Select 1 more:" label', () => {
    expect(document.querySelector('.merge-bar__label').textContent).toBe('Select 1 more:');
  });

  it('keep-a shows selected org name and is disabled', () => {
    const btnA = document.querySelector('.merge-bar__keep-a');
    expect(btnA.textContent).toBe('Selected: "Org 1"');
    expect(btnA.disabled).toBe(true);
  });

  it('keep-b remains disabled with placeholder text', () => {
    const btnB = document.querySelector('.merge-bar__keep-b');
    expect(btnB.textContent).toBe('—');
    expect(btnB.disabled).toBe(true);
  });
});

describe('bar at 2 selections', () => {
  beforeEach(() => {
    setup({ numOrgs: 2 });
    document.getElementById('orgs-merge-btn').click();
    const [cb1, cb2] = checkboxes();
    check(cb1);
    check(cb2);
  });

  it('shows "Merge organizations:" label', () => {
    expect(document.querySelector('.merge-bar__label').textContent).toBe('Merge organizations:');
  });

  it('both keep buttons are enabled', () => {
    expect(document.querySelector('.merge-bar__keep-a').disabled).toBe(false);
    expect(document.querySelector('.merge-bar__keep-b').disabled).toBe(false);
  });

  it('keep-a shows name of first selected org', () => {
    expect(document.querySelector('.merge-bar__keep-a').textContent).toBe('Keep "Org 1"');
  });

  it('keep-b shows name of second selected org', () => {
    expect(document.querySelector('.merge-bar__keep-b').textContent).toBe('Keep "Org 2"');
  });

  it('keep-a hx-get opens the preview modal: winner org-1, loser org-2', () => {
    expect(document.querySelector('.merge-bar__keep-a').getAttribute('hx-get')).toBe(
      '/admin/orgs/org-1/merge-preview/org-2/?winner=org-1&ctx=list',
    );
  });

  it('keep-b hx-get opens the preview modal: winner org-2, loser org-1', () => {
    expect(document.querySelector('.merge-bar__keep-b').getAttribute('hx-get')).toBe(
      '/admin/orgs/org-2/merge-preview/org-1/?winner=org-2&ctx=list',
    );
  });

  it('keep-a hx-target is the shared modal portal', () => {
    expect(document.querySelector('.merge-bar__keep-a').getAttribute('hx-target')).toBe(
      '#merge-modal-portal',
    );
  });

  it('keep-b hx-target is the shared modal portal', () => {
    expect(document.querySelector('.merge-bar__keep-b').getAttribute('hx-target')).toBe(
      '#merge-modal-portal',
    );
  });

  it('keep buttons carry no hx-confirm (the modal is the confirm step now)', () => {
    expect(document.querySelector('.merge-bar__keep-a').getAttribute('hx-confirm')).toBeNull();
    expect(document.querySelector('.merge-bar__keep-b').getAttribute('hx-confirm')).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// showFlash event exits merge mode
// ---------------------------------------------------------------------------

describe('showFlash exits merge mode', () => {
  beforeEach(() => {
    setup();
    document.getElementById('orgs-merge-btn').click();
  });

  it('exits merge mode when showFlash fires while in merge mode', () => {
    expect(document.getElementById('orgs-table').dataset.mergeMode).toBe('true');
    document.dispatchEvent(new CustomEvent('showFlash'));
    expect(document.getElementById('orgs-table').dataset.mergeMode).toBeUndefined();
  });

  it('resets button text and classes on showFlash exit', () => {
    const btn = document.getElementById('orgs-merge-btn');
    document.dispatchEvent(new CustomEvent('showFlash'));
    expect(btn.textContent).toBe('Merge');
    expect(btn.classList.contains('btn--secondary')).toBe(true);
    expect(btn.classList.contains('btn--ghost')).toBe(false);
  });

  it('clears checked selections on showFlash exit', () => {
    const [cb1, cb2] = checkboxes();
    check(cb1);
    check(cb2);
    document.dispatchEvent(new CustomEvent('showFlash'));
    checkboxes().forEach((cb) => expect(cb.checked).toBe(false));
  });

  it('does nothing when showFlash fires outside merge mode', () => {
    document.getElementById('orgs-merge-btn').click();
    document.dispatchEvent(new CustomEvent('showFlash'));
    expect(document.getElementById('orgs-table').dataset.mergeMode).toBeUndefined();
  });
});

// ---------------------------------------------------------------------------
// Survives region swap
// ---------------------------------------------------------------------------

function dispatchAfterSwap(target) {
  target.dispatchEvent(new CustomEvent('htmx:afterSwap', { bubbles: true }));
}

function rebuildRegionInPlace({ numOrgs = 3 } = {}) {
  var region = document.getElementById('orgs-list-region');
  var rows = Array.from({ length: numOrgs }, (_, i) => {
    var n = i + 100; // distinct IDs so we can tell new from old
    return `<tr data-title="Org ${n}" data-org-id="org-${n}">
      <td class="merge-col"><input type="checkbox" name="merge-select" value="org-${n}"></td>
      <td>Org ${n}</td>
    </tr>`;
  }).join('');
  region.innerHTML = `
    <table id="orgs-table">
      <thead><tr><th class="merge-col">Sel</th><th>Name</th></tr></thead>
      <tbody id="orgs-table-body">${rows}</tbody>
    </table>
    <div class="pagination--sticky">pagination here</div>
    <div id="orgs-merge-bar" style="display:none">
      <span class="merge-bar__label">Merge organizations:</span>
      <button class="merge-bar__keep-a" type="button"></button>
      <button class="merge-bar__keep-b" type="button"></button>
    </div>
  `;
  dispatchAfterSwap(region);
}

describe('survives region swap', () => {
  beforeEach(() => setup({ numOrgs: 3 }));

  it('checkbox change still drives merge bar after a swap', () => {
    document.getElementById('orgs-merge-btn').click();
    rebuildRegionInPlace({ numOrgs: 3 });

    var cb = document.querySelector('#orgs-table input[name="merge-select"]');
    cb.checked = true;
    cb.dispatchEvent(new Event('change', { bubbles: true }));

    expect(document.querySelector('.merge-bar__label').textContent).toBe('Select 1 more:');
  });

  it('merge-col cells in the new table are visible if mode was active before swap', () => {
    document.getElementById('orgs-merge-btn').click();
    rebuildRegionInPlace({ numOrgs: 3 });

    document.querySelectorAll('#orgs-table .merge-col').forEach((col) => {
      expect(col.style.display).toBe('');
    });
  });

  it('pagination stays hidden after swap if merge mode was active before', () => {
    document.getElementById('orgs-merge-btn').click();
    rebuildRegionInPlace({ numOrgs: 3 });

    expect(document.querySelector('.pagination--sticky').style.display).toBe('none');
  });

  it('clears stale selection on swap (selection does not persist into a new view)', () => {
    document.getElementById('orgs-merge-btn').click();
    check(checkboxes()[0]);
    expect(document.querySelector('.merge-bar__label').textContent).toBe('Select 1 more:');

    rebuildRegionInPlace({ numOrgs: 3 });

    expect(document.querySelector('.merge-bar__label').textContent).toBe(
      'Select 2 organizations to merge:',
    );
  });

  it('merge button re-syncs disabled state after swap leaves <2 rows', () => {
    var btn = document.getElementById('orgs-merge-btn');
    expect(btn.disabled).toBe(false);

    var region = document.getElementById('orgs-list-region');
    region.innerHTML = `
      <table id="orgs-table">
        <tbody id="orgs-table-body">
          <tr data-title="Solo" data-org-id="org-solo">
            <td class="merge-col"><input type="checkbox" name="merge-select" value="org-solo"></td>
            <td>Solo</td>
          </tr>
        </tbody>
      </table>
      <div id="orgs-merge-bar" style="display:none">
        <span class="merge-bar__label"></span>
        <button class="merge-bar__keep-a"></button>
        <button class="merge-bar__keep-b"></button>
      </div>
    `;
    dispatchAfterSwap(region);

    expect(btn.disabled).toBe(true);
  });

  it('checkbox handler ignores changes outside #orgs-table (delegation safety)', () => {
    document.getElementById('orgs-merge-btn').click();
    var rogue = document.createElement('input');
    rogue.type = 'checkbox';
    rogue.name = 'merge-select';
    rogue.value = 'rogue-id';
    document.body.appendChild(rogue);

    rogue.checked = true;
    rogue.dispatchEvent(new Event('change', { bubbles: true }));

    expect(document.querySelector('.merge-bar__label').textContent).toBe(
      'Select 2 organizations to merge:',
    );
  });
});

// ---------------------------------------------------------------------------
// Merge button disabled state (syncMergeBtn)
// ---------------------------------------------------------------------------

describe('merge button disabled state', () => {
  it('is disabled with not-allowed cursor on wrapper when there are 0 orgs', () => {
    setup({ numOrgs: 0 });
    const btn = document.getElementById('orgs-merge-btn');
    const wrap = document.getElementById('orgs-merge-btn-wrap');
    expect(btn.disabled).toBe(true);
    expect(wrap.style.cursor).toBe('not-allowed');
    expect(wrap.title).toBe('At least 2 organizations required to merge');
  });

  it('is disabled when there is exactly 1 org', () => {
    setup({ numOrgs: 1 });
    const btn = document.getElementById('orgs-merge-btn');
    const wrap = document.getElementById('orgs-merge-btn-wrap');
    expect(btn.disabled).toBe(true);
    expect(wrap.style.cursor).toBe('not-allowed');
  });

  it('is enabled with no cursor override on wrapper when there are exactly 2 orgs', () => {
    setup({ numOrgs: 2 });
    const btn = document.getElementById('orgs-merge-btn');
    const wrap = document.getElementById('orgs-merge-btn-wrap');
    expect(btn.disabled).toBe(false);
    expect(wrap.style.cursor).toBe('');
    expect(wrap.title).toBe('');
  });

  it('is enabled when there are more than 2 orgs', () => {
    setup({ numOrgs: 3 });
    const btn = document.getElementById('orgs-merge-btn');
    expect(btn.disabled).toBe(false);
  });

  it('re-evaluates after htmx:afterSwap inside the list region', () => {
    setup({ numOrgs: 2 });
    const btn = document.getElementById('orgs-merge-btn');
    const wrap = document.getElementById('orgs-merge-btn-wrap');
    expect(btn.disabled).toBe(false);

    const tbody = document.querySelector('#orgs-table tbody');
    tbody.innerHTML = `<tr data-title="O1" data-org-id="org-1">
      <td class="merge-col"><input type="checkbox" name="merge-select" value="org-1"></td>
      <td>O1</td>
    </tr>`;
    document
      .getElementById('orgs-table')
      .dispatchEvent(new CustomEvent('htmx:afterSwap', { bubbles: true }));

    expect(btn.disabled).toBe(true);
    expect(wrap.style.cursor).toBe('not-allowed');
  });
});

// ---------------------------------------------------------------------------
// hx-boost survival (#249/#250) — eval on a page with NO orgs list
// ---------------------------------------------------------------------------

function orgsListMarkup({ numOrgs = 3 } = {}) {
  const rows = Array.from({ length: numOrgs }, (_, i) => {
    const n = i + 1;
    return `<tr data-title="Org ${n}" data-org-id="org-${n}">
      <td class="merge-col"><input type="checkbox" name="merge-select" value="org-${n}"></td>
      <td>Org ${n}</td>
    </tr>`;
  }).join('');
  return `
    <span id="orgs-merge-btn-wrap" style="display:inline-block"><button id="orgs-merge-btn" class="btn btn--secondary">Merge</button></span>
    <div id="orgs-list-region">
      <table id="orgs-table">
        <thead><tr><th class="merge-col">Sel</th><th>Name</th></tr></thead>
        <tbody id="orgs-table-body">${rows}</tbody>
      </table>
      <div class="pagination--sticky">pagination here</div>
      <div id="orgs-merge-bar" style="display:none">
        <span class="merge-bar__label">Merge organizations:</span>
        <button class="merge-bar__keep-a" type="button"></button>
        <button class="merge-bar__keep-b" type="button"></button>
      </div>
    </div>
  `;
}

describe('hx-boost survival', () => {
  beforeEach(() => {
    document.body.innerHTML = '';
    eval(scriptCode);
  });

  function dispatchBoostArrival() {
    document.body.dispatchEvent(new CustomEvent('htmx:load', { bubbles: true }));
  }

  it('does not throw when evaluated on a page without the orgs list', () => {
    expect(() => {
      document.dispatchEvent(new CustomEvent('showFlash'));
      document.body.dispatchEvent(new CustomEvent('htmx:afterSwap', { bubbles: true }));
      dispatchBoostArrival();
    }).not.toThrow();
  });

  it('drives the Merge button delivered by a boosted navigation', () => {
    document.body.innerHTML = orgsListMarkup({ numOrgs: 3 });
    dispatchBoostArrival();

    const btn = document.getElementById('orgs-merge-btn');
    btn.click();
    expect(document.getElementById('orgs-table').dataset.mergeMode).toBe('true');
  });

  it('syncs the Merge button disabled state on boosted arrival (<2 rows)', () => {
    document.body.innerHTML = orgsListMarkup({ numOrgs: 1 });
    dispatchBoostArrival();
    expect(document.getElementById('orgs-merge-btn').disabled).toBe(true);
  });

  it('starts a freshly-arrived orgs list outside merge mode (no stale state)', () => {
    document.body.innerHTML = orgsListMarkup({ numOrgs: 3 });
    dispatchBoostArrival();
    document.getElementById('orgs-merge-btn').click();
    check(checkboxes()[0]);
    expect(document.getElementById('orgs-table').dataset.mergeMode).toBe('true');

    document.body.innerHTML = orgsListMarkup({ numOrgs: 3 });
    dispatchBoostArrival();
    expect(document.getElementById('orgs-table').dataset.mergeMode).toBeUndefined();
    expect(document.getElementById('orgs-merge-bar').style.display).toBe('none');

    document.getElementById('orgs-merge-btn').click();
    expect(document.querySelector('.merge-bar__label').textContent).toBe(
      'Select 2 organizations to merge:',
    );
  });

  it('a region-only htmx:load preserves merge mode', () => {
    document.body.innerHTML = orgsListMarkup({ numOrgs: 3 });
    dispatchBoostArrival();
    document.getElementById('orgs-merge-btn').click();
    expect(document.getElementById('orgs-table').dataset.mergeMode).toBe('true');

    document
      .getElementById('orgs-list-region')
      .dispatchEvent(new CustomEvent('htmx:load', { bubbles: true }));

    expect(document.getElementById('orgs-table').dataset.mergeMode).toBe('true');
  });

  it('a region-only htmx:load preserves an in-progress selection', () => {
    document.body.innerHTML = orgsListMarkup({ numOrgs: 3 });
    dispatchBoostArrival();
    document.getElementById('orgs-merge-btn').click();
    check(checkboxes()[0]);
    expect(document.querySelector('.merge-bar__label').textContent).toBe('Select 1 more:');

    document
      .getElementById('orgs-list-region')
      .dispatchEvent(new CustomEvent('htmx:load', { bubbles: true }));

    expect(document.querySelector('.merge-bar__label').textContent).toBe('Select 1 more:');
  });
});
