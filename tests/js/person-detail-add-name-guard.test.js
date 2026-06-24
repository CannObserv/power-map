/**
 * Tests for src/static/admin/person-detail-add-name-guard.js
 *
 * The script disables the "+ Add name" button while an unsaved
 * `#name-row-new` row is on the page. Re-enables on `htmx:afterSwap`
 * fired against `#names-table` (Save / Edit-Cancel) and on the custom
 * `powerMap:newNameRowClosed` event dispatched by the new-name form's
 * inline Cancel handler.
 *
 * Pattern mirrors `tests/js/typeahead-combobox.test.js`: build DOM
 * fixture → eval the script → simulate events → assert state.
 */
import { readFileSync } from 'node:fs';
import { resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';

const __dirname = dirname(fileURLToPath(import.meta.url));
const scriptCode = readFileSync(
  resolve(__dirname, '../../src/static/admin/person-detail-add-name-guard.js'),
  'utf-8',
);

function setup() {
  document.body.innerHTML = `
    <button id="add-name-btn" type="button">+ Add name</button>
    <table id="names-table"><tbody></tbody></table>
  `;
  // Script's IIFE checks document.readyState; jsdom reports 'complete' once
  // the page is set up, so init() runs immediately on eval.
  eval(scriptCode);
}

function btn() {
  return document.getElementById('add-name-btn');
}
function tbody() {
  return document.querySelector('#names-table tbody');
}
function table() {
  return document.getElementById('names-table');
}

// Global listener cleanup — see docs/STYLE.md §33.
// The script's IIFE attaches a 'powerMap:newNameRowClosed' listener to
// document on every eval. Without removeEventListener in afterEach those
// handlers accumulate and a single dispatch in test N triggers N firings.
let addSpy;

beforeEach(() => {
  addSpy = vi.spyOn(document, 'addEventListener');
  setup();
});

afterEach(() => {
  for (const [type, fn] of addSpy.mock.calls) {
    document.removeEventListener(type, fn);
  }
  addSpy.mockRestore();
  document.body.innerHTML = '';
});

describe('person-detail-add-name-guard', () => {
  it('button starts enabled when no unsaved new-name row exists', () => {
    expect(btn().disabled).toBe(false);
  });

  it('disables the button when a name-row-new appears via htmx:afterSwap', () => {
    tbody().innerHTML = '<tr id="name-row-new"><td>new</td></tr>';
    table().dispatchEvent(new Event('htmx:afterSwap', { bubbles: true }));
    expect(btn().disabled).toBe(true);
  });

  it('re-enables the button when the row is removed and the close event fires', () => {
    // Disable first.
    tbody().innerHTML = '<tr id="name-row-new"><td>new</td></tr>';
    table().dispatchEvent(new Event('htmx:afterSwap', { bubbles: true }));
    expect(btn().disabled).toBe(true);

    // Cancel: row is removed and the custom event fires.
    document.getElementById('name-row-new').remove();
    document.dispatchEvent(new CustomEvent('powerMap:newNameRowClosed'));
    expect(btn().disabled).toBe(false);
  });

  it('re-enables the button after Save replaces the tbody (no new-row in result)', () => {
    // Disable first.
    tbody().innerHTML = '<tr id="name-row-new"><td>new</td></tr>';
    table().dispatchEvent(new Event('htmx:afterSwap', { bubbles: true }));
    expect(btn().disabled).toBe(true);

    // Save returns a fresh tbody without a name-row-new.
    tbody().innerHTML = '<tr id="name-row-existing"><td>existing</td></tr>';
    table().dispatchEvent(new Event('htmx:afterSwap', { bubbles: true }));
    expect(btn().disabled).toBe(false);
  });

  it('syncs on htmx:afterSwap from any region (document-scoped, #237)', () => {
    // Loaded site-wide from base.html, the guard listens on document (not the
    // table) so it survives hx-boost and so outerHTML row swaps that fire on a
    // <tr> are caught. #name-row-new is unique page-wide, so a global id check
    // is correct — reacting to any swap is a harmless idempotent re-check.
    expect(btn().disabled).toBe(false);
    var sibling = document.createElement('div');
    sibling.id = 'other-region';
    sibling.innerHTML = '<span id="name-row-new"></span>';
    document.body.appendChild(sibling);
    sibling.dispatchEvent(new Event('htmx:afterSwap', { bubbles: true }));
    // The document-scoped listener sees the (page-unique) new-row id and
    // disables the button.
    expect(btn().disabled).toBe(true);
  });

  it('activates after a boosted navigation swaps the button in (htmx:load)', () => {
    // Regression scenario (#237): the guard loads on a page WITHOUT the
    // add-name button (e.g. dashboard), then hx-boost swaps in the person
    // detail page. The once-registered htmx:load listener must re-resolve the
    // button and sync it.
    document.body.innerHTML = '';
    for (const [type, fn] of addSpy.mock.calls) {
      document.removeEventListener(type, fn);
    }
    addSpy.mockClear();
    eval(scriptCode);

    document.body.innerHTML = `
      <button id="add-name-btn" type="button">+ Add name</button>
      <table id="names-table"><tbody>
        <tr id="name-row-new"><td>new</td></tr>
      </tbody></table>
    `;
    document.dispatchEvent(new Event('htmx:load', { bubbles: true }));
    expect(btn().disabled).toBe(true);
  });

  it('powerMap:newNameRowClosed sync is idempotent', () => {
    // No row, fire close event — disabled stays false.
    document.dispatchEvent(new CustomEvent('powerMap:newNameRowClosed'));
    expect(btn().disabled).toBe(false);
    // Disable, then fire close TWICE — second is a no-op.
    tbody().innerHTML = '<tr id="name-row-new"></tr>';
    table().dispatchEvent(new Event('htmx:afterSwap', { bubbles: true }));
    expect(btn().disabled).toBe(true);
    document.getElementById('name-row-new').remove();
    document.dispatchEvent(new CustomEvent('powerMap:newNameRowClosed'));
    document.dispatchEvent(new CustomEvent('powerMap:newNameRowClosed'));
    expect(btn().disabled).toBe(false);
  });
});
