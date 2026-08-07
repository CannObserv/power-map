/**
 * Tests for src/static/admin/citations.js
 *
 * A name/event row's "Cite" button (data-citations-toggle="<own-subrow-id>")
 * opens the citations panel as a sub-row. The delegated htmx:beforeRequest
 * listener enforces one-open-at-a-time and makes Cite a toggle:
 *   - opening: close any other open sub-row, let the request proceed;
 *   - reopening the same row: close it and cancel the request (preventDefault).
 * A non-citations request is ignored.
 *
 * Pattern: build DOM fixture → eval the script → dispatch htmx:beforeRequest →
 * assert sub-row removal + defaultPrevented. Listener-cleanup per STYLE.md `docs/TESTING.md` § Vitest test conventions.
 */
import { readFileSync } from 'node:fs';
import { resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';

const __dirname = dirname(fileURLToPath(import.meta.url));
const scriptCode = readFileSync(resolve(__dirname, '../../src/static/admin/citations.js'), 'utf-8');

function mount() {
  eval(scriptCode);
}

function fireBeforeRequest(el) {
  const evt = new Event('htmx:beforeRequest', {
    bubbles: true,
    cancelable: true,
  });
  el.dispatchEvent(evt);
  return evt;
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

describe('citations toggle', () => {
  it('opening a row closes other open sub-rows and lets the request proceed', () => {
    document.body.innerHTML = `
      <table><tbody>
        <tr id="name-row-A"></tr>
        <tr class="citations-subrow" id="citations-subrow-A"><td></td></tr>
        <tr id="name-row-B"></tr>
      </tbody></table>
      <button id="cite-B" data-citations-toggle="citations-subrow-B" type="button">Cite</button>
    `;
    mount();
    const evt = fireBeforeRequest(document.getElementById('cite-B'));
    // The other row's sub-row is closed…
    expect(document.getElementById('citations-subrow-A')).toBeNull();
    // …and the request is NOT cancelled (B will open via htmx).
    expect(evt.defaultPrevented).toBe(false);
  });

  it('reopening the same row closes it and cancels the request', () => {
    document.body.innerHTML = `
      <table><tbody>
        <tr id="name-row-A"></tr>
        <tr class="citations-subrow" id="citations-subrow-A"><td></td></tr>
      </tbody></table>
      <button id="cite-A" data-citations-toggle="citations-subrow-A" type="button">Cite</button>
    `;
    mount();
    const evt = fireBeforeRequest(document.getElementById('cite-A'));
    expect(document.getElementById('citations-subrow-A')).toBeNull();
    expect(evt.defaultPrevented).toBe(true);
  });

  it('reopening one row with several open removes them all and cancels', () => {
    // Intersection of the two behaviors: multiple sub-rows open, reopen one →
    // every sub-row is closed AND the re-fetch is cancelled.
    document.body.innerHTML = `
      <table><tbody>
        <tr id="name-row-A"></tr>
        <tr class="citations-subrow" id="citations-subrow-A"><td></td></tr>
        <tr id="name-row-B"></tr>
        <tr class="citations-subrow" id="citations-subrow-B"><td></td></tr>
      </tbody></table>
      <button id="cite-A" data-citations-toggle="citations-subrow-A" type="button">Cite</button>
    `;
    mount();
    const evt = fireBeforeRequest(document.getElementById('cite-A'));
    expect(document.querySelectorAll('tr.citations-subrow').length).toBe(0);
    expect(evt.defaultPrevented).toBe(true);
  });

  it('ignores requests from non-citations elements', () => {
    document.body.innerHTML = `
      <table><tbody>
        <tr class="citations-subrow" id="citations-subrow-A"><td></td></tr>
      </tbody></table>
      <button id="edit" type="button">Edit</button>
    `;
    mount();
    const evt = fireBeforeRequest(document.getElementById('edit'));
    // An unrelated htmx request must not disturb an open sub-row or cancel.
    expect(document.getElementById('citations-subrow-A')).not.toBeNull();
    expect(evt.defaultPrevented).toBe(false);
  });
});
