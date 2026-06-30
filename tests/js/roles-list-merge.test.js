/**
 * Tests for src/static/admin/roles-merge.js (#251).
 *
 * roles-merge.js is a thin consumer of the shared `window.createMergeMode`
 * factory (merge-mode.js), like orgs-merge.js / people-merge.js — but it adds
 * the role-specific **same-org predicate**: role merge is org-scoped, so the
 * Keep A / Keep B buttons only enable when the two selected roles share an org.
 *
 * Namespaced element IDs (`roles-list-*`) deliberately differ from the
 * org-detail roles table (`roles-table` / `roles-merge-*`, driven by the older
 * role-merge.js) so the two never double-bind the same DOM.
 *
 * Listener cleanup pattern same as orgs-merge.test.js — see docs/STYLE.md §33.
 */
import { readFileSync } from 'node:fs';
import { resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';

const __dirname = dirname(fileURLToPath(import.meta.url));
const scriptCode = readFileSync(
  resolve(__dirname, '../../src/static/admin/roles-merge.js'),
  'utf-8',
);
// roles-merge.js calls window.createMergeMode; define it once. The factory
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

/**
 * Build the roles list markup.
 * `roles` is an array of {id, title, org}. Default: 3 roles, all on 'org-1'.
 */
function rolesListMarkup(roles) {
  const list =
    roles || [1, 2, 3].map((n) => ({ id: `role-${n}`, title: `Role ${n}`, org: 'org-1' }));
  const rows = list
    .map(
      (r) => `<tr data-title="${r.title}" data-role-id="${r.id}" data-org-id="${r.org}">
        <td class="merge-col"><input type="checkbox" name="merge-select" value="${r.id}"></td>
        <td>${r.title}</td>
      </tr>`,
    )
    .join('');
  return `
    <span id="roles-list-merge-btn-wrap" style="display:inline-block"><button id="roles-list-merge-btn" class="btn btn--secondary">Merge</button></span>
    <div id="roles-list-region">
      <table id="roles-list-table">
        <thead><tr><th class="merge-col">Sel</th><th>Title</th></tr></thead>
        <tbody id="roles-list-table-body">${rows}</tbody>
      </table>
      <div class="pagination--sticky">pagination here</div>
      <div id="roles-list-merge-bar" style="display:none">
        <span class="merge-bar__label">Merge roles:</span>
        <button class="merge-bar__keep-a" type="button"></button>
        <button class="merge-bar__keep-b" type="button"></button>
      </div>
    </div>
  `;
}

function setup(roles) {
  document.body.innerHTML = rolesListMarkup(roles);
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
    expect(document.getElementById('roles-list-table').dataset.mergeMode).toBeUndefined();
  });

  it('enters merge mode on button click', () => {
    const btn = document.getElementById('roles-list-merge-btn');
    btn.click();
    expect(document.getElementById('roles-list-table').dataset.mergeMode).toBe('true');
    expect(btn.textContent).toBe('Cancel merge');
    expect(btn.classList.contains('btn--ghost')).toBe(true);
  });

  it('shows .merge-col cells in merge mode', () => {
    document.getElementById('roles-list-merge-btn').click();
    document.querySelectorAll('.merge-col').forEach((col) => {
      expect(col.style.display).toBe('');
    });
  });
});

// ---------------------------------------------------------------------------
// Checkbox cap at 2
// ---------------------------------------------------------------------------

describe('checkbox cap at 2', () => {
  beforeEach(() => {
    setup();
    document.getElementById('roles-list-merge-btn').click();
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
});

// ---------------------------------------------------------------------------
// Bar labels at 0 / 1 selections
// ---------------------------------------------------------------------------

describe('bar labels', () => {
  beforeEach(() => {
    setup();
    document.getElementById('roles-list-merge-btn').click();
  });

  it('shows "Select 2 roles to merge:" at 0 selections', () => {
    expect(document.querySelector('.merge-bar__label').textContent).toBe(
      'Select 2 roles to merge:',
    );
  });

  it('shows "Select 1 more:" at 1 selection', () => {
    check(checkboxes()[0]);
    expect(document.querySelector('.merge-bar__label').textContent).toBe('Select 1 more:');
  });
});

// ---------------------------------------------------------------------------
// Same-org predicate — two roles on the SAME org => mergeable
// ---------------------------------------------------------------------------

describe('two roles, same org', () => {
  beforeEach(() => {
    setup([
      { id: 'role-1', title: 'Director', org: 'org-1' },
      { id: 'role-2', title: 'Exec Director', org: 'org-1' },
    ]);
    document.getElementById('roles-list-merge-btn').click();
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

  it('keep-a hx-post is org-scoped: keep role-1, discard role-2', () => {
    expect(document.querySelector('.merge-bar__keep-a').getAttribute('hx-post')).toBe(
      '/admin/orgs/org-1/roles/role-1/merge/role-2/',
    );
  });

  it('keep-b hx-post is org-scoped: keep role-2, discard role-1', () => {
    expect(document.querySelector('.merge-bar__keep-b').getAttribute('hx-post')).toBe(
      '/admin/orgs/org-1/roles/role-2/merge/role-1/',
    );
  });

  it('keep buttons target the list region', () => {
    expect(document.querySelector('.merge-bar__keep-a').getAttribute('hx-target')).toBe(
      '#roles-list-region',
    );
  });

  it('keep-a hx-confirm names both roles', () => {
    const msg = document.querySelector('.merge-bar__keep-a').getAttribute('hx-confirm');
    expect(msg).toContain('"Director"');
    expect(msg).toContain('"Exec Director"');
  });
});

// ---------------------------------------------------------------------------
// Same-org predicate — two roles on DIFFERENT orgs => blocked
// ---------------------------------------------------------------------------

describe('two roles, different orgs', () => {
  beforeEach(() => {
    setup([
      { id: 'role-1', title: 'Director', org: 'org-1' },
      { id: 'role-2', title: 'Treasurer', org: 'org-2' },
    ]);
    document.getElementById('roles-list-merge-btn').click();
    const [cb1, cb2] = checkboxes();
    check(cb1);
    check(cb2);
  });

  it('shows the same-org hint label', () => {
    expect(document.querySelector('.merge-bar__label').textContent).toBe(
      'Roles must be in the same organization to merge',
    );
  });

  it('both keep buttons are disabled', () => {
    expect(document.querySelector('.merge-bar__keep-a').disabled).toBe(true);
    expect(document.querySelector('.merge-bar__keep-b').disabled).toBe(true);
  });

  it('keep buttons carry no hx-post (merge cannot fire)', () => {
    expect(document.querySelector('.merge-bar__keep-a').getAttribute('hx-post')).toBeNull();
    expect(document.querySelector('.merge-bar__keep-b').getAttribute('hx-post')).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// Same-org predicate — cross-org block then same-org reselect re-enables
// ---------------------------------------------------------------------------

describe('cross-org block then same-org reselect', () => {
  beforeEach(() => {
    setup([
      { id: 'role-1', title: 'Director', org: 'org-1' },
      { id: 'role-2', title: 'Treasurer', org: 'org-2' },
      { id: 'role-3', title: 'Chair', org: 'org-1' },
    ]);
    document.getElementById('roles-list-merge-btn').click();
    const [cb1, cb2] = checkboxes();
    check(cb1); // role-1 (org-1)
    check(cb2); // role-2 (org-2) → cross-org, blocked
  });

  it('starts blocked on the cross-org pair', () => {
    expect(document.querySelector('.merge-bar__label').textContent).toBe(
      'Roles must be in the same organization to merge',
    );
    expect(document.querySelector('.merge-bar__keep-a').disabled).toBe(true);
  });

  it('re-enables with a correct hx-post after swapping in a same-org role', () => {
    const [, cb2, cb3] = checkboxes();
    uncheck(cb2); // drop the org-2 role
    check(cb3); // role-3 (org-1) → role-1 + role-3 now share org-1

    expect(document.querySelector('.merge-bar__label').textContent).toBe('Merge roles:');
    const btnA = document.querySelector('.merge-bar__keep-a');
    const btnB = document.querySelector('.merge-bar__keep-b');
    expect(btnA.disabled).toBe(false);
    expect(btnB.disabled).toBe(false);
    expect(btnA.getAttribute('hx-post')).toBe('/admin/orgs/org-1/roles/role-1/merge/role-3/');
    expect(btnB.getAttribute('hx-post')).toBe('/admin/orgs/org-1/roles/role-3/merge/role-1/');
  });
});

// ---------------------------------------------------------------------------
// Merge button disabled state (syncMergeBtn) — counts data-role-id rows
// ---------------------------------------------------------------------------

describe('merge button disabled state', () => {
  it('is disabled with not-allowed cursor when there are <2 roles', () => {
    setup([{ id: 'role-1', title: 'Solo', org: 'org-1' }]);
    const btn = document.getElementById('roles-list-merge-btn');
    const wrap = document.getElementById('roles-list-merge-btn-wrap');
    expect(btn.disabled).toBe(true);
    expect(wrap.style.cursor).toBe('not-allowed');
    expect(wrap.title).toBe('At least 2 roles required to merge');
  });

  it('is enabled when there are 2+ roles', () => {
    setup();
    expect(document.getElementById('roles-list-merge-btn').disabled).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// hx-boost survival (#249/#250/#251) — eval on a page with NO roles list
// ---------------------------------------------------------------------------

describe('hx-boost survival', () => {
  beforeEach(() => {
    document.body.innerHTML = '';
    eval(scriptCode);
  });

  function dispatchBoostArrival() {
    document.body.dispatchEvent(new CustomEvent('htmx:load', { bubbles: true }));
  }

  it('does not throw when evaluated on a page without the roles list', () => {
    expect(() => {
      document.dispatchEvent(new CustomEvent('showFlash'));
      document.body.dispatchEvent(new CustomEvent('htmx:afterSwap', { bubbles: true }));
      dispatchBoostArrival();
    }).not.toThrow();
  });

  it('drives the Merge button delivered by a boosted navigation', () => {
    document.body.innerHTML = rolesListMarkup();
    dispatchBoostArrival();
    document.getElementById('roles-list-merge-btn').click();
    expect(document.getElementById('roles-list-table').dataset.mergeMode).toBe('true');
  });

  it('starts a freshly-arrived roles list outside merge mode (no stale state)', () => {
    document.body.innerHTML = rolesListMarkup();
    dispatchBoostArrival();
    document.getElementById('roles-list-merge-btn').click();
    expect(document.getElementById('roles-list-table').dataset.mergeMode).toBe('true');

    document.body.innerHTML = rolesListMarkup();
    dispatchBoostArrival();
    expect(document.getElementById('roles-list-table').dataset.mergeMode).toBeUndefined();
  });
});
