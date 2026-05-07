/**
 * Tests for src/static/admin/person-name-parts-cardstack.js
 *
 * Issue #127: vertical card stack for given_names / family_names /
 * additional_names in the parts editor. Add appends empty cards, Remove
 * drops them, and the Add button disables when the cap is reached.
 */
import { readFileSync } from 'node:fs';
import { resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';

const __dirname = dirname(fileURLToPath(import.meta.url));
const SRC = readFileSync(
  resolve(__dirname, '../../src/static/admin/person-name-parts-cardstack.js'),
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

function setupDOM(initialCards = 0) {
  const cards = Array.from(
    { length: initialCards },
    (_, i) => `
    <div data-cardstack-card="given_names" style="display:flex;gap:var(--space-1);align-items:center">
      <input type="text" name="given_names" value="v${i}" style="flex:1">
      <button type="button" data-cardstack-remove="given_names">×</button>
    </div>`,
  ).join('');
  document.body.innerHTML = `
    <fieldset>
      <div data-cardstack="given_names" data-cardstack-cap="5">${cards}</div>
      <button type="button" data-cardstack-add="given_names">+ Add</button>
    </fieldset>`;
  eval(SRC);
}

describe('person-name-parts-cardstack', () => {
  it('Add appends a new empty card', () => {
    setupDOM(1);
    const addBtn = document.querySelector('[data-cardstack-add="given_names"]');
    addBtn.click();
    const cards = document.querySelectorAll('[data-cardstack-card="given_names"]');
    expect(cards.length).toBe(2);
    expect(cards[1].querySelector('input').value).toBe('');
  });

  it('Remove drops the clicked card', () => {
    setupDOM(2);
    const firstRemove = document.querySelector('[data-cardstack-remove="given_names"]');
    firstRemove.click();
    const cards = document.querySelectorAll('[data-cardstack-card="given_names"]');
    expect(cards.length).toBe(1);
    expect(cards[0].querySelector('input').value).toBe('v1');
  });

  it('Add button disables when cap reached', () => {
    setupDOM(4);
    const addBtn = document.querySelector('[data-cardstack-add="given_names"]');
    addBtn.click();
    expect(addBtn.disabled).toBe(true);
    const cards = document.querySelectorAll('[data-cardstack-card="given_names"]');
    expect(cards.length).toBe(5);
  });

  it('Remove re-enables a disabled Add button', () => {
    setupDOM(5);
    const addBtn = document.querySelector('[data-cardstack-add="given_names"]');
    expect(addBtn.disabled).toBe(true);
    const firstRemove = document.querySelector('[data-cardstack-remove="given_names"]');
    firstRemove.click();
    expect(addBtn.disabled).toBe(false);
  });
});
