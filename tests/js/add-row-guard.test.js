/**
 * Tests for src/static/admin/add-row-guard.js
 *
 * The generic guard disables every "+ Add …" button while its own unsaved
 * new-row is on the page. Each button opts in with a `data-new-row-id`
 * attribute naming the `<tr id>` its inline-add form renders; the button is
 * disabled iff `getElementById(<that id>)` resolves. Re-syncs on
 * `htmx:afterSwap` / `htmx:load` and on the custom `powerMap:newRowClosed`
 * event dispatched by each new-row form's inline Cancel handler.
 *
 * Generalizes (and replaces) person-detail-add-name-guard.js and
 * event-add-guard.js. Unlike those single-button-by-id guards, this one scans
 * `button[data-new-row-id]`, so the org detail page's many add-buttons (names,
 * acronyms, contacts, …) are each guarded independently. Issue #238.
 *
 * Pattern: build DOM fixture → eval the script → simulate events → assert
 * state. Listener-cleanup block per docs/STYLE.md §33 (reference impl:
 * tests/js/person-name-row-typeahead.test.js).
 */
import { readFileSync } from 'node:fs';
import { resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';

const __dirname = dirname(fileURLToPath(import.meta.url));
const scriptCode = readFileSync(
  resolve(__dirname, '../../src/static/admin/add-row-guard.js'),
  'utf-8',
);

function mount() {
  // Script's IIFE checks document.readyState; jsdom/happy-dom report 'complete'
  // once the page is set up, so sync() runs immediately on eval.
  eval(scriptCode);
}

function btn(id) {
  return document.querySelector(`button[data-new-row-id="${id}"]`);
}

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

describe('add-row-guard', () => {
  it('button starts enabled when its new-row is absent', () => {
    document.body.innerHTML = `
      <button data-new-row-id="name-row-new" type="button">+ Add name</button>
      <table id="names-table"><tbody></tbody></table>
    `;
    mount();
    expect(btn('name-row-new').disabled).toBe(false);
  });

  it('disables the button when its new-row appears via htmx:afterSwap', () => {
    document.body.innerHTML = `
      <button data-new-row-id="name-row-new" type="button">+ Add name</button>
      <table id="names-table"><tbody></tbody></table>
    `;
    mount();
    document.querySelector('#names-table tbody').innerHTML =
      '<tr id="name-row-new"><td>new</td></tr>';
    document.dispatchEvent(new Event('htmx:afterSwap', { bubbles: true }));
    expect(btn('name-row-new').disabled).toBe(true);
  });

  it('guards multiple add-buttons on one page independently', () => {
    // The org detail page has many add-buttons; each must track only its own
    // new-row id. This is the capability the single-button-id guards lacked.
    document.body.innerHTML = `
      <button data-new-row-id="name-row-new" type="button">+ Add name</button>
      <button data-new-row-id="acronym-row-new" type="button">+ Add acronym</button>
      <table id="acronyms-table"><tbody></tbody></table>
    `;
    mount();
    expect(btn('name-row-new').disabled).toBe(false);
    expect(btn('acronym-row-new').disabled).toBe(false);

    // Open an acronym new-row: only the acronym button disables.
    document.querySelector('#acronyms-table tbody').innerHTML =
      '<tr id="acronym-row-new"><td>new</td></tr>';
    document.dispatchEvent(new Event('htmx:afterSwap', { bubbles: true }));
    expect(btn('name-row-new').disabled).toBe(false);
    expect(btn('acronym-row-new').disabled).toBe(true);
  });

  it('re-enables the button when the row is removed and the close event fires', () => {
    document.body.innerHTML = `
      <button data-new-row-id="name-row-new" type="button">+ Add name</button>
      <table id="names-table"><tbody></tbody></table>
    `;
    mount();
    document.querySelector('#names-table tbody').innerHTML = '<tr id="name-row-new"></tr>';
    document.dispatchEvent(new Event('htmx:afterSwap', { bubbles: true }));
    expect(btn('name-row-new').disabled).toBe(true);

    // Cancel: client-side row removal + the custom event (no HTMX round-trip).
    document.getElementById('name-row-new').remove();
    document.dispatchEvent(new CustomEvent('powerMap:newRowClosed'));
    expect(btn('name-row-new').disabled).toBe(false);
  });

  it('re-enables after Save replaces the tbody (no new-row in result)', () => {
    document.body.innerHTML = `
      <button data-new-row-id="name-row-new" type="button">+ Add name</button>
      <table id="names-table"><tbody><tr id="name-row-new"></tr></tbody></table>
    `;
    mount();
    document.dispatchEvent(new Event('htmx:afterSwap', { bubbles: true }));
    expect(btn('name-row-new').disabled).toBe(true);

    document.querySelector('#names-table tbody').innerHTML = '<tr id="name-row-existing"></tr>';
    document.dispatchEvent(new Event('htmx:afterSwap', { bubbles: true }));
    expect(btn('name-row-new').disabled).toBe(false);
  });

  it('syncs on htmx:afterSwap from any region (document-scoped)', () => {
    document.body.innerHTML = `
      <button data-new-row-id="name-row-new" type="button">+ Add name</button>
    `;
    mount();
    expect(btn('name-row-new').disabled).toBe(false);
    const sibling = document.createElement('div');
    sibling.innerHTML = '<span id="name-row-new"></span>';
    document.body.appendChild(sibling);
    sibling.dispatchEvent(new Event('htmx:afterSwap', { bubbles: true }));
    expect(btn('name-row-new').disabled).toBe(true);
  });

  it('activates after a boosted navigation swaps the button in (htmx:load)', () => {
    // The guard loads on a page WITHOUT any add-button (e.g. dashboard), then
    // hx-boost swaps in a detail page. The once-registered htmx:load listener
    // must re-resolve buttons and sync them.
    document.body.innerHTML = '';
    mount();
    document.body.innerHTML = `
      <button data-new-row-id="name-row-new" type="button">+ Add name</button>
      <table id="names-table"><tbody>
        <tr id="name-row-new"></tr>
      </tbody></table>
    `;
    document.dispatchEvent(new Event('htmx:load', { bubbles: true }));
    expect(btn('name-row-new').disabled).toBe(true);
  });

  it('powerMap:newRowClosed sync is idempotent', () => {
    document.body.innerHTML = `
      <button data-new-row-id="name-row-new" type="button">+ Add name</button>
      <table id="names-table"><tbody></tbody></table>
    `;
    mount();
    document.dispatchEvent(new CustomEvent('powerMap:newRowClosed'));
    expect(btn('name-row-new').disabled).toBe(false);

    document.querySelector('#names-table tbody').innerHTML = '<tr id="name-row-new"></tr>';
    document.dispatchEvent(new Event('htmx:afterSwap', { bubbles: true }));
    expect(btn('name-row-new').disabled).toBe(true);

    document.getElementById('name-row-new').remove();
    document.dispatchEvent(new CustomEvent('powerMap:newRowClosed'));
    document.dispatchEvent(new CustomEvent('powerMap:newRowClosed'));
    expect(btn('name-row-new').disabled).toBe(false);
  });

  it('is a no-op when no guarded button is present', () => {
    document.body.innerHTML = '<table id="names-table"><tbody></tbody></table>';
    expect(() => mount()).not.toThrow();
    document.dispatchEvent(new Event('htmx:afterSwap', { bubbles: true }));
    document.dispatchEvent(new CustomEvent('powerMap:newRowClosed'));
  });
});
