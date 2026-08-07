/**
 * Tests for the `hx-vals='js:...'` expression on the person-name typeahead
 * inputs (locale, script, reading-of) in `_name_metadata_fields.html`.
 *
 * The original Issue #131 bug was that the typeahead inputs sent no `q`
 * parameter to the search endpoints (a `name="q_locale"` + `hx-params="q"`
 * mismatch). The static Python template tests assert the new shape exists,
 * but htmx itself evaluates the `js:` expression at request-time via:
 *
 *     Function("event", "return (" + expr + ")").call(elem, event)
 *
 * This file evaluates the expression the same way and asserts the
 * contract end-to-end:
 *   - With a real input event, returns `{q: "<input value>"}`.
 *   - With no event in scope, degrades to `{q: ""}` (no JS exception).
 *
 * Wire format regression test: any future change that breaks the
 * locale/script/reading-of search wiring will fail here, not silently in
 * production.
 *
 * Note: this file does not eval any admin script and attaches no document
 * listeners, so the canonical vi.spyOn(document,'addEventListener') cleanup
 * block (docs/TESTING.md § Vitest test conventions) is intentionally omitted — there is nothing to
 * clean up.
 */
import { readFileSync } from 'node:fs';
import { resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { describe, it, expect } from 'vitest';

const __dirname = dirname(fileURLToPath(import.meta.url));
const PARTIAL = readFileSync(
  resolve(__dirname, '../../src/templates/admin/people/partials/_name_metadata_fields.html'),
  'utf-8',
);

/**
 * Extract the JS expression from the next `hx-vals=...` attribute that
 * follows the given anchor (a URL or attribute unique to one typeahead).
 * Handles both `'…'` and `"…"` quoting and strips the `js:` prefix.
 *
 * Implementation: from the anchor, find the next `hx-vals=`, capture the
 * value between the opening quote char (whichever is used) and the next
 * matching close. Single-character HTML attribute quotes don't allow
 * embedded quotes of the same kind without escaping, so a plain
 * search-for-the-matching-close is sufficient and robust.
 */
function extractHxVals(anchor) {
  const anchorIdx = PARTIAL.indexOf(anchor);
  if (anchorIdx < 0) throw new Error(`anchor not found: ${anchor}`);
  const tail = PARTIAL.slice(anchorIdx);
  const m = /hx-vals=(["'])((?:(?!\1).)*)\1/.exec(tail);
  if (!m) throw new Error(`no hx-vals attribute after anchor: ${anchor}`);
  return m[2].replace(/^js:/, '');
}

/** Evaluate exactly the way htmx 2.0 does. */
function evalLikeHtmx(expr, event) {
  return new Function('event', `return (${expr})`)(event);
}

const TYPEAHEADS = [
  { name: 'locale', anchor: '/admin/people/_locale_search' },
  { name: 'script', anchor: '/admin/people/_script_search' },
  { name: 'reading-of', anchor: '_reading_target_search' },
];

describe('hx-vals expression on person-name typeaheads', () => {
  for (const t of TYPEAHEADS) {
    describe(`${t.name} typeahead`, () => {
      it('sends q=<input value> when triggered by an input event', () => {
        const expr = extractHxVals(t.anchor);
        const fakeInput = { value: 'en-US' };
        const fakeEvent = { target: fakeInput };
        const out = evalLikeHtmx(expr, fakeEvent);
        expect(out).toEqual({ q: 'en-US' });
      });

      it('degrades to q="" when no event is in scope (defensive)', () => {
        const expr = extractHxVals(t.anchor);
        // htmx's debounce path can in theory fire without an event in scope;
        // the defensive shape returns an empty q rather than throwing.
        const out = evalLikeHtmx(expr, undefined);
        expect(out).toEqual({ q: '' });
      });

      it('degrades to q="" when event has no target', () => {
        const expr = extractHxVals(t.anchor);
        const out = evalLikeHtmx(expr, {});
        expect(out).toEqual({ q: '' });
      });
    });
  }

  it('all three typeaheads use the same defensive expression', () => {
    const exprs = TYPEAHEADS.map((t) => extractHxVals(t.anchor));
    expect(new Set(exprs).size).toBe(1);
  });
});
