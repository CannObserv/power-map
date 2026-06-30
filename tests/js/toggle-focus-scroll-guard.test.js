/**
 * Static CSS guard for the pill-toggle focus-scroll fix (issue #253).
 *
 * Symptom: on Organization detail, clicking a `.toggle` (e.g. the Acronym
 * "Canonical" switch) slammed the `.admin-main` inner scroll container to an
 * extreme, overlaying empty whitespace below the content until the user
 * scrolled back up. No JS was involved — it was native browser focus-scroll.
 *
 * Root cause: the visually-hidden `<input type=checkbox>` was hidden with
 * `position: absolute; width: 0; height: 0` while its `.toggle` label carried
 * no `position`, so the checkbox's containing block resolved to the initial
 * containing block (the viewport). Focusing it (via the label) made the
 * browser scroll `.admin-main` using viewport-relative geometry for an element
 * living deep inside the scrolled container → the jump.
 *
 * Fix (two parts, both asserted here):
 *   1. `.toggle` is `position: relative` so the input's containing block is the
 *      on-screen label, not the viewport.
 *   2. The input uses the clip-based visually-hidden technique (1px + clip),
 *      not the zero-size `width:0;height:0` footgun.
 *
 * happy-dom has no layout engine, so the scroll itself can't be exercised; this
 * locks the CSS contract instead — the same static-guard shape used for the
 * #252 column-count drift guards.
 *
 * Pattern: read admin.css → extract a rule body by exact selector → assert
 * declarations. Comments are stripped first so selector matching is exact.
 */
import { readFileSync } from 'node:fs';
import { resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { describe, it, expect } from 'vitest';

const __dirname = dirname(fileURLToPath(import.meta.url));
const css = readFileSync(resolve(__dirname, '../../src/static/admin/admin.css'), 'utf-8').replace(
  /\/\*[\s\S]*?\*\//g,
  '',
); // strip comments so selectors match exactly

/** Return the declaration block (whitespace-normalized) for an exact selector. */
function ruleBody(selector) {
  for (const chunk of css.split('}')) {
    const brace = chunk.indexOf('{');
    if (brace === -1) continue;
    if (chunk.slice(0, brace).trim() === selector) {
      return chunk
        .slice(brace + 1)
        .replace(/\s+/g, ' ')
        .trim();
    }
  }
  return null;
}

describe('pill toggle — focus-scroll guard (#253)', () => {
  it('positions the .toggle label so the hidden input anchors to it, not the viewport', () => {
    const body = ruleBody('.toggle');
    expect(body).not.toBeNull();
    expect(body).toMatch(/position:\s*relative/);
  });

  it('hides the checkbox via the clip technique, not the zero-size footgun', () => {
    const body = ruleBody('.toggle input[type=checkbox]');
    expect(body).not.toBeNull();
    // The footgun that caused the scroll jump must be gone.
    expect(body).not.toMatch(/width:\s*0(\D|$)/);
    expect(body).not.toMatch(/height:\s*0(\D|$)/);
    // The clip-based visually-hidden technique must be present.
    expect(body).toMatch(/clip:/);
    expect(body).toMatch(/width:\s*1px/);
    // Still taken out of flow (no layout impact, preserves the `+` sibling
    // selectors that drive the track/thumb state).
    expect(body).toMatch(/position:\s*absolute/);
  });
});
