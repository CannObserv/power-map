/**
 * Tests for src/static/admin/person-name-parts-reorder.js
 *
 * Issue #126: up/down arrow buttons on each cardstack card swap the input
 * value with the adjacent card's input value, so admins can reorder
 * given_names / family_names / additional_names without retyping.
 *
 * Follows STYLE.md §33 (vi.spyOn document listeners, cleanup in afterEach).
 */
import { readFileSync } from 'node:fs';
import { resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';

const __dirname = dirname(fileURLToPath(import.meta.url));
const REORDER_SRC = readFileSync(
  resolve(__dirname, '../../src/static/admin/person-name-parts-reorder.js'),
  'utf-8',
);
const CARDSTACK_SRC = readFileSync(
  resolve(__dirname, '../../src/static/admin/person-name-parts-cardstack.js'),
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

/** Build the HTML for `n` pre-populated cards for `field` with up/down arrows.
 *  Mirrors the server-rendered output from `_name_parts_editor.html`.
 *  First card's ↑ and last card's ↓ are disabled per the rendered initial state.
 */
function cardHTML(field, n) {
  return Array.from({ length: n }, (_, i) => {
    const upDisabled = i === 0 ? ' disabled' : '';
    const downDisabled = i === n - 1 ? ' disabled' : '';
    return `
      <div data-cardstack-card="${field}" style="display:flex;gap:var(--space-1);align-items:center">
        <div class="form-group" style="margin-bottom:0;flex:1">
          <input type="text" name="${field}" value="v${i}">
        </div>
        <button type="button" class="btn btn--sm btn--secondary"
                data-cardstack-reorder="up" data-cardstack-field="${field}"
                aria-label="Move this ${field.replace(/_names$/, '')} entry up"${upDisabled}>↑</button>
        <button type="button" class="btn btn--sm btn--secondary"
                data-cardstack-reorder="down" data-cardstack-field="${field}"
                aria-label="Move this ${field.replace(/_names$/, '')} entry down"${downDisabled}>↓</button>
        <button type="button" class="btn btn--sm btn--secondary"
                data-cardstack-remove="${field}"
                aria-label="Remove this ${field.replace(/_names$/, '')} entry">×</button>
      </div>`;
  }).join('');
}

function setupDOM(initialCards) {
  document.body.innerHTML = `
    <form>
      <fieldset>
        <div data-cardstack="given_names" data-cardstack-cap="5">${cardHTML('given_names', initialCards)}</div>
        <button type="button" data-cardstack-add="given_names">+ Add</button>
      </fieldset>
    </form>`;
  eval(REORDER_SRC);
}

function values(field) {
  return Array.from(document.querySelectorAll(`[data-cardstack-card="${field}"] input`)).map(
    (i) => i.value,
  );
}

function reorderButtons(field, direction) {
  return Array.from(
    document.querySelectorAll(
      `[data-cardstack-card="${field}"] [data-cardstack-reorder="${direction}"]`,
    ),
  );
}

describe('person-name-parts-reorder', () => {
  it('Click ↑ on second card swaps with first', () => {
    setupDOM(3);
    const ups = reorderButtons('given_names', 'up');
    ups[1].click();
    expect(values('given_names')).toEqual(['v1', 'v0', 'v2']);
  });

  it('Click ↓ on second card swaps with third', () => {
    setupDOM(3);
    const downs = reorderButtons('given_names', 'down');
    downs[1].click();
    expect(values('given_names')).toEqual(['v0', 'v2', 'v1']);
  });

  it('Click ↑ on first card is a no-op (disabled)', () => {
    setupDOM(3);
    const ups = reorderButtons('given_names', 'up');
    expect(ups[0].disabled).toBe(true);
    ups[0].click();
    expect(values('given_names')).toEqual(['v0', 'v1', 'v2']);
  });

  it('Click ↓ on last card is a no-op (disabled)', () => {
    setupDOM(3);
    const downs = reorderButtons('given_names', 'down');
    expect(downs[downs.length - 1].disabled).toBe(true);
    downs[downs.length - 1].click();
    expect(values('given_names')).toEqual(['v0', 'v1', 'v2']);
  });

  it('After swap, disabled state stays on the topmost ↑ and bottommost ↓', () => {
    setupDOM(3);
    const ups = reorderButtons('given_names', 'up');
    ups[1].click();
    const upsAfter = reorderButtons('given_names', 'up');
    const downsAfter = reorderButtons('given_names', 'down');
    expect(upsAfter[0].disabled).toBe(true);
    expect(upsAfter[1].disabled).toBe(false);
    expect(upsAfter[2].disabled).toBe(false);
    expect(downsAfter[0].disabled).toBe(false);
    expect(downsAfter[1].disabled).toBe(false);
    expect(downsAfter[2].disabled).toBe(true);
  });

  it('htmx:afterSwap re-runs init and re-syncs disabled state', () => {
    document.body.innerHTML = '<div></div>';
    eval(REORDER_SRC);
    const host = document.querySelector('div');
    host.innerHTML = `
      <form>
        <fieldset>
          <div data-cardstack="given_names" data-cardstack-cap="5">${cardHTML('given_names', 2)}</div>
        </fieldset>
      </form>`;
    for (const btn of host.querySelectorAll('[data-cardstack-reorder]')) {
      btn.removeAttribute('disabled');
    }
    document.dispatchEvent(new CustomEvent('htmx:afterSwap', { detail: { target: host } }));
    const ups = host.querySelectorAll('[data-cardstack-reorder="up"]');
    const downs = host.querySelectorAll('[data-cardstack-reorder="down"]');
    expect(ups[0].disabled).toBe(true);
    expect(ups[1].disabled).toBe(false);
    expect(downs[0].disabled).toBe(false);
    expect(downs[1].disabled).toBe(true);
  });

  describe('multi-stack isolation (two rows open simultaneously)', () => {
    beforeEach(() => {
      document.body.innerHTML = `
        <form id="form1">
          <fieldset>
            <div data-cardstack="given_names" data-cardstack-cap="5">${cardHTML('given_names', 3)}</div>
          </fieldset>
        </form>
        <form id="form2">
          <fieldset>
            <div data-cardstack="given_names" data-cardstack-cap="5">${cardHTML('given_names', 3)}</div>
          </fieldset>
        </form>`;
      eval(REORDER_SRC);
    });

    it('Click ↑ in form A leaves form B values untouched', () => {
      const form1 = document.getElementById('form1');
      const form2 = document.getElementById('form2');
      const ups = form1.querySelectorAll('[data-cardstack-reorder="up"]');
      ups[1].click();
      const form1Vals = Array.from(form1.querySelectorAll('input')).map((i) => i.value);
      const form2Vals = Array.from(form2.querySelectorAll('input')).map((i) => i.value);
      expect(form1Vals).toEqual(['v1', 'v0', 'v2']);
      expect(form2Vals).toEqual(['v0', 'v1', 'v2']);
    });
  });

  describe('cardstack Add wires arrows on newly-built cards', () => {
    it('After Add, new card has arrows and disabled-state is correct', () => {
      document.body.innerHTML = `
        <form>
          <fieldset>
            <div data-cardstack="given_names" data-cardstack-cap="5">${cardHTML('given_names', 1)}</div>
            <button type="button" data-cardstack-add="given_names">+ Add</button>
          </fieldset>
        </form>`;
      eval(REORDER_SRC);
      eval(CARDSTACK_SRC);
      const addBtn = document.querySelector('[data-cardstack-add="given_names"]');
      addBtn.click();
      const cards = document.querySelectorAll('[data-cardstack-card="given_names"]');
      expect(cards.length).toBe(2);
      const newUp = cards[1].querySelector('[data-cardstack-reorder="up"]');
      const newDown = cards[1].querySelector('[data-cardstack-reorder="down"]');
      expect(newUp).not.toBeNull();
      expect(newDown).not.toBeNull();
      const ups = document.querySelectorAll('[data-cardstack-reorder="up"]');
      const downs = document.querySelectorAll('[data-cardstack-reorder="down"]');
      expect(ups[0].disabled).toBe(true);
      expect(ups[1].disabled).toBe(false);
      expect(downs[0].disabled).toBe(false);
      expect(downs[1].disabled).toBe(true);
    });

    it('After Remove, disabled state on remaining arrows is re-synced', () => {
      document.body.innerHTML = `
        <form>
          <fieldset>
            <div data-cardstack="given_names" data-cardstack-cap="5">${cardHTML('given_names', 3)}</div>
            <button type="button" data-cardstack-add="given_names">+ Add</button>
          </fieldset>
        </form>`;
      eval(REORDER_SRC);
      eval(CARDSTACK_SRC);
      const middleRemove = document.querySelectorAll('[data-cardstack-remove]')[1];
      middleRemove.click();
      const ups = document.querySelectorAll('[data-cardstack-reorder="up"]');
      const downs = document.querySelectorAll('[data-cardstack-reorder="down"]');
      expect(ups.length).toBe(2);
      expect(ups[0].disabled).toBe(true);
      expect(ups[1].disabled).toBe(false);
      expect(downs[0].disabled).toBe(false);
      expect(downs[1].disabled).toBe(true);
    });
  });
});
