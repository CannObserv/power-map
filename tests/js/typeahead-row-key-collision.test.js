/**
 * Tests for issue #125 — row-key suffixed DOM ids on multi-instance admin
 * form rows must isolate each form's typeahead binding.
 *
 * Regression scenario: before #125, the inline form rows under
 * `_assignment_form_row.html` / `_child_form_row.html` rendered a single
 * fixed id stem (e.g. `role-search-display`, `child-search-display`).
 * If two such forms ever co-existed in the DOM (e.g. an open `+ Add`
 * row alongside an open edit drawer that reused the same stem), the
 * second `initTypeaheadCombobox(...)` call would resolve every
 * `getElementById` to form #1's elements — so typing into form #2
 * silently mutated form #1's hidden field and form #2's listbox never
 * rendered.
 *
 * The fix is mechanical: every id gets a `-<row_key>` suffix, and the
 * inline `<script>` passes the suffixed ids through to the factory.
 *
 * This test reproduces the contract that the partial-template scripts
 * rely on: build two forms in the same DOM with distinct row-keys,
 * wire each with its own `initTypeaheadCombobox(...)` call, and verify
 * a selection in form B never touches form A's hidden field. It uses
 * the real `typeahead-combobox.js` factory (no stubs) so a future
 * regression in the factory's `getElementById` lookup is also caught.
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
 * Build one form-row fixture mirroring `_child_form_row.html` post-#125:
 * a typeahead input + hidden field + listbox, all id-suffixed by `key`.
 */
function buildRow(key) {
  return `
    <div data-row="${key}">
      <input id="search-display-${key}" type="text" autocomplete="off"
             role="combobox" aria-expanded="false"
             aria-haspopup="listbox" aria-controls="search-results-${key}"
             aria-autocomplete="list">
      <input type="hidden" id="hidden-${key}" name="picked_id" value="">
      <ul id="search-results-${key}" class="typeahead-results" role="listbox"
          style="display:none"></ul>
    </div>
  `;
}

let addSpy;

beforeEach(() => {
  // Cleanup pattern from `tests/js/person-name-row-typeahead.test.js`:
  // record every document-level listener attached during the test and
  // unwire each in afterEach so cross-test state doesn't leak. The
  // typeahead-combobox factory attaches click + scroll listeners on
  // openDropdown(); calling closeDropdown() (via Escape) is the
  // intended teardown path, but the spy is defense-in-depth.
  addSpy = vi.spyOn(document, 'addEventListener');
});

afterEach(() => {
  // Drop any listeners the spy recorded.
  for (const [type, fn] of addSpy.mock.calls) {
    document.removeEventListener(type, fn);
  }
  addSpy.mockRestore();
  document.body.innerHTML = '';
  delete window.initTypeaheadCombobox;
});

describe('typeahead row-key collision (#125)', () => {
  it('two forms with distinct row-keys bind independently', () => {
    document.body.innerHTML = buildRow('A') + buildRow('new');
    eval(factoryCode);

    // Wire each form, mirroring what the partial templates emit.
    window.initTypeaheadCombobox({
      inputId: 'search-display-A',
      listboxId: 'search-results-A',
      hiddenId: 'hidden-A',
    });
    window.initTypeaheadCombobox({
      inputId: 'search-display-new',
      listboxId: 'search-results-new',
      hiddenId: 'hidden-new',
    });

    // Populate form B's listbox and click an option. Use mousedown +
    // preventDefault — that's the path the factory installs (mousedown
    // captures selection before the browser blurs the input).
    const ulB = document.getElementById('search-results-new');
    ulB.innerHTML = '<li data-id="org_zzz" data-label="Zeta Org">Zeta Org</li>';

    const liB = ulB.querySelector('li[data-id="org_zzz"]');
    const ev = new window.MouseEvent('mousedown', { bubbles: true, cancelable: true });
    liB.dispatchEvent(ev);

    // Form B's hidden field captured the selection.
    expect(document.getElementById('hidden-new').value).toBe('org_zzz');
    // Form A's hidden field was NOT touched — the regression this test guards.
    expect(document.getElementById('hidden-A').value).toBe('');
    // Form B's display input shows the picked label; Form A's input is empty.
    expect(document.getElementById('search-display-new').value).toBe('Zeta Org');
    expect(document.getElementById('search-display-A').value).toBe('');
  });

  it('a selection in form A also leaves form B untouched (symmetry check)', () => {
    document.body.innerHTML = buildRow('A') + buildRow('new');
    eval(factoryCode);

    window.initTypeaheadCombobox({
      inputId: 'search-display-A',
      listboxId: 'search-results-A',
      hiddenId: 'hidden-A',
    });
    window.initTypeaheadCombobox({
      inputId: 'search-display-new',
      listboxId: 'search-results-new',
      hiddenId: 'hidden-new',
    });

    const ulA = document.getElementById('search-results-A');
    ulA.innerHTML = '<li data-id="org_aaa" data-label="Alpha Org">Alpha Org</li>';
    const liA = ulA.querySelector('li[data-id="org_aaa"]');
    liA.dispatchEvent(new window.MouseEvent('mousedown', { bubbles: true, cancelable: true }));

    expect(document.getElementById('hidden-A').value).toBe('org_aaa');
    expect(document.getElementById('hidden-new').value).toBe('');
    expect(document.getElementById('search-display-A').value).toBe('Alpha Org');
    expect(document.getElementById('search-display-new').value).toBe('');
  });
});
