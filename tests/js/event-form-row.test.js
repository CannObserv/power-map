/**
 * Tests for src/static/admin/event-form-row.js
 *
 * The script discovers event form rows via `[data-event-form-row]`, reads
 * `data-uid`, and wires the Linked Entity section show/hide, the linked-entity
 * typeahead, and the linked_entity_type scope-switch behavior (#172).
 *
 * Pattern mirrors tests/js/person-name-row-typeahead.test.js: build DOM
 * fixture → stub `window.initTypeaheadCombobox` → eval the script → dispatch
 * synthetic events → assert the wiring contract.
 */
import { readFileSync } from 'node:fs';
import { resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';

const __dirname = dirname(fileURLToPath(import.meta.url));
const scriptCode = readFileSync(
  resolve(__dirname, '../../src/static/admin/event-form-row.js'),
  'utf-8',
);

/**
 * Build a form fixture mirroring `_event_form_row.html`: a
 * <form data-event-form-row data-uid="<uid>"> containing the event_type
 * select (with a data-requires-linked option), the linked_entity_type select,
 * and the namespaced typeahead search input + hidden id + listbox.
 *
 * @param {object} opts
 * @param {string} opts.uid          row namespace
 * @param {boolean} opts.requiresLinked  whether the selected event type requires a link
 * @param {string} opts.linkedType   initial linked_entity_type value
 * @param {string} opts.searchValue  initial visible search value
 * @param {string} opts.hiddenValue  initial hidden linked_entity_id value
 */
function buildForm({
  uid,
  requiresLinked = false,
  linkedType = '',
  searchValue = '',
  hiddenValue = '',
}) {
  document.body.innerHTML = `
    <table><tbody>
      <tr id="event-row-${uid}">
        <td>
          <form data-event-form-row data-uid="${uid}">
            <select name="event_type_id">
              <option value="plain" data-requires-linked="false" ${
                requiresLinked ? '' : 'selected'
              }>Plain</option>
              <option value="linked" data-requires-linked="true" ${
                requiresLinked ? 'selected' : ''
              }>Linked</option>
            </select>
            <div data-linked-entity-section>
              <select name="linked_entity_type" data-linked-type>
                <option value="" ${linkedType === '' ? 'selected' : ''}>— none —</option>
                <option value="person" ${linkedType === 'person' ? 'selected' : ''}>Person</option>
                <option value="organization" ${
                  linkedType === 'organization' ? 'selected' : ''
                }>Organization</option>
              </select>
              <input id="linked-entity-search-${uid}" value="${searchValue}">
              <input type="hidden" id="linked-entity-id-${uid}" value="${hiddenValue}">
              <ul id="linked-entity-results-${uid}"></ul>
            </div>
          </form>
        </td>
      </tr>
    </tbody></table>
  `;
  return document.querySelector(`[data-uid="${uid}"]`);
}

let initStub;
let addSpy;

beforeEach(() => {
  // Mimic the real factory's return handle: clear() empties the input + hidden
  // fields it was wired to, so the scope-switch path (combo.clear()) is exercised.
  initStub = vi.fn((opts) => ({
    clear: () => {
      const i = opts && document.getElementById(opts.inputId);
      const h = opts && document.getElementById(opts.hiddenId);
      if (i) i.value = '';
      if (h) h.value = '';
    },
  }));
  window.initTypeaheadCombobox = initStub;
  addSpy = vi.spyOn(document, 'addEventListener');
});

afterEach(() => {
  for (const [type, fn] of addSpy.mock.calls) {
    document.removeEventListener(type, fn);
  }
  addSpy.mockRestore();
  document.body.innerHTML = '';
  delete window.initTypeaheadCombobox;
});

describe('event-form-row', () => {
  it('wires the linked-entity typeahead with namespaced ids on htmx:afterSwap', () => {
    buildForm({ uid: 'ev_abc', requiresLinked: true, linkedType: 'person' });
    eval(scriptCode);
    document.dispatchEvent(new Event('htmx:afterSwap'));
    expect(initStub).toHaveBeenCalledTimes(1);
    expect(initStub.mock.calls[0][0]).toEqual({
      inputId: 'linked-entity-search-ev_abc',
      listboxId: 'linked-entity-results-ev_abc',
      hiddenId: 'linked-entity-id-ev_abc',
      clearButtonId: 'linked-entity-clear-ev_abc',
    });
  });

  it('also wires forms present at DOMContentLoaded (initial render)', () => {
    buildForm({ uid: 'ev_initial', requiresLinked: true, linkedType: 'person' });
    eval(scriptCode);
    document.dispatchEvent(new Event('DOMContentLoaded'));
    expect(initStub).toHaveBeenCalledTimes(1);
  });

  it('hides the linked-entity section when the event type does not require a link', () => {
    buildForm({ uid: 'ev_plain', requiresLinked: false });
    eval(scriptCode);
    document.dispatchEvent(new Event('htmx:afterSwap'));
    const section = document.querySelector('[data-uid="ev_plain"] [data-linked-entity-section]');
    expect(section.style.display).toBe('none');
  });

  it('shows the section when the event type requires a link', () => {
    buildForm({ uid: 'ev_req', requiresLinked: true, linkedType: 'person' });
    eval(scriptCode);
    document.dispatchEvent(new Event('htmx:afterSwap'));
    const section = document.querySelector('[data-uid="ev_req"] [data-linked-entity-section]');
    expect(section.style.display).toBe('');
  });

  it('disables the search input until a scope (linked_entity_type) is chosen', () => {
    buildForm({ uid: 'ev_notype', requiresLinked: true, linkedType: '' });
    eval(scriptCode);
    document.dispatchEvent(new Event('htmx:afterSwap'));
    expect(document.getElementById('linked-entity-search-ev_notype').disabled).toBe(true);
  });

  it('enables the search input when a scope is already selected (edit prefill)', () => {
    buildForm({
      uid: 'ev_edit',
      requiresLinked: true,
      linkedType: 'organization',
      searchValue: 'Acme Corp',
      hiddenValue: 'org_123',
    });
    eval(scriptCode);
    document.dispatchEvent(new Event('htmx:afterSwap'));
    const search = document.getElementById('linked-entity-search-ev_edit');
    // Prefilled value preserved; input usable.
    expect(search.disabled).toBe(false);
    expect(search.value).toBe('Acme Corp');
    expect(document.getElementById('linked-entity-id-ev_edit').value).toBe('org_123');
  });

  it('clears prior selection and re-disables when scope is changed back to none', () => {
    buildForm({
      uid: 'ev_switch',
      requiresLinked: true,
      linkedType: 'organization',
      searchValue: 'Acme Corp',
      hiddenValue: 'org_123',
    });
    eval(scriptCode);
    document.dispatchEvent(new Event('htmx:afterSwap'));
    const typeSel = document.querySelector('[data-uid="ev_switch"] [data-linked-type]');
    typeSel.value = '';
    typeSel.dispatchEvent(new Event('change'));
    expect(document.getElementById('linked-entity-search-ev_switch').value).toBe('');
    expect(document.getElementById('linked-entity-id-ev_switch').value).toBe('');
    expect(document.getElementById('linked-entity-search-ev_switch').disabled).toBe(true);
  });

  it('clears prior selection when switching scope person → organization', () => {
    buildForm({
      uid: 'ev_p2o',
      requiresLinked: true,
      linkedType: 'person',
      searchValue: 'Jane Doe',
      hiddenValue: 'per_999',
    });
    eval(scriptCode);
    document.dispatchEvent(new Event('htmx:afterSwap'));
    const typeSel = document.querySelector('[data-uid="ev_p2o"] [data-linked-type]');
    typeSel.value = 'organization';
    typeSel.dispatchEvent(new Event('change'));
    // Stale person selection must not survive a scope switch.
    expect(document.getElementById('linked-entity-search-ev_p2o').value).toBe('');
    expect(document.getElementById('linked-entity-id-ev_p2o').value).toBe('');
    expect(document.getElementById('linked-entity-search-ev_p2o').disabled).toBe(false);
  });

  it('is row-scoped — switching one form does not clear a sibling form', () => {
    document.body.innerHTML = `
      <table><tbody>
        <tr><td>
          <form data-event-form-row data-uid="A">
            <select name="event_type_id"><option value="linked" data-requires-linked="true" selected>Linked</option></select>
            <div data-linked-entity-section>
              <select name="linked_entity_type" data-linked-type><option value="person" selected>Person</option><option value="organization">Organization</option></select>
              <input id="linked-entity-search-A" value="Jane Doe">
              <input type="hidden" id="linked-entity-id-A" value="per_A">
              <ul id="linked-entity-results-A"></ul>
            </div>
          </form>
        </td></tr>
        <tr><td>
          <form data-event-form-row data-uid="B">
            <select name="event_type_id"><option value="linked" data-requires-linked="true" selected>Linked</option></select>
            <div data-linked-entity-section>
              <select name="linked_entity_type" data-linked-type><option value="person" selected>Person</option><option value="organization">Organization</option></select>
              <input id="linked-entity-search-B" value="John Roe">
              <input type="hidden" id="linked-entity-id-B" value="per_B">
              <ul id="linked-entity-results-B"></ul>
            </div>
          </form>
        </td></tr>
      </tbody></table>
    `;
    eval(scriptCode);
    document.dispatchEvent(new Event('htmx:afterSwap'));
    const typeSelA = document.querySelector('[data-uid="A"] [data-linked-type]');
    typeSelA.value = 'organization';
    typeSelA.dispatchEvent(new Event('change'));
    // Row A cleared; row B untouched.
    expect(document.getElementById('linked-entity-search-A').value).toBe('');
    expect(document.getElementById('linked-entity-id-A').value).toBe('');
    expect(document.getElementById('linked-entity-search-B').value).toBe('John Roe');
    expect(document.getElementById('linked-entity-id-B').value).toBe('per_B');
  });

  it('skips forms missing data-uid', () => {
    document.body.innerHTML = `<form data-event-form-row><div data-linked-entity-section></div></form>`;
    eval(scriptCode);
    document.dispatchEvent(new Event('htmx:afterSwap'));
    expect(initStub).not.toHaveBeenCalled();
  });

  it('is idempotent — re-running on the same form is a no-op', () => {
    buildForm({ uid: 'ev_dup', requiresLinked: true, linkedType: 'person' });
    eval(scriptCode);
    document.dispatchEvent(new Event('htmx:afterSwap'));
    expect(initStub).toHaveBeenCalledTimes(1);
    document.dispatchEvent(new Event('htmx:afterSwap'));
    expect(initStub).toHaveBeenCalledTimes(1);
  });

  it('does NOT mark the form inited if initTypeaheadCombobox is unavailable', () => {
    delete window.initTypeaheadCombobox;
    buildForm({ uid: 'ev_retry', requiresLinked: true, linkedType: 'person' });
    eval(scriptCode);
    document.dispatchEvent(new Event('htmx:afterSwap'));
    const form = document.querySelector('[data-uid="ev_retry"]');
    expect(form.dataset.eventRowInited).toBeUndefined();
    const lateStub = vi.fn();
    window.initTypeaheadCombobox = lateStub;
    document.dispatchEvent(new Event('htmx:afterSwap'));
    expect(lateStub).toHaveBeenCalledTimes(1);
    expect(form.dataset.eventRowInited).toBe('1');
  });
});
