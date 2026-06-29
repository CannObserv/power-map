/**
 * Tests for src/static/admin/people-merge.js
 *
 * Mirrors tests/js/role-merge.test.js. Diff vs role-merge:
 *   - root #people-table (no data-org-id — URL has no org segment)
 *   - row data attrs: data-person-id, data-title
 *   - URL: /admin/people/{a}/merge/{b}/
 *   - labels: "Select 2 people to merge:" / "Select 1 more:" / "Merge people:"
 *   - no inline filter
 *   - pagination-swap: .pagination--sticky hidden in merge mode, restored on exit
 *
 * Listener cleanup pattern same as role-merge.test.js — see docs/STYLE.md §33.
 */
import { readFileSync } from 'node:fs';
import { resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';

const __dirname = dirname(fileURLToPath(import.meta.url));
const scriptCode = readFileSync(
  resolve(__dirname, '../../src/static/admin/people-merge.js'),
  'utf-8',
);

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

function setup({ numPeople = 3 } = {}) {
  const rows = Array.from({ length: numPeople }, (_, i) => {
    const n = i + 1;
    return `<tr data-title="Person ${n}" data-person-id="person-${n}">
      <td class="merge-col"><input type="checkbox" name="merge-select" value="person-${n}"></td>
      <td>Person ${n}</td>
    </tr>`;
  }).join('');

  document.body.innerHTML = `
    <span id="people-merge-btn-wrap" style="display:inline-block"><button id="people-merge-btn" class="btn btn--secondary">Merge</button></span>
    <div id="people-list-region">
      <table id="people-table">
        <thead><tr><th class="merge-col">Sel</th><th>Name</th></tr></thead>
        <tbody id="people-table-body">${rows}</tbody>
      </table>
      <div class="pagination--sticky">pagination here</div>
      <div id="people-merge-bar" style="display:none">
        <span class="merge-bar__label">Merge people:</span>
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
    expect(document.getElementById('people-table').dataset.mergeMode).toBeUndefined();
  });

  it('enters merge mode on button click', () => {
    const btn = document.getElementById('people-merge-btn');
    btn.click();
    expect(document.getElementById('people-table').dataset.mergeMode).toBe('true');
    expect(btn.textContent).toBe('Cancel merge');
    expect(btn.classList.contains('btn--ghost')).toBe(true);
    expect(btn.classList.contains('btn--secondary')).toBe(false);
  });

  it('exits merge mode on second click', () => {
    const btn = document.getElementById('people-merge-btn');
    btn.click();
    btn.click();
    expect(document.getElementById('people-table').dataset.mergeMode).toBeUndefined();
    expect(btn.textContent).toBe('Merge');
    expect(btn.classList.contains('btn--secondary')).toBe(true);
    expect(btn.classList.contains('btn--ghost')).toBe(false);
  });

  it('shows .merge-col cells in merge mode', () => {
    document.getElementById('people-merge-btn').click();
    document.querySelectorAll('.merge-col').forEach((col) => {
      expect(col.style.display).toBe('');
    });
  });

  it('hides .merge-col cells after exiting merge mode', () => {
    const btn = document.getElementById('people-merge-btn');
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
    document.getElementById('people-merge-btn').click();
  });

  it('bar is visible in merge mode', () => {
    expect(document.getElementById('people-merge-bar').style.display).toBe('flex');
  });

  it('bar is hidden after exiting merge mode', () => {
    document.getElementById('people-merge-btn').click();
    expect(document.getElementById('people-merge-bar').style.display).toBe('none');
  });
});

// ---------------------------------------------------------------------------
// Pagination swap — distinguishing feature vs role-merge
// ---------------------------------------------------------------------------

describe('pagination swap', () => {
  beforeEach(() => setup());

  it('hides .pagination--sticky when entering merge mode', () => {
    expect(document.querySelector('.pagination--sticky').style.display).toBe('');
    document.getElementById('people-merge-btn').click();
    expect(document.querySelector('.pagination--sticky').style.display).toBe('none');
  });

  it('restores .pagination--sticky when exiting merge mode', () => {
    const btn = document.getElementById('people-merge-btn');
    btn.click();
    btn.click();
    expect(document.querySelector('.pagination--sticky').style.display).toBe('');
  });

  it('restores .pagination--sticky on showFlash exit', () => {
    document.getElementById('people-merge-btn').click();
    document.dispatchEvent(new CustomEvent('showFlash'));
    expect(document.querySelector('.pagination--sticky').style.display).toBe('');
  });

  it('does not crash when .pagination--sticky is absent (short lists)', () => {
    document.body.innerHTML = '';
    // Custom fixture with no .pagination--sticky element
    document.body.innerHTML = `
      <span id="people-merge-btn-wrap"><button id="people-merge-btn" class="btn btn--secondary">Merge</button></span>
      <table id="people-table">
        <tbody id="people-table-body">
          <tr data-title="P1" data-person-id="p-1"><td class="merge-col"><input type="checkbox" name="merge-select" value="p-1"></td></tr>
          <tr data-title="P2" data-person-id="p-2"><td class="merge-col"><input type="checkbox" name="merge-select" value="p-2"></td></tr>
        </tbody>
      </table>
      <div id="people-merge-bar" style="display:none">
        <span class="merge-bar__label"></span>
        <button class="merge-bar__keep-a"></button>
        <button class="merge-bar__keep-b"></button>
      </div>
    `;
    eval(scriptCode);
    expect(() => document.getElementById('people-merge-btn').click()).not.toThrow();
  });
});

// ---------------------------------------------------------------------------
// Checkbox cap at 2
// ---------------------------------------------------------------------------

describe('checkbox cap at 2', () => {
  beforeEach(() => {
    setup({ numPeople: 3 });
    document.getElementById('people-merge-btn').click();
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
    document.getElementById('people-merge-btn').click();
    checkboxes().forEach((cb) => expect(cb.checked).toBe(false));
  });
});

// ---------------------------------------------------------------------------
// Bar label + button state at 0 selections
// ---------------------------------------------------------------------------

describe('bar at 0 selections', () => {
  beforeEach(() => {
    setup();
    document.getElementById('people-merge-btn').click();
  });

  it('shows "Select 2 people to merge:" label', () => {
    expect(document.querySelector('.merge-bar__label').textContent).toBe(
      'Select 2 people to merge:',
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

// ---------------------------------------------------------------------------
// Bar label + button state at 1 selection
// ---------------------------------------------------------------------------

describe('bar at 1 selection', () => {
  beforeEach(() => {
    setup();
    document.getElementById('people-merge-btn').click();
    check(checkboxes()[0]);
  });

  it('shows "Select 1 more:" label', () => {
    expect(document.querySelector('.merge-bar__label').textContent).toBe('Select 1 more:');
  });

  it('keep-a shows selected person name and is disabled', () => {
    const btnA = document.querySelector('.merge-bar__keep-a');
    expect(btnA.textContent).toBe('Selected: "Person 1"');
    expect(btnA.disabled).toBe(true);
  });

  it('keep-b remains disabled with placeholder text', () => {
    const btnB = document.querySelector('.merge-bar__keep-b');
    expect(btnB.textContent).toBe('—');
    expect(btnB.disabled).toBe(true);
  });
});

// ---------------------------------------------------------------------------
// Bar label, button state, and URL construction at 2 selections
// ---------------------------------------------------------------------------

describe('bar at 2 selections', () => {
  beforeEach(() => {
    setup({ numPeople: 2 });
    document.getElementById('people-merge-btn').click();
    const [cb1, cb2] = checkboxes();
    check(cb1);
    check(cb2);
  });

  it('shows "Merge people:" label', () => {
    expect(document.querySelector('.merge-bar__label').textContent).toBe('Merge people:');
  });

  it('both keep buttons are enabled', () => {
    expect(document.querySelector('.merge-bar__keep-a').disabled).toBe(false);
    expect(document.querySelector('.merge-bar__keep-b').disabled).toBe(false);
  });

  it('keep-a shows name of first selected person', () => {
    expect(document.querySelector('.merge-bar__keep-a').textContent).toBe('Keep "Person 1"');
  });

  it('keep-b shows name of second selected person', () => {
    expect(document.querySelector('.merge-bar__keep-b').textContent).toBe('Keep "Person 2"');
  });

  it('keep-a hx-post: keep person-1, discard person-2', () => {
    expect(document.querySelector('.merge-bar__keep-a').getAttribute('hx-post')).toBe(
      '/admin/people/person-1/merge/person-2/',
    );
  });

  it('keep-b hx-post: keep person-2, discard person-1', () => {
    expect(document.querySelector('.merge-bar__keep-b').getAttribute('hx-post')).toBe(
      '/admin/people/person-2/merge/person-1/',
    );
  });

  it('keep-a hx-target swaps the whole list region (caption + pagination stay in sync)', () => {
    expect(document.querySelector('.merge-bar__keep-a').getAttribute('hx-target')).toBe(
      '#people-list-region',
    );
  });

  it('keep-b hx-target swaps the whole list region (caption + pagination stay in sync)', () => {
    expect(document.querySelector('.merge-bar__keep-b').getAttribute('hx-target')).toBe(
      '#people-list-region',
    );
  });

  it('keep-a hx-confirm names both people', () => {
    const msg = document.querySelector('.merge-bar__keep-a').getAttribute('hx-confirm');
    expect(msg).toContain('"Person 2"');
    expect(msg).toContain('"Person 1"');
  });

  it('keep-b hx-confirm names both people', () => {
    const msg = document.querySelector('.merge-bar__keep-b').getAttribute('hx-confirm');
    expect(msg).toContain('"Person 1"');
    expect(msg).toContain('"Person 2"');
  });
});

// ---------------------------------------------------------------------------
// showFlash event exits merge mode
// ---------------------------------------------------------------------------

describe('showFlash exits merge mode', () => {
  beforeEach(() => {
    setup();
    document.getElementById('people-merge-btn').click();
  });

  it('exits merge mode when showFlash fires while in merge mode', () => {
    expect(document.getElementById('people-table').dataset.mergeMode).toBe('true');
    document.dispatchEvent(new CustomEvent('showFlash'));
    expect(document.getElementById('people-table').dataset.mergeMode).toBeUndefined();
  });

  it('resets button text and classes on showFlash exit', () => {
    const btn = document.getElementById('people-merge-btn');
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
    document.getElementById('people-merge-btn').click();
    document.dispatchEvent(new CustomEvent('showFlash'));
    expect(document.getElementById('people-table').dataset.mergeMode).toBeUndefined();
  });
});

// ---------------------------------------------------------------------------
// Survives region swap (CR #1 — search/filter/pagination re-renders the
// whole #people-list-region; cached element refs would break merge UI).
// ---------------------------------------------------------------------------

function dispatchAfterSwap(target) {
  // htmx:afterSwap bubbles; dispatching with bubbles:true lets a
  // document-level listener catch it.
  target.dispatchEvent(new CustomEvent('htmx:afterSwap', { bubbles: true }));
}

function rebuildRegionInPlace({ numPeople = 3 } = {}) {
  // Simulate what HTMX does on a search/filter swap: replace the inner
  // HTML of #people-list-region with fresh markup that re-uses the same
  // IDs but produces new DOM nodes.
  var region = document.getElementById('people-list-region');
  var rows = Array.from({ length: numPeople }, (_, i) => {
    var n = i + 100; // distinct IDs so we can tell new from old
    return `<tr data-title="Person ${n}" data-person-id="person-${n}">
      <td class="merge-col"><input type="checkbox" name="merge-select" value="person-${n}"></td>
      <td>Person ${n}</td>
    </tr>`;
  }).join('');
  region.innerHTML = `
    <table id="people-table">
      <thead><tr><th class="merge-col">Sel</th><th>Name</th></tr></thead>
      <tbody id="people-table-body">${rows}</tbody>
    </table>
    <div class="pagination--sticky">pagination here</div>
    <div id="people-merge-bar" style="display:none">
      <span class="merge-bar__label">Merge people:</span>
      <button class="merge-bar__keep-a" type="button"></button>
      <button class="merge-bar__keep-b" type="button"></button>
    </div>
  `;
  dispatchAfterSwap(region);
}

describe('survives region swap', () => {
  beforeEach(() => setup({ numPeople: 3 }));

  it('checkbox change still drives merge bar after a swap', () => {
    document.getElementById('people-merge-btn').click(); // enter merge mode
    rebuildRegionInPlace({ numPeople: 3 });

    // Click a checkbox in the NEW table — the IIFE's cached refs would be
    // stale, but document-level delegation must still catch this.
    var cb = document.querySelector('#people-table input[name="merge-select"]');
    cb.checked = true;
    cb.dispatchEvent(new Event('change', { bubbles: true }));

    expect(document.querySelector('.merge-bar__label').textContent).toBe('Select 1 more:');
  });

  it('merge-col cells in the new table are visible if mode was active before swap', () => {
    document.getElementById('people-merge-btn').click();
    rebuildRegionInPlace({ numPeople: 3 });

    document.querySelectorAll('#people-table .merge-col').forEach((col) => {
      expect(col.style.display).toBe('');
    });
  });

  it('pagination stays hidden after swap if merge mode was active before', () => {
    document.getElementById('people-merge-btn').click();
    rebuildRegionInPlace({ numPeople: 3 });

    expect(document.querySelector('.pagination--sticky').style.display).toBe('none');
  });

  it('clears stale selection on swap (selection does not persist into a new view)', () => {
    document.getElementById('people-merge-btn').click();
    check(checkboxes()[0]); // select Person 1
    expect(document.querySelector('.merge-bar__label').textContent).toBe('Select 1 more:');

    rebuildRegionInPlace({ numPeople: 3 });

    // After the swap the new rows are different people; selection is wiped
    // so the bar returns to the 0-selection prompt.
    expect(document.querySelector('.merge-bar__label').textContent).toBe(
      'Select 2 people to merge:',
    );
  });

  it('merge button re-syncs disabled state after swap leaves <2 rows', () => {
    var btn = document.getElementById('people-merge-btn');
    expect(btn.disabled).toBe(false);

    // Swap in a region with only 1 row
    var region = document.getElementById('people-list-region');
    region.innerHTML = `
      <table id="people-table">
        <tbody id="people-table-body">
          <tr data-title="Solo" data-person-id="person-solo">
            <td class="merge-col"><input type="checkbox" name="merge-select" value="person-solo"></td>
            <td>Solo</td>
          </tr>
        </tbody>
      </table>
      <div id="people-merge-bar" style="display:none">
        <span class="merge-bar__label"></span>
        <button class="merge-bar__keep-a"></button>
        <button class="merge-bar__keep-b"></button>
      </div>
    `;
    dispatchAfterSwap(region);

    expect(btn.disabled).toBe(true);
  });

  it('checkbox handler ignores changes outside #people-table (delegation safety)', () => {
    document.getElementById('people-merge-btn').click();
    // Inject a sibling checkbox with the same name outside the table.
    var rogue = document.createElement('input');
    rogue.type = 'checkbox';
    rogue.name = 'merge-select';
    rogue.value = 'rogue-id';
    document.body.appendChild(rogue);

    rogue.checked = true;
    rogue.dispatchEvent(new Event('change', { bubbles: true }));

    // Rogue checkbox must NOT enter selection — bar still at 0 prompt.
    expect(document.querySelector('.merge-bar__label').textContent).toBe(
      'Select 2 people to merge:',
    );
  });
});

// ---------------------------------------------------------------------------
// Merge button disabled state (syncMergeBtn)
// ---------------------------------------------------------------------------

describe('merge button disabled state', () => {
  it('is disabled with not-allowed cursor on wrapper when there are 0 people', () => {
    setup({ numPeople: 0 });
    const btn = document.getElementById('people-merge-btn');
    const wrap = document.getElementById('people-merge-btn-wrap');
    expect(btn.disabled).toBe(true);
    expect(wrap.style.cursor).toBe('not-allowed');
    expect(wrap.title).toBe('At least 2 people required to merge');
  });

  it('is disabled when there is exactly 1 person', () => {
    setup({ numPeople: 1 });
    const btn = document.getElementById('people-merge-btn');
    const wrap = document.getElementById('people-merge-btn-wrap');
    expect(btn.disabled).toBe(true);
    expect(wrap.style.cursor).toBe('not-allowed');
  });

  it('is enabled with no cursor override on wrapper when there are exactly 2 people', () => {
    setup({ numPeople: 2 });
    const btn = document.getElementById('people-merge-btn');
    const wrap = document.getElementById('people-merge-btn-wrap');
    expect(btn.disabled).toBe(false);
    expect(wrap.style.cursor).toBe('');
    expect(wrap.title).toBe('');
  });

  it('is enabled when there are more than 2 people', () => {
    setup({ numPeople: 3 });
    const btn = document.getElementById('people-merge-btn');
    expect(btn.disabled).toBe(false);
  });

  it('re-evaluates after htmx:afterSwap inside the list region', () => {
    setup({ numPeople: 2 });
    const btn = document.getElementById('people-merge-btn');
    const wrap = document.getElementById('people-merge-btn-wrap');
    expect(btn.disabled).toBe(false);

    const tbody = document.querySelector('#people-table tbody');
    tbody.innerHTML = `<tr data-title="P1" data-person-id="person-1">
      <td class="merge-col"><input type="checkbox" name="merge-select" value="person-1"></td>
      <td>P1</td>
    </tr>`;
    // Event must bubble to the document-level listener (the JS now uses
    // delegation so it survives region swaps).
    document
      .getElementById('people-table')
      .dispatchEvent(new CustomEvent('htmx:afterSwap', { bubbles: true }));

    expect(btn.disabled).toBe(true);
    expect(wrap.style.cursor).toBe('not-allowed');
  });
});

// ---------------------------------------------------------------------------
// hx-boost survival (#249)
//
// people-merge.js is now loaded site-wide from base.html (the admin shell is
// hx-boost="true", which strips <head> from boosted responses — a script in
// the People list's extra_head never ran on a boosted nav, leaving Merge an
// inert no-op). The script must therefore:
//   - register its listeners even when the People list is absent at eval time
//     (e.g. the script first loads on the dashboard), and
//   - drive the Merge button when the list arrives later via a boosted nav.
// Achieved with document-level delegation + module-scope state (no per-element
// binding at eval time).
// ---------------------------------------------------------------------------

function peopleListMarkup({ numPeople = 3 } = {}) {
  const rows = Array.from({ length: numPeople }, (_, i) => {
    const n = i + 1;
    return `<tr data-title="Person ${n}" data-person-id="person-${n}">
      <td class="merge-col"><input type="checkbox" name="merge-select" value="person-${n}"></td>
      <td>Person ${n}</td>
    </tr>`;
  }).join('');
  return `
    <span id="people-merge-btn-wrap" style="display:inline-block"><button id="people-merge-btn" class="btn btn--secondary">Merge</button></span>
    <div id="people-list-region">
      <table id="people-table">
        <thead><tr><th class="merge-col">Sel</th><th>Name</th></tr></thead>
        <tbody id="people-table-body">${rows}</tbody>
      </table>
      <div class="pagination--sticky">pagination here</div>
      <div id="people-merge-bar" style="display:none">
        <span class="merge-bar__label">Merge people:</span>
        <button class="merge-bar__keep-a" type="button"></button>
        <button class="merge-bar__keep-b" type="button"></button>
      </div>
    </div>
  `;
}

describe('hx-boost survival (#249)', () => {
  // Eval on a page with NO People list (e.g. the dashboard), as happens when
  // the script loads site-wide before the user navigates to the People list.
  beforeEach(() => {
    document.body.innerHTML = '';
    eval(scriptCode);
  });

  it('does not throw when evaluated on a page without the People list', () => {
    expect(() => {
      document.dispatchEvent(new CustomEvent('showFlash'));
      document.body.dispatchEvent(new CustomEvent('htmx:afterSwap', { bubbles: true }));
    }).not.toThrow();
  });

  it('drives the Merge button delivered by a boosted navigation', () => {
    document.body.innerHTML = peopleListMarkup({ numPeople: 3 });
    // Boosted full-page arrival: htmx swaps <body>; afterSwap bubbles to document.
    document.body.dispatchEvent(new CustomEvent('htmx:afterSwap', { bubbles: true }));

    const btn = document.getElementById('people-merge-btn');
    btn.click();
    expect(document.getElementById('people-table').dataset.mergeMode).toBe('true');
  });

  it('syncs the Merge button disabled state on boosted arrival (<2 rows)', () => {
    document.body.innerHTML = peopleListMarkup({ numPeople: 1 });
    document.body.dispatchEvent(new CustomEvent('htmx:afterSwap', { bubbles: true }));
    expect(document.getElementById('people-merge-btn').disabled).toBe(true);
  });

  it('starts a freshly-arrived People list outside merge mode (no stale state)', () => {
    // First arrival: enter merge mode + select a person.
    document.body.innerHTML = peopleListMarkup({ numPeople: 3 });
    document.body.dispatchEvent(new CustomEvent('htmx:afterSwap', { bubbles: true }));
    document.getElementById('people-merge-btn').click();
    check(checkboxes()[0]);
    expect(document.getElementById('people-table').dataset.mergeMode).toBe('true');

    // Navigate away and back (boosted): a brand-new People list arrives. It must
    // start clean — merge mode off and the action bar hidden.
    document.body.innerHTML = peopleListMarkup({ numPeople: 3 });
    document.body.dispatchEvent(new CustomEvent('htmx:afterSwap', { bubbles: true }));
    expect(document.getElementById('people-table').dataset.mergeMode).toBeUndefined();
    expect(document.getElementById('people-merge-bar').style.display).toBe('none');

    // Entering merge mode on the fresh list shows the 0-selection prompt — the
    // previous view's selection did not leak across the navigation.
    document.getElementById('people-merge-btn').click();
    expect(document.querySelector('.merge-bar__label').textContent).toBe(
      'Select 2 people to merge:',
    );
  });
});
