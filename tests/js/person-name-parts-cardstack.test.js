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

// Global listener cleanup — see docs/STYLE.md §33.
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

/** Build the HTML for `n` pre-populated cards for `field`. */
function cardHTML(field, n) {
  return Array.from(
    { length: n },
    (_, i) => `
    <div data-cardstack-card="${field}" style="display:flex;gap:var(--space-1);align-items:center">
      <input type="text" name="${field}" value="v${i}" style="flex:1">
      <button type="button" data-cardstack-remove="${field}">×</button>
    </div>`,
  ).join('');
}

/**
 * Set up a single-stack DOM for given_names, wrapped in a <form> so that
 * `closest('form')` works for scope resolution.
 */
function setupDOM(initialCards = 0) {
  document.body.innerHTML = `
    <form>
      <fieldset>
        <div data-cardstack="given_names" data-cardstack-cap="5">${cardHTML('given_names', initialCards)}</div>
        <button type="button" data-cardstack-add="given_names">+ Add</button>
      </fieldset>
    </form>`;
  eval(SRC);
}

describe('person-name-parts-cardstack', () => {
  it('wires a stack delivered by a boosted navigation (#237)', () => {
    // Loaded site-wide from base.html, the script evals on a page with no
    // parts editor. hx-boost then swaps in the person detail page, firing
    // htmx:afterSwap — the persistent document rescan must run initAll on the
    // freshly-swapped stack and disable Add at the cap.
    document.body.innerHTML = '';
    eval(SRC);
    document.body.innerHTML = `
      <form><fieldset>
        <div data-cardstack="given_names" data-cardstack-cap="2">${cardHTML('given_names', 2)}</div>
        <button type="button" data-cardstack-add="given_names">+ Add</button>
      </fieldset></form>`;
    document.dispatchEvent(new Event('htmx:afterSwap'));
    // 2 cards at cap 2 → Add disabled by initAll's syncAddBtn.
    expect(document.querySelector('[data-cardstack-add="given_names"]').disabled).toBe(true);
  });

  it('Add appends a new empty card', () => {
    setupDOM(1);
    const addBtn = document.querySelector('[data-cardstack-add="given_names"]');
    addBtn.click();
    const cards = document.querySelectorAll('[data-cardstack-card="given_names"]');
    expect(cards.length).toBe(2);
    expect(cards[1].querySelector('input').value).toBe('');
  });

  it('Added card wraps the <input> in a .form-group so it inherits site styling', () => {
    // Round-2 of #131 (4ad31b2) wrapped server-rendered cardstack inputs
    // in `<div class="form-group">` so they pick up the baseline
    // `.form-group input` rule (font-size, padding, min-height: 44px).
    // `buildCard()` in the JS was missed: bare <input> appended directly
    // to the card div fell back to browser-default styling. Pin the
    // contract: every card — server-rendered OR JS-built — must have a
    // .form-group wrapper around its <input>.
    setupDOM(0);
    const addBtn = document.querySelector('[data-cardstack-add="given_names"]');
    addBtn.click();
    const card = document.querySelector('[data-cardstack-card="given_names"]');
    expect(card).not.toBeNull();
    const wrapper = card.querySelector('.form-group');
    expect(wrapper).not.toBeNull();
    const input = wrapper.querySelector('input[type="text"]');
    expect(input).not.toBeNull();
    expect(input.getAttribute('name')).toBe('given_names');
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

  describe('multi-stack isolation (two rows open simultaneously)', () => {
    beforeEach(() => {
      // Two independent name-row editors, each in its own <form>.
      // Row 1: 1 given_names card. Row 2: 2 given_names cards.
      document.body.innerHTML = `
        <form id="form1">
          <fieldset>
            <div data-cardstack="given_names" data-cardstack-cap="5">${cardHTML('given_names', 1)}</div>
            <button type="button" data-cardstack-add="given_names">+ Add</button>
          </fieldset>
        </form>
        <form id="form2">
          <fieldset>
            <div data-cardstack="given_names" data-cardstack-cap="5">${cardHTML('given_names', 2)}</div>
            <button type="button" data-cardstack-add="given_names">+ Add</button>
          </fieldset>
        </form>`;
      eval(SRC);
    });

    it('Add on second stack appends only to second stack', () => {
      const form2 = document.getElementById('form2');
      const addBtn2 = form2.querySelector('[data-cardstack-add="given_names"]');
      addBtn2.click();

      const form1 = document.getElementById('form1');
      expect(form1.querySelectorAll('[data-cardstack-card="given_names"]').length).toBe(1);
      expect(form2.querySelectorAll('[data-cardstack-card="given_names"]').length).toBe(3);
    });

    it('Remove on second stack removes only from second stack', () => {
      const form2 = document.getElementById('form2');
      const firstRemove2 = form2.querySelector('[data-cardstack-remove="given_names"]');
      firstRemove2.click();

      const form1 = document.getElementById('form1');
      expect(form1.querySelectorAll('[data-cardstack-card="given_names"]').length).toBe(1);
      expect(form2.querySelectorAll('[data-cardstack-card="given_names"]').length).toBe(1);
    });

    it('Add button on stack 1 is unaffected by stack 2 cap state', () => {
      // Fill stack 2 to cap so its Add button is disabled.
      const form2 = document.getElementById('form2');
      const addBtn2 = form2.querySelector('[data-cardstack-add="given_names"]');
      for (let i = 0; i < 3; i++) addBtn2.click(); // 2 + 3 = 5 → disabled

      expect(addBtn2.disabled).toBe(true);

      // Stack 1 has 1 card — its Add button must still be enabled.
      const form1 = document.getElementById('form1');
      const addBtn1 = form1.querySelector('[data-cardstack-add="given_names"]');
      expect(addBtn1.disabled).toBe(false);
    });
  });

  // Issue #146 — per-card buttons (and the input) embed a 1-based position
  // in their aria-label so screen-reader users can disambiguate siblings.
  // The server pre-renders these via Jinja's `loop.index`; the JS must keep
  // them in sync after Add (new card) and Remove (surviving cards shift).
  describe('per-card aria-label disambiguation (#146)', () => {
    /** Derive the same human-readable labels the JS produces, so test fixtures
     *  can mirror server-rendered aria-labels for any field name. */
    function labelsFor(field) {
      const lower = field.replace(/_/g, ' ');
      const cap = lower.charAt(0).toUpperCase() + lower.slice(1);
      return { lower, cap };
    }

    /** Server-shape card: input + up/down/remove, all with indexed aria-labels. */
    function indexedCardHTML(field, n) {
      const { lower, cap } = labelsFor(field);
      return Array.from({ length: n }, (_, i) => {
        const idx = i + 1;
        return `
        <div data-cardstack-card="${field}" style="display:flex;gap:var(--space-1);align-items:center">
          <div class="form-group" style="margin-bottom:0;flex:1">
            <input type="text" name="${field}" value="v${i}" aria-label="${cap} ${idx}">
          </div>
          <button type="button" data-cardstack-reorder="up" data-cardstack-field="${field}"
                  aria-label="Move ${lower} entry ${idx} up">↑</button>
          <button type="button" data-cardstack-reorder="down" data-cardstack-field="${field}"
                  aria-label="Move ${lower} entry ${idx} down">↓</button>
          <button type="button" data-cardstack-remove="${field}"
                  aria-label="Remove ${lower} entry ${idx}">×</button>
        </div>`;
      }).join('');
    }

    function setupIndexedDOM(initialCards, field = 'given_names') {
      document.body.innerHTML = `
        <form>
          <fieldset>
            <div data-cardstack="${field}" data-cardstack-cap="5">${indexedCardHTML(field, initialCards)}</div>
            <button type="button" data-cardstack-add="${field}">+ Add</button>
          </fieldset>
        </form>`;
      eval(SRC);
    }

    function ariaLabelsByCard(field) {
      const cards = Array.from(document.querySelectorAll(`[data-cardstack-card="${field}"]`));
      return cards.map((card) => ({
        input: card.querySelector(`input[name="${field}"]`).getAttribute('aria-label'),
        up: card.querySelector('[data-cardstack-reorder="up"]').getAttribute('aria-label'),
        down: card.querySelector('[data-cardstack-reorder="down"]').getAttribute('aria-label'),
        remove: card.querySelector(`[data-cardstack-remove="${field}"]`).getAttribute('aria-label'),
      }));
    }

    it('Added card embeds its 1-based position in every aria-label', () => {
      setupIndexedDOM(2);
      const addBtn = document.querySelector('[data-cardstack-add="given_names"]');
      addBtn.click();
      const labels = ariaLabelsByCard('given_names');
      expect(labels).toHaveLength(3);
      expect(labels[2].input).toBe('Given names 3');
      expect(labels[2].up).toBe('Move given names entry 3 up');
      expect(labels[2].down).toBe('Move given names entry 3 down');
      expect(labels[2].remove).toBe('Remove given names entry 3');
    });

    it('Remove shifts surviving cards’ aria-labels down by one', () => {
      // Start with 3 cards (entries 1, 2, 3); remove the middle one.
      // Survivors used to be entries 1 and 3 — after refresh they must
      // read as entries 1 and 2 so the labels track DOM position, not
      // original render order.
      setupIndexedDOM(3);
      const middleRemove = document.querySelectorAll('[data-cardstack-remove="given_names"]')[1];
      middleRemove.click();
      const labels = ariaLabelsByCard('given_names');
      expect(labels).toHaveLength(2);
      expect(labels[0].input).toBe('Given names 1');
      expect(labels[0].up).toBe('Move given names entry 1 up');
      expect(labels[0].down).toBe('Move given names entry 1 down');
      expect(labels[0].remove).toBe('Remove given names entry 1');
      expect(labels[1].input).toBe('Given names 2');
      expect(labels[1].up).toBe('Move given names entry 2 up');
      expect(labels[1].down).toBe('Move given names entry 2 down');
      expect(labels[1].remove).toBe('Remove given names entry 2');
    });

    it('Add then Remove leaves contiguous indices on survivors', () => {
      setupIndexedDOM(1);
      const addBtn = document.querySelector('[data-cardstack-add="given_names"]');
      addBtn.click(); // now entries 1, 2
      addBtn.click(); // now entries 1, 2, 3
      const firstRemove = document.querySelector('[data-cardstack-remove="given_names"]');
      firstRemove.click();
      const labels = ariaLabelsByCard('given_names');
      expect(labels.map((l) => l.input)).toEqual(['Given names 1', 'Given names 2']);
      expect(labels.map((l) => l.remove)).toEqual([
        'Remove given names entry 1',
        'Remove given names entry 2',
      ]);
    });

    // Cross-field coverage: `fieldLabel` is field-agnostic, but a future
    // special-case (e.g. stripping `_names` again) would only break the
    // non-`given_names` paths. Pin Add + Remove for both other fields.
    it.each([
      { field: 'family_names', cap: 'Family names', lower: 'family names' },
      { field: 'additional_names', cap: 'Additional names', lower: 'additional names' },
    ])('Add + Remove track DOM position for $field', ({ field, cap, lower }) => {
      setupIndexedDOM(2, field);
      // Add → expect entry 3 with the field-derived label.
      const addBtn = document.querySelector(`[data-cardstack-add="${field}"]`);
      addBtn.click();
      let labels = ariaLabelsByCard(field);
      expect(labels).toHaveLength(3);
      expect(labels[2].input).toBe(`${cap} 3`);
      expect(labels[2].up).toBe(`Move ${lower} entry 3 up`);
      expect(labels[2].down).toBe(`Move ${lower} entry 3 down`);
      expect(labels[2].remove).toBe(`Remove ${lower} entry 3`);

      // Remove the first card → survivors must re-index from 1, not stay
      // at their original 2/3.
      const firstRemove = document.querySelector(`[data-cardstack-remove="${field}"]`);
      firstRemove.click();
      labels = ariaLabelsByCard(field);
      expect(labels).toHaveLength(2);
      expect(labels.map((l) => l.input)).toEqual([`${cap} 1`, `${cap} 2`]);
      expect(labels.map((l) => l.remove)).toEqual([
        `Remove ${lower} entry 1`,
        `Remove ${lower} entry 2`,
      ]);
    });
  });
});
