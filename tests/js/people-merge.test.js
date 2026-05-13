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

  it('keep-a hx-target swaps the table body', () => {
    expect(document.querySelector('.merge-bar__keep-a').getAttribute('hx-target')).toBe(
      '#people-table-body',
    );
  });

  it('keep-b hx-target swaps the table body', () => {
    expect(document.querySelector('.merge-bar__keep-b').getAttribute('hx-target')).toBe(
      '#people-table-body',
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

  it('re-evaluates after htmx:afterSwap on the table', () => {
    setup({ numPeople: 2 });
    const btn = document.getElementById('people-merge-btn');
    const wrap = document.getElementById('people-merge-btn-wrap');
    expect(btn.disabled).toBe(false);

    const tbody = document.querySelector('#people-table tbody');
    tbody.innerHTML = `<tr data-title="P1" data-person-id="person-1">
      <td class="merge-col"><input type="checkbox" name="merge-select" value="person-1"></td>
      <td>P1</td>
    </tr>`;
    document.getElementById('people-table').dispatchEvent(new Event('htmx:afterSwap'));

    expect(btn.disabled).toBe(true);
    expect(wrap.style.cursor).toBe('not-allowed');
  });
});
