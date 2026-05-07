/**
 * Tests for src/static/admin/person-name-row-typeahead.js
 *
 * The script discovers form rows via `[data-name-row-typeahead]`,
 * reads `data-uid`, and wires the locale / script / reading-of
 * typeaheads + the reading-of-block visibility toggle for each row.
 *
 * Pattern mirrors `tests/js/typeahead-combobox.test.js`: build DOM
 * fixture → stub `window.initTypeaheadCombobox` → eval the script →
 * dispatch synthetic events → assert the wiring contract.
 */
import { readFileSync } from 'node:fs';
import { resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';

const __dirname = dirname(fileURLToPath(import.meta.url));
const scriptCode = readFileSync(
  resolve(__dirname, '../../src/static/admin/person-name-row-typeahead.js'),
  'utf-8',
);

/**
 * Build a row fixture mirroring what `_name_form_row.html` renders:
 * a <tr data-name-row-typeahead data-uid="<uid>"> containing a
 * select[name="name_type"] and the namespaced reading-of block. The
 * locale/script/reading-of inputs + listboxes + hidden fields are also
 * present so initTypeaheadCombobox can find them.
 */
function buildRow(uid, nameType) {
  document.body.innerHTML = `
    <table id="names-table"><tbody>
      <tr id="name-row-${uid}" data-name-row-typeahead data-uid="${uid}">
        <td>
          <select name="name_type">
            <option value="legal" ${nameType === 'legal' ? 'selected' : ''}>legal</option>
            <option value="reading" ${nameType === 'reading' ? 'selected' : ''}>reading</option>
            <option value="romanization" ${
              nameType === 'romanization' ? 'selected' : ''
            }>romanization</option>
            <option value="mrz" ${nameType === 'mrz' ? 'selected' : ''}>mrz</option>
          </select>
          <input id="locale-search-display-${uid}">
          <input type="hidden" id="locale-hidden-${uid}">
          <ul id="locale-search-results-${uid}"></ul>
          <input id="script-search-display-${uid}">
          <input type="hidden" id="script-hidden-${uid}">
          <ul id="script-search-results-${uid}"></ul>
          <div id="reading-of-block-${uid}" style="display:${
            ['reading', 'romanization', 'mrz'].includes(nameType) ? '' : 'none'
          }">
            <input id="reading-of-display-${uid}">
            <input type="hidden" id="reading-of-hidden-${uid}">
            <ul id="reading-of-results-${uid}"></ul>
          </div>
        </td>
      </tr>
    </tbody></table>
  `;
  return document.querySelector(`[data-uid="${uid}"]`);
}

let initStub;

beforeEach(() => {
  initStub = vi.fn();
  window.initTypeaheadCombobox = initStub;
});

afterEach(() => {
  document.body.innerHTML = '';
  delete window.initTypeaheadCombobox;
});

describe('person-name-row-typeahead', () => {
  it('wires locale/script/reading-of typeaheads with namespaced ids on htmx:afterSwap', () => {
    buildRow('nid_abc', 'legal');
    eval(scriptCode);
    document.dispatchEvent(new Event('htmx:afterSwap'));
    // Three calls to initTypeaheadCombobox, one per typeahead.
    expect(initStub).toHaveBeenCalledTimes(3);
    const callArgs = initStub.mock.calls.map((c) => c[0]);
    expect(callArgs).toContainEqual({
      inputId: 'locale-search-display-nid_abc',
      listboxId: 'locale-search-results-nid_abc',
      hiddenId: 'locale-hidden-nid_abc',
    });
    expect(callArgs).toContainEqual({
      inputId: 'script-search-display-nid_abc',
      listboxId: 'script-search-results-nid_abc',
      hiddenId: 'script-hidden-nid_abc',
    });
    expect(callArgs).toContainEqual({
      inputId: 'reading-of-display-nid_abc',
      listboxId: 'reading-of-results-nid_abc',
      hiddenId: 'reading-of-hidden-nid_abc',
    });
  });

  it('also wires rows present at DOMContentLoaded (initial render)', () => {
    buildRow('nid_initial', 'legal');
    eval(scriptCode);
    document.dispatchEvent(new Event('DOMContentLoaded'));
    expect(initStub).toHaveBeenCalledTimes(3);
  });

  it('skips rows missing data-uid', () => {
    document.body.innerHTML = `
      <table id="names-table"><tbody>
        <tr id="name-row-new" data-name-row-typeahead><td></td></tr>
      </tbody></table>
    `;
    eval(scriptCode);
    document.dispatchEvent(new Event('htmx:afterSwap'));
    expect(initStub).not.toHaveBeenCalled();
  });

  it('is idempotent — re-running on the same row is a no-op', () => {
    buildRow('nid_dup', 'legal');
    eval(scriptCode);
    document.dispatchEvent(new Event('htmx:afterSwap'));
    expect(initStub).toHaveBeenCalledTimes(3);
    // Second event for the same row in the DOM: should not re-init.
    document.dispatchEvent(new Event('htmx:afterSwap'));
    expect(initStub).toHaveBeenCalledTimes(3);
  });

  it('reading-of block is hidden when name_type is non-reading', () => {
    buildRow('nid_legal', 'legal');
    eval(scriptCode);
    document.dispatchEvent(new Event('htmx:afterSwap'));
    expect(document.getElementById('reading-of-block-nid_legal').style.display).toBe('none');
  });

  it('reading-of block becomes visible when name_type changes to reading', () => {
    buildRow('nid_legal', 'legal');
    eval(scriptCode);
    document.dispatchEvent(new Event('htmx:afterSwap'));
    const sel = document.querySelector('[data-uid="nid_legal"] select[name="name_type"]');
    sel.value = 'reading';
    sel.dispatchEvent(new Event('change'));
    expect(document.getElementById('reading-of-block-nid_legal').style.display).toBe('');
  });

  it('reading-of block is visible on initial render when name_type is reading', () => {
    buildRow('nid_reading', 'reading');
    eval(scriptCode);
    document.dispatchEvent(new Event('htmx:afterSwap'));
    expect(document.getElementById('reading-of-block-nid_reading').style.display).toBe('');
  });

  it("reading-of toggle is row-scoped — sibling row's select does not affect this row's block", () => {
    // Two rows on the same page; toggling row A's select must not affect row B's block.
    document.body.innerHTML = `
      <table id="names-table"><tbody>
        <tr data-name-row-typeahead data-uid="A">
          <td>
            <select name="name_type"><option value="legal" selected>legal</option><option value="reading">reading</option></select>
            <div id="reading-of-block-A" style="display:none"></div>
            <input id="locale-search-display-A"><input type="hidden" id="locale-hidden-A"><ul id="locale-search-results-A"></ul>
            <input id="script-search-display-A"><input type="hidden" id="script-hidden-A"><ul id="script-search-results-A"></ul>
            <input id="reading-of-display-A"><input type="hidden" id="reading-of-hidden-A"><ul id="reading-of-results-A"></ul>
          </td>
        </tr>
        <tr data-name-row-typeahead data-uid="B">
          <td>
            <select name="name_type"><option value="legal" selected>legal</option><option value="reading">reading</option></select>
            <div id="reading-of-block-B" style="display:none"></div>
            <input id="locale-search-display-B"><input type="hidden" id="locale-hidden-B"><ul id="locale-search-results-B"></ul>
            <input id="script-search-display-B"><input type="hidden" id="script-hidden-B"><ul id="script-search-results-B"></ul>
            <input id="reading-of-display-B"><input type="hidden" id="reading-of-hidden-B"><ul id="reading-of-results-B"></ul>
          </td>
        </tr>
      </tbody></table>
    `;
    eval(scriptCode);
    document.dispatchEvent(new Event('htmx:afterSwap'));
    // Toggle row A's select to reading.
    const selA = document.querySelector('[data-uid="A"] select[name="name_type"]');
    selA.value = 'reading';
    selA.dispatchEvent(new Event('change'));
    expect(document.getElementById('reading-of-block-A').style.display).toBe('');
    expect(document.getElementById('reading-of-block-B').style.display).toBe('none');
  });
});
