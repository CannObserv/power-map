/**
 * Tests for src/static/admin/dark-mode.js
 *
 * The script is an IIFE that uses document-level delegation to handle clicks on
 * #theme-toggle and syncs aria-label / icon with the current html.dark class state.
 * It also re-syncs the button after every htmx:afterSettle so hx-boost body swaps
 * don't leave the button in a stale label/icon state.
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

// ---------------------------------------------------------------------------
// Global listener cleanup — see docs/STYLE.md §33
// ---------------------------------------------------------------------------

let addSpy;

beforeEach(() => {
  addSpy = vi.spyOn(document, 'addEventListener');
  // Reset html classes and localStorage
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
  return `<button id="theme-toggle" aria-label="Switch to dark mode" type="button">
    <span data-theme-icon aria-hidden="true">☽</span>
  </button>`;
}

function setup({ dark = false } = {}) {
  if (dark) document.documentElement.classList.add('dark');
  document.body.innerHTML = buildButton();
  eval(scriptCode); // no-eval disabled for test files in eslint.config.js
}

function btn() {
  return document.getElementById('theme-toggle');
}

function click() {
  btn().dispatchEvent(new MouseEvent('click', { bubbles: true }));
}

// ---------------------------------------------------------------------------
// Basic toggle tests (initial page load — button already present)
// ---------------------------------------------------------------------------

describe('initial page load', () => {
  it('toggles from light to dark on click', () => {
    setup({ dark: false });
    click();
    expect(document.documentElement.classList.contains('dark')).toBe(true);
  });

  it('toggles from dark to light on click', () => {
    setup({ dark: true });
    click();
    expect(document.documentElement.classList.contains('dark')).toBe(false);
  });

  it('persists preference to localStorage', () => {
    setup({ dark: false });
    click();
    expect(localStorage.getItem('pm-color-scheme')).toBe('dark');
    click();
    expect(localStorage.getItem('pm-color-scheme')).toBe('light');
  });

  it('updates aria-label after toggle', () => {
    setup({ dark: false });
    click();
    expect(btn().getAttribute('aria-label')).toBe('Switch to light mode');
    click();
    expect(btn().getAttribute('aria-label')).toBe('Switch to dark mode');
  });

  it('syncs button label and icon on load when dark mode is already active', () => {
    setup({ dark: true });
    expect(btn().getAttribute('aria-label')).toBe('Switch to light mode');
    expect(btn().querySelector('[data-theme-icon]').textContent).toBe('\u2600');
  });
});

// ---------------------------------------------------------------------------
// HTMX boost survival — the bug
//
// When HTMX boost navigates, it swaps document.body.innerHTML, destroying the
// original #theme-toggle element and inserting a fresh one. The script must
// survive this swap (via document-level event delegation) so the new button
// still works.
// ---------------------------------------------------------------------------

describe('survives HTMX boost body swap', () => {
  it('toggles after body innerHTML is replaced (simulated hx-boost)', () => {
    setup({ dark: false });

    // Simulate HTMX boost: replace entire body (destroys old btn + its listeners)
    document.body.innerHTML = buildButton();

    // New button should still be responsive
    click();
    expect(document.documentElement.classList.contains('dark')).toBe(true);
  });

  it('toggles multiple times after body swap', () => {
    setup({ dark: false });
    document.body.innerHTML = buildButton();

    click();
    expect(document.documentElement.classList.contains('dark')).toBe(true);
    click();
    expect(document.documentElement.classList.contains('dark')).toBe(false);
  });

  it('persists to localStorage after body swap', () => {
    setup({ dark: false });
    document.body.innerHTML = buildButton();

    click();
    expect(localStorage.getItem('pm-color-scheme')).toBe('dark');
  });

  it('updates aria-label on new button after body swap', () => {
    setup({ dark: false });
    document.body.innerHTML = buildButton();

    click();
    expect(btn().getAttribute('aria-label')).toBe('Switch to light mode');
  });

  it('syncs button label and icon via htmx:afterSettle when dark mode is active', () => {
    setup({ dark: false });
    click(); // go dark
    document.body.innerHTML = buildButton(); // simulate boost swap — new button has stale defaults
    document.dispatchEvent(new Event('htmx:afterSettle'));
    expect(btn().getAttribute('aria-label')).toBe('Switch to light mode');
    expect(btn().querySelector('[data-theme-icon]').textContent).toBe('\u2600');
  });
});
