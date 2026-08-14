/**
 * Tests for the typeahead mount queue (#435).
 *
 * The queue stub lives inline and non-deferred in `src/templates/admin/base.html`
 * (it must run during parse, before any deferred script); `typeahead-combobox.js`
 * replaces it and drains the queue at the bottom of the file. This suite covers
 * the ordering that broke on hard page loads: an inline `<body>` mount executes
 * BEFORE the deferred factory, so the call has to survive until the factory
 * exists — and must then wire exactly once.
 *
 * Pattern: build DOM fixture → eval() the stub extracted from base.html → mount
 * (queued) → eval() the factory (drains) → assert the combobox behaves.
 */
import { readFileSync } from 'node:fs';
import { resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';

const __dirname = dirname(fileURLToPath(import.meta.url));

const factoryCode = readFileSync(
  resolve(__dirname, '../../src/static/admin/typeahead-combobox.js'),
  'utf-8',
);

/**
 * The stub ships inline in base.html, so extract the one `<script>` block that
 * defines the queue rather than duplicating it here — a copy would keep passing
 * after the shipped snippet regressed.
 */
const baseTemplate = readFileSync(
  resolve(__dirname, '../../src/templates/admin/base.html'),
  'utf-8',
);
const stubCode = (() => {
  const blocks = [...baseTemplate.matchAll(/<script>([\s\S]*?)<\/script>/g)].map((m) => m[1]);
  const match = blocks.filter((b) => b.includes('__pmTypeaheadQueue'));
  if (match.length !== 1) {
    throw new Error(`expected exactly one inline queue-stub script, found ${match.length}`);
  }
  return match[0];
})();

const INPUT_ID = 'q-search';
const LIST_ID = 'q-results';
const HIDDEN_ID = 'q-hidden';

let addSpy;

beforeEach(() => {
  addSpy = vi.spyOn(document, 'addEventListener');
  document.body.innerHTML = `
    <input id="${INPUT_ID}" type="text" role="combobox" aria-expanded="false">
    <input type="hidden" id="${HIDDEN_ID}" value="">
    <ul id="${LIST_ID}" class="typeahead-results" role="listbox" style="display:none"></ul>
  `;
});

afterEach(() => {
  // Let the factory tear its own document listeners down, then drop anything
  // the spy still recorded (docs/TESTING.md § Vitest test conventions).
  const input = document.getElementById(INPUT_ID);
  if (input) {
    input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
  }
  for (const [type, fn] of addSpy.mock.calls) {
    document.removeEventListener(type, fn);
  }
  addSpy.mockRestore();
  document.body.innerHTML = '';
  delete window.initTypeaheadCombobox;
  delete window.__pmTypeaheadQueue;
});

function mount(extra = {}) {
  return window.initTypeaheadCombobox({
    inputId: INPUT_ID,
    listboxId: LIST_ID,
    hiddenId: HIDDEN_ID,
    ...extra,
  });
}

function ul() {
  return document.getElementById(LIST_ID);
}
function inp() {
  return document.getElementById(INPUT_ID);
}
function hidden() {
  return document.getElementById(HIDDEN_ID);
}

/** Simulate HTMX populating the listbox and firing afterSwap. */
function populateResults() {
  ul().innerHTML =
    '<li id="opt-id-1" data-id="id-1" data-label="Alpha">Alpha</li>' +
    '<li id="opt-id-2" data-id="id-2" data-label="Beta">Beta</li>';
  ul().dispatchEvent(new Event('htmx:afterSwap', { bubbles: false }));
}

describe('hard-load ordering: inline mount before the deferred factory', () => {
  beforeEach(() => {
    eval(stubCode); // no-eval disabled for test files
  });

  it('queues the mount instead of throwing when the factory is absent', () => {
    mount();
    expect(window.__pmTypeaheadQueue).toHaveLength(1);
    expect(window.__pmTypeaheadQueue[0].config.inputId).toBe(INPUT_ID);
  });

  it('leaves the combobox unwired until the factory loads', () => {
    mount();
    populateResults();
    expect(ul().style.display).toBe('none');
    expect(inp().getAttribute('aria-expanded')).toBe('false');
  });

  it('wires the queued mount when the factory loads', () => {
    mount();
    eval(factoryCode);
    populateResults();
    expect(ul().style.display).toBe('block');
    expect(inp().getAttribute('aria-expanded')).toBe('true');
    // Scoped-id contract applies to the drained mount too.
    expect(ul().querySelector('li').id).toBe(`${LIST_ID}-opt-id-1`);
  });

  it('empties the queue and replaces the stub once drained', () => {
    mount();
    eval(factoryCode);
    expect(window.__pmTypeaheadQueue).toBeNull();
    expect(window.initTypeaheadCombobox.name).toBe('initTypeaheadCombobox');
  });

  it('wires a queued mount exactly once', () => {
    const onSelect = vi.fn();
    mount({ onSelect });
    eval(factoryCode);
    populateResults();
    const li = ul().querySelector('li[data-id]');
    li.dispatchEvent(new MouseEvent('mousedown', { bubbles: true }));
    expect(onSelect).toHaveBeenCalledTimes(1);
    expect(hidden().value).toBe('id-1');
  });

  it('drains multiple queued mounts in order', () => {
    document.body.insertAdjacentHTML(
      'beforeend',
      `<input id="b-search" type="text"><input type="hidden" id="b-hidden" value="">
       <ul id="b-results" role="listbox" style="display:none"></ul>`,
    );
    mount();
    window.initTypeaheadCombobox({
      inputId: 'b-search',
      listboxId: 'b-results',
      hiddenId: 'b-hidden',
    });
    expect(window.__pmTypeaheadQueue).toHaveLength(2);
    eval(factoryCode);
    populateResults();
    expect(ul().style.display).toBe('block');
    // The second mount is live as well: its own afterSwap opens its listbox.
    const b = document.getElementById('b-results');
    b.innerHTML = '<li id="opt-b1" data-id="b1" data-label="B One">B One</li>';
    b.dispatchEvent(new Event('htmx:afterSwap', { bubbles: false }));
    expect(b.style.display).toBe('block');
  });

  it('returns a deferred handle whose clear() reaches the real handle', () => {
    const onClear = vi.fn();
    const handle = mount({ onClear });
    expect(() => handle.clear()).not.toThrow(); // before the factory: no-op
    eval(factoryCode);
    populateResults();
    ul()
      .querySelector('li[data-id]')
      .dispatchEvent(new MouseEvent('mousedown', { bubbles: true }));
    expect(hidden().value).toBe('id-1');
    handle.clear();
    expect(hidden().value).toBe('');
    expect(inp().value).toBe('');
    expect(onClear).toHaveBeenCalledTimes(1);
  });
});

describe('boosted-nav ordering: mount after the factory loaded', () => {
  it('calls the real factory directly and never queues', () => {
    eval(factoryCode);
    expect(window.__pmTypeaheadQueue).toBeNull();
    const handle = mount();
    expect(window.__pmTypeaheadQueue).toBeNull();
    populateResults();
    expect(ul().style.display).toBe('block');
    expect(typeof handle.clear).toBe('function');
  });

  it('drain is a no-op when nothing queued', () => {
    eval(stubCode);
    expect(window.__pmTypeaheadQueue).toHaveLength(0);
    eval(factoryCode);
    expect(window.__pmTypeaheadQueue).toBeNull();
  });
});
