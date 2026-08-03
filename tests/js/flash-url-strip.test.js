/**
 * Tests for the ?flash= URL-strip IIFE in src/static/admin/flash.js (#379).
 *
 * A full-page navigation that lands on a flash-bearing URL (HX-Redirect
 * delete→list from #376, or a non-HTMX 303 fallback) leaves ?flash=<key> in the
 * address bar. The server-rendered flash is already in #flash-region, so on load
 * flash.js strips the consumed param via history.replaceState — a manual refresh
 * then won't re-show the message. Boosted navigations are handled server-side by
 * HX-Replace-Url and are out of scope here.
 *
 * Pattern: set window URL → eval() the IIFE (runs on load) → assert the URL.
 */
import { readFileSync } from 'node:fs';
import { resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';

const __dirname = dirname(fileURLToPath(import.meta.url));
const scriptCode = readFileSync(resolve(__dirname, '../../src/static/admin/flash.js'), 'utf-8');

function setUrl(url) {
  // happy-dom: authoritative way to (re)point window.location without a real nav.
  if (!window.happyDOM) throw new Error('these tests require the happy-dom vitest environment');
  window.happyDOM.setURL(url);
}

describe('flash.js — ?flash= URL strip on load', () => {
  // eval()-ing flash.js registers a document 'showFlash' listener each run;
  // spy on addEventListener and remove them in afterEach so they don't
  // accumulate across tests (same guard as dark-mode.test.js).
  let addSpy;

  beforeEach(() => {
    addSpy = vi.spyOn(document, 'addEventListener');
    setUrl('http://localhost/admin/');
  });

  afterEach(() => {
    for (const [type, fn] of addSpy.mock.calls) {
      document.removeEventListener(type, fn);
    }
    addSpy.mockRestore();
  });

  it('removes a lone ?flash= param, preserving the path', () => {
    setUrl('http://localhost/admin/orgs/?flash=deleted');
    eval(scriptCode);
    expect(window.location.pathname).toBe('/admin/orgs/');
    expect(window.location.search).toBe('');
  });

  it('removes only flash, keeping other query params', () => {
    setUrl('http://localhost/admin/role-assignments/?status=all&flash=deleted&q=foo');
    eval(scriptCode);
    const params = new URLSearchParams(window.location.search);
    expect(params.has('flash')).toBe(false);
    expect(params.get('status')).toBe('all');
    expect(params.get('q')).toBe('foo');
  });

  it('leaves a URL without a flash param untouched', () => {
    setUrl('http://localhost/admin/people/?status=active');
    eval(scriptCode);
    expect(window.location.pathname).toBe('/admin/people/');
    expect(window.location.search).toBe('?status=active');
  });

  it('preserves the hash fragment while stripping flash', () => {
    setUrl('http://localhost/admin/orgs/?flash=deleted#section');
    eval(scriptCode);
    expect(window.location.search).toBe('');
    expect(window.location.hash).toBe('#section');
  });
});
