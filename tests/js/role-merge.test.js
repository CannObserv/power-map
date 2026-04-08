/**
 * Tests for src/static/admin/role-merge.js
 *
 * The script is an IIFE that attaches listeners to DOM elements immediately on
 * execution. Pattern: build DOM fixture → eval() the IIFE → simulate events →
 * assert state.
 *
 * Listener cleanup: each eval() adds a document-level 'showFlash' listener.
 * A global beforeEach/afterEach pair spies on document.addEventListener,
 * captures every handler registered during the test, and removes them all in
 * afterEach — preventing cross-test listener accumulation.
 */
import { readFileSync } from 'node:fs';
import { resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';

const __dirname = dirname(fileURLToPath(import.meta.url));
const scriptCode = readFileSync(
  resolve(__dirname, '../../src/static/admin/role-merge.js'),
  'utf-8',
);

// ---------------------------------------------------------------------------
// Global listener cleanup
// Spy on document.addEventListener before every test so we can remove all
// handlers the IIFE registers. The spy calls through; vi.restoreAllMocks()
// restores the original after each test.
// ---------------------------------------------------------------------------

let _addSpy;

beforeEach(() => {
  _addSpy = vi.spyOn(document, 'addEventListener');
});

afterEach(() => {
  _addSpy.mock.calls.forEach(([type, handler]) => document.removeEventListener(type, handler));
  vi.restoreAllMocks();
  document.body.innerHTML = '';
});

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

// Build the required DOM fixture and eval the script.
function setup({ orgId = 'org-1', numRoles = 3 } = {}) {
  const rows = Array.from({ length: numRoles }, (_, i) => {
    const n = i + 1;
    return `<tr data-title="Role ${n}">
      <td class="merge-col"><input type="checkbox" name="merge-select" value="role-${n}"></td>
      <td>Role ${n}</td>
    </tr>`;
  }).join('');

  document.body.innerHTML = `
    <input id="roles-filter" type="search">
    <table id="roles-table" data-org-id="${orgId}">
      <thead><tr><th class="merge-col">Sel</th><th>Title</th></tr></thead>
      <tbody>${rows}</tbody>
    </table>
    <button id="roles-merge-btn" class="btn btn--secondary">Merge</button>
    <div id="roles-merge-bar" style="display:none">
      <span class="merge-bar__label">Merge roles:</span>
      <button class="merge-bar__keep-a" type="button"></button>
      <button class="merge-bar__keep-b" type="button"></button>
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
    expect(document.getElementById('roles-table').dataset.mergeMode).toBeUndefined();
  });

  it('enters merge mode on button click', () => {
    const btn = document.getElementById('roles-merge-btn');
    btn.click();
    expect(document.getElementById('roles-table').dataset.mergeMode).toBe('true');
    expect(btn.textContent).toBe('Cancel merge');
    expect(btn.classList.contains('btn--ghost')).toBe(true);
    expect(btn.classList.contains('btn--secondary')).toBe(false);
  });

  it('exits merge mode on second click', () => {
    const btn = document.getElementById('roles-merge-btn');
    btn.click();
    btn.click();
    expect(document.getElementById('roles-table').dataset.mergeMode).toBeUndefined();
    expect(btn.textContent).toBe('Merge');
    expect(btn.classList.contains('btn--secondary')).toBe(true);
    expect(btn.classList.contains('btn--ghost')).toBe(false);
  });

  it('shows .merge-col cells in merge mode', () => {
    document.getElementById('roles-merge-btn').click();
    document.querySelectorAll('.merge-col').forEach((col) => {
      expect(col.style.display).toBe('');
    });
  });

  it('hides .merge-col cells after exiting merge mode', () => {
    const btn = document.getElementById('roles-merge-btn');
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
    document.getElementById('roles-merge-btn').click(); // enter merge mode
  });

  it('bar is visible in merge mode', () => {
    expect(document.getElementById('roles-merge-bar').style.display).toBe('flex');
  });

  it('bar is hidden after exiting merge mode', () => {
    document.getElementById('roles-merge-btn').click(); // exit
    expect(document.getElementById('roles-merge-bar').style.display).toBe('none');
  });
});

// ---------------------------------------------------------------------------
// Checkbox cap at 2
// ---------------------------------------------------------------------------

describe('checkbox cap at 2', () => {
  beforeEach(() => {
    setup({ numRoles: 3 });
    document.getElementById('roles-merge-btn').click();
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
    document.getElementById('roles-merge-btn').click(); // exit
    checkboxes().forEach((cb) => expect(cb.checked).toBe(false));
  });
});

// ---------------------------------------------------------------------------
// Bar label + button state at 0 selections
// ---------------------------------------------------------------------------

describe('bar at 0 selections', () => {
  beforeEach(() => {
    setup();
    document.getElementById('roles-merge-btn').click();
  });

  it('shows "Select 2 roles to merge:" label', () => {
    expect(document.querySelector('.merge-bar__label').textContent).toBe(
      'Select 2 roles to merge:',
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
    document.getElementById('roles-merge-btn').click();
    check(checkboxes()[0]);
  });

  it('shows "Select 1 more:" label', () => {
    expect(document.querySelector('.merge-bar__label').textContent).toBe('Select 1 more:');
  });

  it('keep-a shows selected role title and is disabled', () => {
    const btnA = document.querySelector('.merge-bar__keep-a');
    expect(btnA.textContent).toBe('Selected: "Role 1"');
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
  const orgId = 'org-abc';

  beforeEach(() => {
    setup({ orgId, numRoles: 2 });
    document.getElementById('roles-merge-btn').click();
    const [cb1, cb2] = checkboxes();
    check(cb1);
    check(cb2);
  });

  it('shows "Merge roles:" label', () => {
    expect(document.querySelector('.merge-bar__label').textContent).toBe('Merge roles:');
  });

  it('both keep buttons are enabled', () => {
    expect(document.querySelector('.merge-bar__keep-a').disabled).toBe(false);
    expect(document.querySelector('.merge-bar__keep-b').disabled).toBe(false);
  });

  it('keep-a shows title of first selected role', () => {
    expect(document.querySelector('.merge-bar__keep-a').textContent).toBe('Keep "Role 1"');
  });

  it('keep-b shows title of second selected role', () => {
    expect(document.querySelector('.merge-bar__keep-b').textContent).toBe('Keep "Role 2"');
  });

  it('keep-a hx-post: keep role-1, discard role-2', () => {
    expect(document.querySelector('.merge-bar__keep-a').getAttribute('hx-post')).toBe(
      `/admin/orgs/${orgId}/roles/role-1/merge/role-2/`,
    );
  });

  it('keep-b hx-post: keep role-2, discard role-1', () => {
    expect(document.querySelector('.merge-bar__keep-b').getAttribute('hx-post')).toBe(
      `/admin/orgs/${orgId}/roles/role-2/merge/role-1/`,
    );
  });

  it('keep-a hx-confirm names both roles', () => {
    const msg = document.querySelector('.merge-bar__keep-a').getAttribute('hx-confirm');
    expect(msg).toContain('"Role 2"'); // role being discarded
    expect(msg).toContain('"Role 1"'); // role being kept
  });

  it('keep-b hx-confirm names both roles', () => {
    const msg = document.querySelector('.merge-bar__keep-b').getAttribute('hx-confirm');
    expect(msg).toContain('"Role 1"'); // role being discarded
    expect(msg).toContain('"Role 2"'); // role being kept
  });
});

// ---------------------------------------------------------------------------
// showFlash event exits merge mode
// ---------------------------------------------------------------------------

describe('showFlash exits merge mode', () => {
  beforeEach(() => {
    setup();
    document.getElementById('roles-merge-btn').click(); // enter merge mode
  });

  it('exits merge mode when showFlash fires while in merge mode', () => {
    expect(document.getElementById('roles-table').dataset.mergeMode).toBe('true');
    document.dispatchEvent(new CustomEvent('showFlash'));
    expect(document.getElementById('roles-table').dataset.mergeMode).toBeUndefined();
  });

  it('resets button text and classes on showFlash exit', () => {
    const btn = document.getElementById('roles-merge-btn');
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
    document.getElementById('roles-merge-btn').click(); // exit first
    document.dispatchEvent(new CustomEvent('showFlash'));
    // still outside merge mode, no error
    expect(document.getElementById('roles-table').dataset.mergeMode).toBeUndefined();
  });
});

// ---------------------------------------------------------------------------
// Roles filter
// ---------------------------------------------------------------------------

describe('roles filter', () => {
  function tableRows() {
    return Array.from(document.querySelectorAll('tbody tr[data-title]'));
  }

  function fireInput(value) {
    const input = document.getElementById('roles-filter');
    input.value = value;
    input.dispatchEvent(new Event('input'));
  }

  beforeEach(() => setup({ numRoles: 3 }));

  it('hides rows whose title does not match', () => {
    fireInput('Role 1');
    const hidden = tableRows().filter((r) => r.style.display === 'none');
    expect(hidden).toHaveLength(2);
  });

  it('shows rows whose title matches', () => {
    fireInput('Role 1');
    const visible = tableRows().filter((r) => r.style.display !== 'none');
    expect(visible).toHaveLength(1);
    expect(visible[0].dataset.title).toBe('Role 1');
  });

  it('is case-insensitive', () => {
    fireInput('role 1');
    const visible = tableRows().filter((r) => r.style.display !== 'none');
    expect(visible).toHaveLength(1);
  });

  it('shows all rows when query is cleared', () => {
    fireInput('Role 1');
    fireInput('');
    tableRows().forEach((r) => expect(r.style.display).not.toBe('none'));
  });
});
