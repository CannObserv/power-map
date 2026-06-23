/**
 * Tests for src/static/admin/event-add-guard.js
 *
 * The script disables the "+ Add event" button while an unsaved new-event
 * row is on the page. The button must carry data-events-table and
 * data-new-row-id attributes. Re-enables on `htmx:afterSwap` fired anywhere
 * on document (covers both tbody re-renders and new-row outerHTML swaps on
 * the <tr>) and on `powerMap:newEventRowClosed` dispatched by the new-event
 * form's inline Cancel handler.
 *
 * Pattern mirrors tests/js/person-detail-add-name-guard.test.js.
 */
import { readFileSync } from 'node:fs';
import { resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';

const __dirname = dirname(fileURLToPath(import.meta.url));
const scriptCode = readFileSync(
  resolve(__dirname, '../../src/static/admin/event-add-guard.js'),
  'utf-8',
);

function setup() {
  document.body.innerHTML = `
    <button id="add-event-btn" type="button"
            data-events-table="org-events-table"
            data-new-row-id="org-event-row-new">+ Add event</button>
    <table id="org-events-table"><tbody></tbody></table>
  `;
  eval(scriptCode);
}

function btn() {
  return document.getElementById('add-event-btn');
}
function tbody() {
  return document.querySelector('#org-events-table tbody');
}

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

describe('event-add-guard', () => {
  it('button starts enabled when no unsaved new-event row exists', () => {
    expect(btn().disabled).toBe(false);
  });

  it('disables the button when a new-event row appears and htmx:afterSwap fires', () => {
    tbody().innerHTML = '<tr id="org-event-row-new"><td>new</td></tr>';
    document.dispatchEvent(new Event('htmx:afterSwap', { bubbles: true }));
    expect(btn().disabled).toBe(true);
  });

  it('re-enables the button when the row is removed and the close event fires', () => {
    tbody().innerHTML = '<tr id="org-event-row-new"><td>new</td></tr>';
    document.dispatchEvent(new Event('htmx:afterSwap', { bubbles: true }));
    expect(btn().disabled).toBe(true);

    document.getElementById('org-event-row-new').remove();
    document.dispatchEvent(new CustomEvent('powerMap:newEventRowClosed'));
    expect(btn().disabled).toBe(false);
  });

  it('re-enables the button after Save replaces the new-event row (outerHTML swap)', () => {
    // New row is added; guard disables button.
    tbody().innerHTML = '<tr id="org-event-row-new"><td>new</td></tr>';
    document.dispatchEvent(new Event('htmx:afterSwap', { bubbles: true }));
    expect(btn().disabled).toBe(true);

    // Save: outerHTML swap replaces the new row with a read row. The swap
    // fires htmx:afterSwap on document (not the table), which is why the
    // guard listens on document rather than the table element.
    document.getElementById('org-event-row-new').outerHTML =
      '<tr id="org-event-row-01ABC"><td>saved</td></tr>';
    document.dispatchEvent(new Event('htmx:afterSwap', { bubbles: true }));
    expect(btn().disabled).toBe(false);
  });

  it('syncs on htmx:afterSwap from an unrelated region (document-scoped listener)', () => {
    // Unlike the names guard (table-scoped), the event guard intentionally
    // listens on document so outerHTML row swaps trigger it. A consequence
    // is that unrelated swaps also call sync() — which is harmless since
    // sync() only checks getElementById.
    tbody().innerHTML = '<tr id="org-event-row-new"><td>new</td></tr>';
    var sibling = document.createElement('div');
    sibling.id = 'other-region';
    document.body.appendChild(sibling);
    sibling.dispatchEvent(new Event('htmx:afterSwap', { bubbles: true }));
    // New row is still present, so button should be disabled after sync.
    expect(btn().disabled).toBe(true);
  });

  it('registers document listeners even when the events table is absent', () => {
    // The guard is loaded site-wide from base.html (#237), so it runs on pages
    // with no events table. It must still register its document listeners up
    // front — otherwise it could never activate when a boosted navigation later
    // swaps a table in. sync() is defensive, so dispatching events is a safe
    // no-op when no button/table is present.
    document.body.innerHTML = ''; // no button, no table
    for (const [type, fn] of addSpy.mock.calls) {
      document.removeEventListener(type, fn);
    }
    addSpy.mockClear();
    eval(scriptCode);
    const types = addSpy.mock.calls.map(([t]) => t);
    expect(types).toContain('htmx:afterSwap');
    expect(types).toContain('htmx:load');
    expect(types).toContain('powerMap:newEventRowClosed');
    // Dispatching does not throw with nothing on the page.
    expect(() => {
      document.dispatchEvent(new Event('htmx:afterSwap', { bubbles: true }));
      document.dispatchEvent(new Event('htmx:load', { bubbles: true }));
    }).not.toThrow();
  });

  it('activates after a boosted navigation swaps the button in (htmx:load)', () => {
    // Simulate the regression scenario: the script loaded on a page WITHOUT the
    // events button (e.g. dashboard), then hx-boost swaps in the detail page.
    document.body.innerHTML = '';
    for (const [type, fn] of addSpy.mock.calls) {
      document.removeEventListener(type, fn);
    }
    addSpy.mockClear();
    eval(scriptCode);

    // Boosted nav renders the detail page (button + an unsaved new-event row).
    document.body.innerHTML = `
      <button id="add-event-btn" type="button"
              data-events-table="org-events-table"
              data-new-row-id="org-event-row-new">+ Add event</button>
      <table id="org-events-table"><tbody>
        <tr id="org-event-row-new"><td>new</td></tr>
      </tbody></table>
    `;
    document.dispatchEvent(new Event('htmx:load', { bubbles: true }));
    // The once-registered listener re-resolved the button and disabled it.
    expect(btn().disabled).toBe(true);
  });

  it('powerMap:newEventRowClosed sync is idempotent', () => {
    document.dispatchEvent(new CustomEvent('powerMap:newEventRowClosed'));
    expect(btn().disabled).toBe(false);

    tbody().innerHTML = '<tr id="org-event-row-new"></tr>';
    document.dispatchEvent(new Event('htmx:afterSwap', { bubbles: true }));
    expect(btn().disabled).toBe(true);

    document.getElementById('org-event-row-new').remove();
    document.dispatchEvent(new CustomEvent('powerMap:newEventRowClosed'));
    document.dispatchEvent(new CustomEvent('powerMap:newEventRowClosed'));
    expect(btn().disabled).toBe(false);
  });
});
