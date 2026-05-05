/**
 * Tests for src/static/admin/person-name-deadname-confirm.js
 *
 * Phase 2a Task 4: when an admin sets `name_type=deadname` on a person-name
 * form, the form gains hx-confirm + data-confirm-* attributes so the
 * existing admin-modal.js handler renders a styled confirmation dialog.
 * Selecting any other type clears those attributes.
 */
import { readFileSync } from 'node:fs';
import { resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';

const __dirname = dirname(fileURLToPath(import.meta.url));
const scriptCode = readFileSync(
  resolve(__dirname, '../../src/static/admin/person-name-deadname-confirm.js'),
  'utf-8',
);

let _addSpy;

beforeEach(() => {
  _addSpy = vi.spyOn(document, 'addEventListener');
});

afterEach(() => {
  _addSpy.mock.calls.forEach(([type, handler]) => document.removeEventListener(type, handler));
  vi.restoreAllMocks();
  document.body.innerHTML = '';
});

function buildForm({ initial = 'legal' } = {}) {
  document.body.innerHTML = `
    <form id="pn-form" hx-post="/admin/people/p1/names/">
      <select name="name_type">
        <option value="legal">legal</option>
        <option value="alias">alias</option>
        <option value="deadname">deadname</option>
      </select>
      <button type="submit">Save</button>
    </form>
  `;
  // happy-dom doesn't reflect <option selected> from innerHTML; set the
  // selection programmatically before the script's initial scan.
  document.querySelector('select[name="name_type"]').value = initial;
  eval(scriptCode);
  return {
    form: document.getElementById('pn-form'),
    select: document.querySelector('select[name="name_type"]'),
  };
}

function changeSelect(select, value) {
  select.value = value;
  select.dispatchEvent(new Event('change', { bubbles: true }));
}

describe('person-name-deadname-confirm', () => {
  it('does not set hx-confirm when name_type is non-deadname on load', () => {
    const { form } = buildForm({ initial: 'legal' });
    expect(form.hasAttribute('hx-confirm')).toBe(false);
  });

  it('sets hx-confirm on a form already showing deadname at load', () => {
    const { form } = buildForm({ initial: 'deadname' });
    expect(form.hasAttribute('hx-confirm')).toBe(true);
    expect(form.getAttribute('hx-confirm')).toMatch(/visibility/i);
  });

  it('adds hx-confirm when user selects deadname', () => {
    const { form, select } = buildForm({ initial: 'legal' });
    expect(form.hasAttribute('hx-confirm')).toBe(false);
    changeSelect(select, 'deadname');
    expect(form.hasAttribute('hx-confirm')).toBe(true);
  });

  it('removes hx-confirm when user changes away from deadname', () => {
    const { form, select } = buildForm({ initial: 'deadname' });
    expect(form.hasAttribute('hx-confirm')).toBe(true);
    changeSelect(select, 'alias');
    expect(form.hasAttribute('hx-confirm')).toBe(false);
  });

  it('sets data-confirm-title and data-confirm-variant for the modal', () => {
    const { form, select } = buildForm({ initial: 'legal' });
    changeSelect(select, 'deadname');
    expect(form.dataset.confirmTitle).toMatch(/deadname/i);
    expect(form.dataset.confirmVariant).toBeDefined();
  });

  it('confirm message mentions visibility coercion', () => {
    const { form, select } = buildForm({ initial: 'legal' });
    changeSelect(select, 'deadname');
    expect(form.getAttribute('hx-confirm')).toMatch(/legal_only|visibility/i);
  });

  it('ignores non-person-name forms (org-name path)', () => {
    document.body.innerHTML = `
      <form id="org-form" hx-post="/admin/orgs/o1/names/">
        <select name="name_type">
          <option value="legal">legal</option>
          <option value="deadname">deadname</option>
        </select>
      </form>
    `;
    document.querySelector('select[name="name_type"]').value = 'deadname';
    eval(scriptCode);
    const form = document.getElementById('org-form');
    expect(form.hasAttribute('hx-confirm')).toBe(false);
    // Even on subsequent change events, the org form must stay untouched.
    const select = form.querySelector('select[name="name_type"]');
    changeSelect(select, 'deadname');
    expect(form.hasAttribute('hx-confirm')).toBe(false);
  });

  it('handles forms inserted via HTMX afterSwap', () => {
    document.body.innerHTML = '<div id="container"></div>';
    eval(scriptCode);
    const container = document.getElementById('container');
    container.innerHTML = `
      <form id="late-form" hx-post="/admin/people/p2/names/">
        <select name="name_type">
          <option value="legal" selected>legal</option>
          <option value="deadname">deadname</option>
        </select>
      </form>
    `;
    // Simulate htmx:afterSwap so the script can re-scan / use delegated handler.
    document.dispatchEvent(new CustomEvent('htmx:afterSwap', { detail: { target: container } }));
    const form = document.getElementById('late-form');
    const select = form.querySelector('select[name="name_type"]');
    changeSelect(select, 'deadname');
    expect(form.hasAttribute('hx-confirm')).toBe(true);
  });
});
