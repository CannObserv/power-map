/**
 * Tests for src/static/admin/person-name-parts-reorder.js
 *
 * Issue #126: up/down arrow buttons on each cardstack card swap the input
 * value with the adjacent card's input value, so admins can reorder
 * given_names / family_names / additional_names without retyping.
 *
 * Follows docs/TESTING.md § Vitest test conventions (vi.spyOn document listeners, cleanup in afterEach).
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
  it('syncs a stack delivered by a boosted navigation (#237)', () => {
    // Loaded site-wide from base.html, the script evals on a page with no
    // parts editor. hx-boost then swaps in the person detail page, firing
    // htmx:afterSwap — the persistent document rescan must run syncAll and fix
    // the boundary arrows (top ↑ / bottom ↓ disabled).
    document.body.innerHTML = '';
    eval(REORDER_SRC);
    document.body.innerHTML = `
      <form><fieldset>
        <div data-cardstack="given_names" data-cardstack-cap="5">${cardHTML('given_names', 3)}</div>
      </fieldset></form>`;
    // Force every arrow enabled so the assertion proves syncAll ran, not the
    // static server markup.
    document.querySelectorAll('[data-cardstack-reorder]').forEach((b) => (b.disabled = false));
    document.dispatchEvent(new Event('htmx:afterSwap'));
    const cards = document.querySelectorAll('[data-cardstack-card="given_names"]');
    expect(cards[0].querySelector('[data-cardstack-reorder="up"]').disabled).toBe(true);
    expect(cards[2].querySelector('[data-cardstack-reorder="down"]').disabled).toBe(true);
    expect(cards[1].querySelector('[data-cardstack-reorder="up"]').disabled).toBe(false);
  });

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
    function setupTwoForms(cardsPerForm) {
      document.body.innerHTML = `
        <form id="form1">
          <fieldset>
            <div data-cardstack="given_names" data-cardstack-cap="5">${cardHTML('given_names', cardsPerForm)}</div>
          </fieldset>
        </form>
        <form id="form2">
          <fieldset>
            <div data-cardstack="given_names" data-cardstack-cap="5">${cardHTML('given_names', cardsPerForm)}</div>
          </fieldset>
        </form>`;
      eval(REORDER_SRC);
    }

    it('Click ↑ in form A leaves form B values untouched', () => {
      setupTwoForms(3);
      const form1 = document.getElementById('form1');
      const form2 = document.getElementById('form2');
      const ups = form1.querySelectorAll('[data-cardstack-reorder="up"]');
      ups[1].click();
      const form1Vals = Array.from(form1.querySelectorAll('input')).map((i) => i.value);
      const form2Vals = Array.from(form2.querySelectorAll('input')).map((i) => i.value);
      expect(form1Vals).toEqual(['v1', 'v0', 'v2']);
      expect(form2Vals).toEqual(['v0', 'v1', 'v2']);
    });

    // #145 — focus-follows-value must not leak across forms. 4 cards exercises
    // the interior button-focus path (form-scoped selector); 2 cards exercises
    // the boundary input-fallback path.
    it('Interior ↑ in form A keeps focus inside form A', () => {
      setupTwoForms(4);
      const form1 = document.getElementById('form1');
      const ups = form1.querySelectorAll('[data-cardstack-reorder="up"]');
      ups[2].click();
      expect(form1.contains(document.activeElement)).toBe(true);
    });

    it('Boundary ↑ in form A keeps focus inside form A', () => {
      setupTwoForms(2);
      const form1 = document.getElementById('form1');
      const ups = form1.querySelectorAll('[data-cardstack-reorder="up"]');
      ups[1].click();
      expect(form1.contains(document.activeElement)).toBe(true);
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

  // Issue #146 — reorder is a value-swap, not a DOM reorder. Aria-labels
  // encode position, not value, so ↑↓ must leave them byte-identical.
  // Locks in the design intent: if a future maintainer "helpfully" calls
  // refreshIndices() after a swap, this test catches the regression and
  // forces a deliberate decision.
  describe('aria-label stability under ↑↓ swap (#146)', () => {
    /** Render N cards with server-shape, #146-style indexed aria-labels. */
    function indexedCardHTML(field, n) {
      const lower = field.replace(/_/g, ' ');
      const cap = lower.charAt(0).toUpperCase() + lower.slice(1);
      return Array.from({ length: n }, (_, i) => {
        const idx = i + 1;
        const upDisabled = i === 0 ? ' disabled' : '';
        const downDisabled = i === n - 1 ? ' disabled' : '';
        return `
          <div data-cardstack-card="${field}" style="display:flex;gap:var(--space-1);align-items:center">
            <div class="form-group" style="margin-bottom:0;flex:1">
              <input type="text" name="${field}" value="v${i}" aria-label="${cap} ${idx}">
            </div>
            <button type="button" class="btn btn--sm btn--secondary"
                    data-cardstack-reorder="up" data-cardstack-field="${field}"
                    aria-label="Move ${lower} entry ${idx} up"${upDisabled}>↑</button>
            <button type="button" class="btn btn--sm btn--secondary"
                    data-cardstack-reorder="down" data-cardstack-field="${field}"
                    aria-label="Move ${lower} entry ${idx} down"${downDisabled}>↓</button>
            <button type="button" class="btn btn--sm btn--secondary"
                    data-cardstack-remove="${field}"
                    aria-label="Remove ${lower} entry ${idx}">×</button>
          </div>`;
      }).join('');
    }

    function snapshotAriaLabels(field) {
      const cards = Array.from(document.querySelectorAll(`[data-cardstack-card="${field}"]`));
      return cards.map((card) => ({
        input: card.querySelector(`input[name="${field}"]`).getAttribute('aria-label'),
        up: card.querySelector('[data-cardstack-reorder="up"]').getAttribute('aria-label'),
        down: card.querySelector('[data-cardstack-reorder="down"]').getAttribute('aria-label'),
        remove: card.querySelector(`[data-cardstack-remove="${field}"]`).getAttribute('aria-label'),
      }));
    }

    it('↑ on the middle card leaves every aria-label untouched (only values swap)', () => {
      document.body.innerHTML = `
        <form>
          <fieldset>
            <div data-cardstack="given_names" data-cardstack-cap="5">${indexedCardHTML('given_names', 3)}</div>
          </fieldset>
        </form>`;
      eval(REORDER_SRC);
      const before = snapshotAriaLabels('given_names');
      const ups = document.querySelectorAll('[data-cardstack-reorder="up"]');
      ups[1].click();
      // Values swapped — card-1 now holds 'v1', card-2 holds 'v0'.
      expect(values('given_names')).toEqual(['v1', 'v0', 'v2']);
      // …but every aria-label is byte-identical to the pre-click snapshot.
      const after = snapshotAriaLabels('given_names');
      expect(after).toEqual(before);
    });

    it('↓ on the first card likewise preserves aria-labels', () => {
      document.body.innerHTML = `
        <form>
          <fieldset>
            <div data-cardstack="given_names" data-cardstack-cap="5">${indexedCardHTML('given_names', 3)}</div>
          </fieldset>
        </form>`;
      eval(REORDER_SRC);
      const before = snapshotAriaLabels('given_names');
      const downs = document.querySelectorAll('[data-cardstack-reorder="down"]');
      downs[0].click();
      expect(values('given_names')).toEqual(['v1', 'v0', 'v2']);
      const after = snapshotAriaLabels('given_names');
      expect(after).toEqual(before);
    });
  });

  // Issue #145 — keyboard-only reorder UX. After a value swap, focus must
  // follow the value: land on the neighbor's same-direction button so a
  // user can keep pressing the arrow key to walk the value through the
  // stack. At the boundary (neighbor's same-direction button is disabled),
  // fall back to the neighbor's input — the value just landed there.
  describe('focus-follows-value after ↑↓ swap (#145)', () => {
    function neighborButton(field, cardIdx, direction) {
      const cards = document.querySelectorAll(`[data-cardstack-card="${field}"]`);
      return cards[cardIdx].querySelector(`[data-cardstack-reorder="${direction}"]`);
    }

    function neighborInput(field, cardIdx) {
      const cards = document.querySelectorAll(`[data-cardstack-card="${field}"]`);
      return cards[cardIdx].querySelector(`input[name="${field}"]`);
    }

    // 4 cards needed: with 3 cards, the middle card's neighbors are both
    // boundaries (top has ↑ disabled, bottom has ↓ disabled), so the
    // interior-fallthrough path is unreachable.
    it("Interior ↑ moves focus to neighbor card's ↑ button", () => {
      setupDOM(4);
      const ups = reorderButtons('given_names', 'up');
      ups[2].click();
      expect(document.activeElement).toBe(neighborButton('given_names', 1, 'up'));
    });

    it("Interior ↓ moves focus to neighbor card's ↓ button", () => {
      setupDOM(4);
      const downs = reorderButtons('given_names', 'down');
      downs[1].click();
      expect(document.activeElement).toBe(neighborButton('given_names', 2, 'down'));
    });

    it("Boundary ↑ (neighbor is topmost) falls back to neighbor's input", () => {
      setupDOM(2);
      const ups = reorderButtons('given_names', 'up');
      ups[1].click();
      expect(document.activeElement).toBe(neighborInput('given_names', 0));
    });

    it("Boundary ↓ (neighbor is bottommost) falls back to neighbor's input", () => {
      setupDOM(2);
      const downs = reorderButtons('given_names', 'down');
      downs[0].click();
      expect(document.activeElement).toBe(neighborInput('given_names', 1));
    });

    it('Repeated ↑ on activeElement walks a value to the top', () => {
      setupDOM(4);
      reorderButtons('given_names', 'up')[3].click();
      // After move 1: [v0, v1, v3, v2], focus on card-3's ↑.
      document.activeElement.click();
      // After move 2: [v0, v3, v1, v2], focus on card-2's ↑.
      document.activeElement.click();
      // After move 3: [v3, v0, v1, v2], focus falls back to card-1's input.
      expect(values('given_names')).toEqual(['v3', 'v0', 'v1', 'v2']);
      expect(document.activeElement).toBe(neighborInput('given_names', 0));
    });
  });
});
