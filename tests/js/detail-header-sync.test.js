/**
 * Tests for src/static/admin/org-detail.js and src/static/admin/person-detail.js
 *
 * Both scripts listen for a custom event (updateOrgHeader / updatePersonHeader)
 * and live-update three targets: #page-heading (h1), #breadcrumb-current (span),
 * and document.title.
 *
 * Regression guard for #153: previously the two files diverged on the missing-
 * payload case — org-detail.js early-returned, person-detail.js blanked the h1
 * and breadcrumb. These tests pin both to the early-return behavior.
 *
 * Pattern mirrors `tests/js/dark-mode.test.js`: build DOM fixture → eval the
 * script → dispatch events → assert state. Global listener cleanup follows
 * docs/STYLE.md §33.
 */
import { readFileSync } from 'node:fs';
import { resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';

const __dirname = dirname(fileURLToPath(import.meta.url));

const ORG_SCRIPT = readFileSync(
  resolve(__dirname, '../../src/static/admin/org-detail.js'),
  'utf-8',
);
const PERSON_SCRIPT = readFileSync(
  resolve(__dirname, '../../src/static/admin/person-detail.js'),
  'utf-8',
);

const INITIAL_HEADING = 'Initial Heading';
const INITIAL_BREADCRUMB = 'Initial Breadcrumb';
const INITIAL_TITLE = 'Initial Title';

function setupDom() {
  document.body.innerHTML = `
    <h1 id="page-heading">${INITIAL_HEADING}</h1>
    <span id="breadcrumb-current">${INITIAL_BREADCRUMB}</span>
  `;
  document.title = INITIAL_TITLE;
}

function h1Text() {
  return document.getElementById('page-heading').textContent;
}
function crumbText() {
  return document.getElementById('breadcrumb-current').textContent;
}

let addSpy;

beforeEach(() => {
  addSpy = vi.spyOn(document, 'addEventListener');
  setupDom();
});

afterEach(() => {
  for (const [type, fn] of addSpy.mock.calls) {
    document.removeEventListener(type, fn);
  }
  addSpy.mockRestore();
  document.body.innerHTML = '';
  document.title = '';
});

// ---------------------------------------------------------------------------
// Shared assertions — call from each describe block. `describe`/`it` titles
// must be string literals (vitest/valid-title), so we keep the wrappers
// explicit and the body shared.
// ---------------------------------------------------------------------------

function defineCommonCases({ script, event, happyTitle, happyDisplay }) {
  beforeEach(() => {
    eval(script);
  });

  it('updates h1, breadcrumb, and document.title on non-empty display', () => {
    document.dispatchEvent(new CustomEvent(event, { detail: { display: happyDisplay } }));
    expect(h1Text()).toBe(happyDisplay);
    expect(crumbText()).toBe(happyDisplay);
    expect(document.title).toBe(happyTitle);
  });

  it('leaves page intact when detail.display is an empty string', () => {
    document.dispatchEvent(new CustomEvent(event, { detail: { display: '' } }));
    expect(h1Text()).toBe(INITIAL_HEADING);
    expect(crumbText()).toBe(INITIAL_BREADCRUMB);
    expect(document.title).toBe(INITIAL_TITLE);
  });

  it('leaves page intact when detail.display is missing', () => {
    document.dispatchEvent(new CustomEvent(event, { detail: {} }));
    expect(h1Text()).toBe(INITIAL_HEADING);
    expect(crumbText()).toBe(INITIAL_BREADCRUMB);
    expect(document.title).toBe(INITIAL_TITLE);
  });

  it('leaves page intact when detail itself is missing', () => {
    document.dispatchEvent(new Event(event));
    expect(h1Text()).toBe(INITIAL_HEADING);
    expect(crumbText()).toBe(INITIAL_BREADCRUMB);
    expect(document.title).toBe(INITIAL_TITLE);
  });
}

describe('org-detail.js', () => {
  defineCommonCases({
    script: ORG_SCRIPT,
    event: 'updateOrgHeader',
    happyTitle: 'Acme Corp — Organization — Power Map',
    happyDisplay: 'Acme Corp',
  });
});

describe('person-detail.js', () => {
  defineCommonCases({
    script: PERSON_SCRIPT,
    event: 'updatePersonHeader',
    happyTitle: 'Jane Doe — Person — Power Map',
    happyDisplay: 'Jane Doe',
  });
});
