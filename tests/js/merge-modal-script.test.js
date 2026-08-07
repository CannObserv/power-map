/**
 * Tests for the shared merge-preview modal portal script
 * (src/templates/admin/partials/_merge_modal_script.html, #255).
 *
 * The script ships inline in the Orgs/People/Roles modal templates so htmx
 * re-runs it on every portal swap. This suite reads the partial, extracts the
 * inline <script> body (no Jinja in it — it's pure JS), and exercises the
 * portal lifecycle (window.__pmMergeClose, Escape-to-close, and
 * close-on-successful-submit) in jsdom.
 */
import { readFileSync } from 'node:fs';
import { resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';

const __dirname = dirname(fileURLToPath(import.meta.url));
const template = readFileSync(
  resolve(__dirname, '../../src/templates/admin/partials/_merge_modal_script.html'),
  'utf-8',
);
const scriptCode = template.slice(
  template.indexOf('<script>') + '<script>'.length,
  template.indexOf('</script>'),
);

// Global listener cleanup — see docs/TESTING.md § Vitest test conventions
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
  delete window.__pmMergeClose;
  delete document.__pmMergeKey;
  delete document.__pmMergeSavedFocus;
});

function mountModal() {
  document.body.innerHTML = `
    <div id="merge-modal-portal">
      <div class="modal">
        <form id="merge-form">
          <button type="button" id="cancel">Cancel</button>
          <button type="submit" id="exec">Execute merge</button>
        </form>
      </div>
    </div>`;
  // The partial's inline script is a self-invoking IIFE; eval runs it, matching
  // the src/static test precedent (person-name-deadname-confirm.test.js).
  eval(scriptCode);
}

describe('merge modal portal script', () => {
  it('exposes window.__pmMergeClose which empties the portal', () => {
    mountModal();
    expect(document.getElementById('merge-form')).not.toBeNull();
    window.__pmMergeClose();
    expect(document.getElementById('merge-modal-portal').innerHTML).toBe('');
  });

  it('closes the portal on Escape', () => {
    mountModal();
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }));
    expect(document.getElementById('merge-modal-portal').innerHTML).toBe('');
  });

  it('closes the portal when the merge POST succeeds', () => {
    mountModal();
    document
      .getElementById('merge-form')
      .dispatchEvent(
        new CustomEvent('htmx:afterRequest', { detail: { successful: true }, bubbles: true }),
      );
    expect(document.getElementById('merge-modal-portal').innerHTML).toBe('');
  });

  it('leaves the portal open when the merge POST fails', () => {
    mountModal();
    document
      .getElementById('merge-form')
      .dispatchEvent(
        new CustomEvent('htmx:afterRequest', { detail: { successful: false }, bubbles: true }),
      );
    expect(document.getElementById('merge-form')).not.toBeNull();
  });
});
