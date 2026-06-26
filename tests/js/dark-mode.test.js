/**
 * Tests for src/static/admin/dark-mode.js
 *
 * Three-state toggle (#25). The script is an IIFE that uses document-level
 * delegation to handle clicks on #theme-toggle and cycles the stored color
 * scheme through light → system → dark → light. The cycle is driven off the
 * stored localStorage preference (not the rendered html class), because
 * `system` and explicit `light` both render as light and are otherwise
 * indistinguishable by class.
 *
 *   stored 'light'  → html.light, force light
 *   stored 'dark'   → html.dark,  force dark
 *   stored absent   → no class — OS prefers-color-scheme governs (system)
 *
 * It also re-syncs the button (icon + aria-label) after every htmx:afterSettle
 * so hx-boost body swaps don't leave the button in a stale state.
 *
 * Pattern: build DOM fixture → eval() the IIFE → simulate events → assert state.
 *
 * Listener cleanup: the script registers document-level 'click' and
 * 'htmx:afterSettle' listeners. A global beforeEach/afterEach pair spies on
 * document.addEventListener, captures every handler registered during the test,
 * and removes them all in afterEach — preventing cross-test listener accumulation.
 */
import { readFileSync } from 'node:fs';
import { resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';

const __dirname = dirname(fileURLToPath(import.meta.url));
const scriptCode = readFileSync(resolve(__dirname, '../../src/static/admin/dark-mode.js'), 'utf-8');

const KEY = 'pm-color-scheme';

// Affordance glyphs / labels — current-state convention (icon shows the active
// state, label names the state and the next action in the cycle).
const ICON = { light: '☀', system: '◑', dark: '☽' };
const LABEL = {
  light: 'Color theme: Light. Activate for System.',
  system: 'Color theme: System. Activate for Dark.',
  dark: 'Color theme: Dark. Activate for Light.',
};

// ---------------------------------------------------------------------------
// Global listener cleanup — see docs/STYLE.md §33
// ---------------------------------------------------------------------------

let addSpy;

beforeEach(() => {
  addSpy = vi.spyOn(document, 'addEventListener');
  document.documentElement.classList.remove('dark', 'light');
  localStorage.clear();
});

afterEach(() => {
  for (const [type, fn] of addSpy.mock.calls) {
    document.removeEventListener(type, fn);
  }
  addSpy.mockRestore();
  document.body.innerHTML = '';
  document.documentElement.classList.remove('dark', 'light');
  localStorage.clear();
});

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function buildButton() {
  return `<button id="theme-toggle" aria-label="Color theme: System. Activate for Dark." type="button">
    <span data-theme-icon aria-hidden="true">◑</span>
  </button>`;
}

// `stored` controls the localStorage preference the cycle reads. Omit (or pass
// undefined) for the absent/system default.
function setup({ stored } = {}) {
  if (stored === undefined) localStorage.removeItem(KEY);
  else localStorage.setItem(KEY, stored);
  document.body.innerHTML = buildButton();
  eval(scriptCode); // no-eval disabled for test files in eslint.config.js
}

function btn() {
  return document.getElementById('theme-toggle');
}

function click() {
  btn().dispatchEvent(new MouseEvent('click', { bubbles: true }));
}

function iconText() {
  return btn().querySelector('[data-theme-icon]').textContent;
}

function hasDark() {
  return document.documentElement.classList.contains('dark');
}

function hasLight() {
  return document.documentElement.classList.contains('light');
}

// ---------------------------------------------------------------------------
// Three-state cycle (light → system → dark → light)
// ---------------------------------------------------------------------------

describe('three-state cycle', () => {
  it('system → dark on first click (default / new user)', () => {
    setup(); // no stored value → system
    click();
    expect(localStorage.getItem(KEY)).toBe('dark');
    expect(hasDark()).toBe(true);
    expect(hasLight()).toBe(false);
  });

  it('dark → light', () => {
    setup({ stored: 'dark' });
    click();
    expect(localStorage.getItem(KEY)).toBe('light');
    expect(hasLight()).toBe(true);
    expect(hasDark()).toBe(false);
  });

  it('light → system clears the key and removes both classes', () => {
    setup({ stored: 'light' });
    click();
    expect(localStorage.getItem(KEY)).toBeNull();
    expect(hasDark()).toBe(false);
    expect(hasLight()).toBe(false);
  });

  it('cycles the full ring system → dark → light → system', () => {
    setup();
    click();
    expect(localStorage.getItem(KEY)).toBe('dark');
    click();
    expect(localStorage.getItem(KEY)).toBe('light');
    click();
    expect(localStorage.getItem(KEY)).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// Legacy / unknown stored values — treated as system
// ---------------------------------------------------------------------------

describe('unknown stored value', () => {
  it('treats an unrecognized value as system', () => {
    setup({ stored: 'garbage' });
    expect(btn().getAttribute('aria-label')).toBe(LABEL.system);
    expect(iconText()).toBe(ICON.system);
    click(); // system → dark
    expect(localStorage.getItem(KEY)).toBe('dark');
  });
});

// ---------------------------------------------------------------------------
// Button sync — icon + aria-label reflect the stored state
// ---------------------------------------------------------------------------

describe('button affordance', () => {
  it('shows the system affordance when no preference is stored', () => {
    setup();
    expect(btn().getAttribute('aria-label')).toBe(LABEL.system);
    expect(iconText()).toBe(ICON.system);
  });

  it('shows the dark affordance when stored dark', () => {
    setup({ stored: 'dark' });
    expect(btn().getAttribute('aria-label')).toBe(LABEL.dark);
    expect(iconText()).toBe(ICON.dark);
  });

  it('shows the light affordance when stored light', () => {
    setup({ stored: 'light' });
    expect(btn().getAttribute('aria-label')).toBe(LABEL.light);
    expect(iconText()).toBe(ICON.light);
  });

  it('updates the affordance after a click', () => {
    setup({ stored: 'dark' });
    click(); // → light
    expect(btn().getAttribute('aria-label')).toBe(LABEL.light);
    expect(iconText()).toBe(ICON.light);
  });
});

// ---------------------------------------------------------------------------
// HTMX boost survival — the original bug from #19
//
// When HTMX boost navigates, it swaps document.body.innerHTML, destroying the
// original #theme-toggle element and inserting a fresh one. The script must
// survive this swap (via document-level event delegation) so the new button
// still works.
// ---------------------------------------------------------------------------

describe('survives HTMX boost body swap', () => {
  it('cycles after body innerHTML is replaced (simulated hx-boost)', () => {
    setup();
    document.body.innerHTML = buildButton();
    click();
    expect(localStorage.getItem(KEY)).toBe('dark');
    expect(hasDark()).toBe(true);
  });

  it('cycles multiple times after body swap', () => {
    setup({ stored: 'dark' });
    document.body.innerHTML = buildButton();
    click();
    expect(localStorage.getItem(KEY)).toBe('light');
    click();
    expect(localStorage.getItem(KEY)).toBeNull();
  });

  it('syncs button affordance via htmx:afterSettle', () => {
    setup({ stored: 'dark' });
    document.body.innerHTML = buildButton(); // new button has stale defaults
    document.dispatchEvent(new Event('htmx:afterSettle'));
    expect(btn().getAttribute('aria-label')).toBe(LABEL.dark);
    expect(iconText()).toBe(ICON.dark);
  });
});
