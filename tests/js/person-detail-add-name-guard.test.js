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
import { describe, it, expect, beforeEach, afterEach } from 'vitest';

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

beforeEach(() => {
  setup();
});

afterEach(() => {
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
    document.body.dispatchEvent(new CustomEvent('powerMap:newNameRowClosed'));
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

  it('does NOT re-sync on htmx:afterSwap fired outside #names-table', () => {
    // A rogue swap somewhere else on the page that adds a name-row-new
    // *outside* the names table — the listener is scoped to the table, so
    // the button stays in its prior state. (Defensive: the rogue id
    // shouldn't exist, but the test pins down the scoping intent.)
    expect(btn().disabled).toBe(false);
    // Append a rogue row outside the names table and fire afterSwap on body.
    var rogue = document.createElement('div');
    rogue.id = 'rogue-region';
    rogue.innerHTML = '<span id="name-row-new"></span>';
    document.body.appendChild(rogue);
    document.body.dispatchEvent(new Event('htmx:afterSwap', { bubbles: false }));
    // Button is still enabled because the listener is on #names-table.
    expect(btn().disabled).toBe(false);
  });

  it('powerMap:newNameRowClosed sync is idempotent', () => {
    // No row, fire close event — disabled stays false.
    document.body.dispatchEvent(new CustomEvent('powerMap:newNameRowClosed'));
    expect(btn().disabled).toBe(false);
    // Disable, then fire close TWICE — second is a no-op.
    tbody().innerHTML = '<tr id="name-row-new"></tr>';
    table().dispatchEvent(new Event('htmx:afterSwap', { bubbles: true }));
    expect(btn().disabled).toBe(true);
    document.getElementById('name-row-new').remove();
    document.body.dispatchEvent(new CustomEvent('powerMap:newNameRowClosed'));
    document.body.dispatchEvent(new CustomEvent('powerMap:newNameRowClosed'));
    expect(btn().disabled).toBe(false);
  });
});
