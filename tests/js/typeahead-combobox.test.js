/**
 * Tests for src/static/admin/typeahead-combobox.js
 *
 * Pattern: build DOM fixture → eval() the factory → call initTypeaheadCombobox()
 * → simulate events → assert state.
 *
 * The key regression test: mouse selection via mousedown must populate the input
 * and hidden field.  The bug was that a plain 'click' listener missed selections
 * in some browsers because mousedown fires first, blurs the input, and the click
 * is swallowed or re-routed before the listbox handler can run.
 */
import { readFileSync } from 'node:fs';
import { resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';

const __dirname = dirname(fileURLToPath(import.meta.url));
const scriptCode = readFileSync(
  resolve(__dirname, '../../src/static/admin/typeahead-combobox.js'),
  'utf-8',
);

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const INPUT_ID = 'test-search';
const LIST_ID = 'test-results';
const HIDDEN_ID = 'test-hidden';

// ---------------------------------------------------------------------------
// Global listener cleanup — see docs/STYLE.md §33.
//
// The factory registers document-level click + scroll listeners on every
// openDropdown() call (and removes them on closeDropdown()). Tests that exit
// with the dropdown still open would leave those listeners attached, so we
// also dispatch Escape to close any live dropdown — that path lets the
// factory's own removeEventListener fire. The vi.spyOn block then catches
// any *other* listeners attached during the test (defense-in-depth).
// ---------------------------------------------------------------------------

let addSpy;

beforeEach(() => {
  addSpy = vi.spyOn(document, 'addEventListener');
});

afterEach(() => {
  // First, give the factory a chance to clean up via its own teardown path.
  const input = document.getElementById(INPUT_ID);
  if (input) {
    input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
  }
  // Then drop anything the spy still recorded so cross-test state is clean.
  for (const [type, fn] of addSpy.mock.calls) {
    document.removeEventListener(type, fn);
  }
  addSpy.mockRestore();
  document.body.innerHTML = '';
});

function setup() {
  document.body.innerHTML = `
    <input id="${INPUT_ID}" type="text" autocomplete="off"
           role="combobox" aria-expanded="false"
           aria-haspopup="listbox" aria-controls="${LIST_ID}" aria-autocomplete="list">
    <input type="hidden" id="${HIDDEN_ID}" value="">
    <ul id="${LIST_ID}" class="typeahead-results" role="listbox" style="display:none"></ul>
  `;
  eval(scriptCode); // no-eval disabled for test files
  window.initTypeaheadCombobox({ inputId: INPUT_ID, listboxId: LIST_ID, hiddenId: HIDDEN_ID });
}

function inp() {
  return document.getElementById(INPUT_ID);
}
function ul() {
  return document.getElementById(LIST_ID);
}
function hidden() {
  return document.getElementById(HIDDEN_ID);
}

/** Simulate HTMX populating the listbox with results then firing afterSwap. */
function populateResults(
  items = [
    { id: 'id-1', label: 'Alpha' },
    { id: 'id-2', label: 'Beta' },
  ],
) {
  ul().innerHTML = items
    .map((r) => `<li id="opt-${r.id}" data-id="${r.id}" data-label="${r.label}">${r.label}</li>`)
    .join('');
  ul().dispatchEvent(new Event('htmx:afterSwap', { bubbles: false }));
}

function getItems() {
  return Array.from(ul().querySelectorAll('li[data-id]'));
}

// ---------------------------------------------------------------------------
// Dropdown open / close via afterSwap
// ---------------------------------------------------------------------------

describe('dropdown open/close', () => {
  beforeEach(() => setup());

  it('opens after afterSwap with results', () => {
    populateResults();
    expect(ul().style.display).toBe('block');
    expect(inp().getAttribute('aria-expanded')).toBe('true');
  });

  it('closes after afterSwap with empty results', () => {
    populateResults();
    ul().innerHTML = '';
    ul().dispatchEvent(new Event('htmx:afterSwap', { bubbles: false }));
    expect(ul().style.display).toBe('none');
    expect(inp().getAttribute('aria-expanded')).toBe('false');
  });

  it('scopes li ids to the listbox id', () => {
    populateResults([{ id: 'org-1', label: 'Org One' }]);
    const li = getItems()[0];
    expect(li.id).toBe(`${LIST_ID}-opt-org-1`);
  });
});

// ---------------------------------------------------------------------------
// Mouse selection — regression for the mouse-click bug
//
// Root cause: the old code used a 'click' listener on the ul.  In some browsers
// mousedown fires first, blurs the input, and the subsequent click is swallowed
// or re-targeted before the ul handler can run.
//
// Fix: use 'mousedown' with e.preventDefault() so focus never leaves the input.
// These tests dispatch mousedown (not click) to confirm the fix is in place.
// ---------------------------------------------------------------------------

describe('mouse selection via mousedown', () => {
  beforeEach(() => {
    setup();
    populateResults([
      { id: 'org-abc', label: 'Acme Corp' },
      { id: 'org-xyz', label: 'Zenith LLC' },
    ]);
  });

  it('sets inp.value to the item label on mousedown', () => {
    const li = getItems()[0];
    li.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, cancelable: true }));
    expect(inp().value).toBe('Acme Corp');
  });

  it('sets hidden.value to the item id on mousedown', () => {
    const li = getItems()[0];
    li.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, cancelable: true }));
    expect(hidden().value).toBe('org-abc');
  });

  it('closes the dropdown after mousedown selection', () => {
    const li = getItems()[0];
    li.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, cancelable: true }));
    expect(ul().style.display).toBe('none');
    expect(inp().getAttribute('aria-expanded')).toBe('false');
  });

  it('selects the second item correctly', () => {
    const items = getItems();
    items[1].dispatchEvent(new MouseEvent('mousedown', { bubbles: true, cancelable: true }));
    expect(inp().value).toBe('Zenith LLC');
    expect(hidden().value).toBe('org-xyz');
  });

  it('mousedown on the ul itself (not a li) does not select', () => {
    ul().dispatchEvent(new MouseEvent('mousedown', { bubbles: true, cancelable: true }));
    expect(inp().value).toBe('');
    expect(hidden().value).toBe('');
    // dropdown stays open — no item was targeted
    expect(ul().style.display).toBe('block');
  });
});

// ---------------------------------------------------------------------------
// Keyboard selection
// ---------------------------------------------------------------------------

describe('keyboard selection', () => {
  beforeEach(() => {
    setup();
    populateResults([
      { id: 'p-1', label: 'Person One' },
      { id: 'p-2', label: 'Person Two' },
    ]);
  });

  it('ArrowDown + Enter selects first item', () => {
    inp().dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowDown', bubbles: true }));
    inp().dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
    expect(inp().value).toBe('Person One');
    expect(hidden().value).toBe('p-1');
  });

  it('ArrowDown twice + Enter selects second item', () => {
    inp().dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowDown', bubbles: true }));
    inp().dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowDown', bubbles: true }));
    inp().dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
    expect(inp().value).toBe('Person Two');
    expect(hidden().value).toBe('p-2');
  });

  it('ArrowUp from -1 clamps to -1 (no selection)', () => {
    inp().dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowUp', bubbles: true }));
    inp().dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
    // activeIdx is -1; Enter does nothing
    expect(inp().value).toBe('');
  });

  it('Escape closes the dropdown', () => {
    inp().dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
    expect(ul().style.display).toBe('none');
    expect(inp().getAttribute('aria-expanded')).toBe('false');
  });

  it('sets aria-activedescendant on ArrowDown', () => {
    inp().dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowDown', bubbles: true }));
    const items = getItems();
    expect(inp().getAttribute('aria-activedescendant')).toBe(items[0].id);
  });

  it('clears aria-activedescendant on close', () => {
    inp().dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowDown', bubbles: true }));
    inp().dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
    expect(inp().getAttribute('aria-activedescendant')).toBe('');
  });
});

// ---------------------------------------------------------------------------
// Outside-click closes the dropdown
// ---------------------------------------------------------------------------

describe('outside click', () => {
  beforeEach(() => {
    setup();
    populateResults();
  });

  it('closes on click outside the listbox and input', () => {
    const other = document.createElement('button');
    document.body.appendChild(other);
    other.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    expect(ul().style.display).toBe('none');
  });

  it('does not close when clicking on the input', () => {
    inp().dispatchEvent(new MouseEvent('click', { bubbles: true }));
    expect(ul().style.display).toBe('block');
  });
});

// ---------------------------------------------------------------------------
// onSelect callback
// ---------------------------------------------------------------------------

describe('onSelect callback', () => {
  it('calls onSelect with the selected item id on mousedown', () => {
    document.body.innerHTML = `
      <input id="${INPUT_ID}" type="text" autocomplete="off"
             role="combobox" aria-expanded="false"
             aria-haspopup="listbox" aria-controls="${LIST_ID}" aria-autocomplete="list">
      <input type="hidden" id="${HIDDEN_ID}" value="">
      <ul id="${LIST_ID}" class="typeahead-results" role="listbox" style="display:none"></ul>
    `;
    eval(scriptCode);
    const onSelect = vi.fn();
    window.initTypeaheadCombobox({
      inputId: INPUT_ID,
      listboxId: LIST_ID,
      hiddenId: HIDDEN_ID,
      onSelect,
    });
    populateResults([{ id: 'org-1', label: 'Acme' }]);
    const li = getItems()[0];
    li.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, cancelable: true }));
    expect(onSelect).toHaveBeenCalledTimes(1);
    expect(onSelect).toHaveBeenCalledWith('org-1');
  });

  it('calls onSelect with the selected item id on keyboard Enter', () => {
    document.body.innerHTML = `
      <input id="${INPUT_ID}" type="text" autocomplete="off"
             role="combobox" aria-expanded="false"
             aria-haspopup="listbox" aria-controls="${LIST_ID}" aria-autocomplete="list">
      <input type="hidden" id="${HIDDEN_ID}" value="">
      <ul id="${LIST_ID}" class="typeahead-results" role="listbox" style="display:none"></ul>
    `;
    eval(scriptCode);
    const onSelect = vi.fn();
    window.initTypeaheadCombobox({
      inputId: INPUT_ID,
      listboxId: LIST_ID,
      hiddenId: HIDDEN_ID,
      onSelect,
    });
    populateResults([{ id: 'org-2', label: 'Beta' }]);
    inp().dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowDown', bubbles: true }));
    inp().dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
    expect(onSelect).toHaveBeenCalledTimes(1);
    expect(onSelect).toHaveBeenCalledWith('org-2');
  });

  it('does not throw when onSelect is not provided', () => {
    setup(); // uses existing setup() without onSelect
    populateResults([{ id: 'org-3', label: 'Gamma' }]);
    const li = getItems()[0];
    expect(() =>
      li.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, cancelable: true })),
    ).not.toThrow();
  });
});
