# Person Detail — Name Editor Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Issue:** #127
**Branch:** `feat/127-name-editor-redesign`
**Worktree:** `.worktrees/feat/127-name-editor-redesign`
**Predecessor:** #123 (Phase 2 person-name UI; closed)
**Related (out of scope):** #126 (reorder buttons; lands on top of Task B's CardStack DOM)

**Goal:** Redesign the per-name edit drawer on the person detail page so the inline row carries only the core name fields, all metadata + structured parts live behind a single "Details" disclosure, both halves save through one Save button, the array fields use a vertical CardStack with an Add affordance, and every field has a clear label and help text.

**Architecture:** Single `<form>` per name row replaces today's two-form split (outer name form + inner parts form). The parts upsert/delete logic moves out of its own routes into the existing name create/edit handlers in [`_names_shared.py`](src/api/admin/_names_shared.py), gated by `supports_person_metadata`. The standalone parts routes are deleted along with the "Save parts" and "Remove structured parts" buttons. Array inputs (`given_names`, `family_names`, `additional_names`) become a JS-driven vertical card stack capped at 5 cards, with an Add button that disables at the cap. The `<details>` disclosure auto-opens when any non-default metadata or parts value is present on the row being edited.

**Tech Stack:** Python 3.12 / FastAPI / asyncpg / Jinja2 / HTMX / vanilla JS / pytest / Vitest.

**Conventions:**
- TDD: red → green → refactor for every behavior change.
- Frequent commits — one per task step pair (failing test → impl → passing test → commit).
- Commit messages: `#127 [type]: <description>` (types: feat, fix, refactor, docs, test, chore).
- Keep `docs/STYLE.md §32` in sync with admin-dashboard convention changes (per project memory: STYLE.md updates are not deferred to end of session).

**File map:**

Templates (modified):
- `src/templates/admin/people/partials/_name_form_row.html` — core inline row + the new Details disclosure body.
- `src/templates/admin/people/partials/_name_parts_editor.html` — converted from a self-contained `<form>` to a markup fragment included inside the parent form; loses its own Save/Remove buttons and gets the CardStack + labels.
- `src/templates/admin/people/partials/_name_row.html` — read-row subtitle drops the `parts: ` prefix (rename consistency).

Routes (modified):
- `src/api/admin/_names_shared.py` — `name_create` and `name_edit_row_post` accept the parts form fields and call the parts upsert/delete inside their existing transaction (gated by `supports_person_metadata`).
- `src/api/admin/people_name_parts.py` — keep the SQL/validation helpers as importable functions; delete the two `@router.post` route handlers and the empty `router` export.
- `src/api/admin/router.py` — drop the `include_router(people_name_parts_module.router)` mount.

Static (created/modified):
- `src/static/admin/person-name-parts-cardstack.js` (new) — Add / Remove handlers for the array CardStacks; idempotent re-init on `htmx:afterSwap`.

Tests (modified/created):
- `tests/api/admin/test_people_name_parts.py` — repoint integration tests from `/parts/` to `/edit-row/`; drop tests that exercised only the standalone delete route; add tests for the new "all-empty deletes" semantic and combined transaction.
- `tests/api/admin/test_people_name_templates.py` — update parts-editor structural assertions: no inner `<form>`, no Save-parts button, no Remove button, single Save/Cancel pair, CardStack markup, Add button, Details disclosure auto-open rule, label + help-text presence.
- `tests/api/admin/test_people_names.py` — extend edit/create tests to cover combined name+parts payload.
- `tests/js/person-name-parts-cardstack.test.js` (new) — Vitest coverage for Add / Remove / cap behavior.

Docs (modified):
- `docs/STYLE.md` §32 — note the single-form pattern for person-name editing and the "Details" disclosure terminology.
- `docs/CONVENTIONS.md` — note that "structured parts" (DB/schema term) renders as "Details" in the admin UI.

---

## Task A — Rename "Structured parts" → "Details" (UI label only)

**Why first:** atomic, independent, low risk. Sets terminology for everything that follows.

**Files:**
- Modify: `src/templates/admin/people/partials/_name_parts_editor.html` (the `<summary>` text).
- Modify: `src/templates/admin/people/partials/_name_row.html` (the read-row subtitle's `parts: ` prefix → drop the prefix).
- Modify: `tests/api/admin/test_people_name_templates.py` (search for any string assertion containing "Structured parts" or "parts: ").
- Modify: `docs/CONVENTIONS.md` (add a one-line note: DB column `person_name_parts` ↔ UI label "Details").

### Steps

- [ ] **A.1: Write the failing test — disclosure summary says "Details"**

Add to `tests/api/admin/test_people_name_templates.py`:

```python
def test_parts_editor_summary_label_says_details():
    """Issue #127: rename 'Structured parts' to 'Details' (UI label only)."""
    from jinja2 import Environment, FileSystemLoader
    env = Environment(loader=FileSystemLoader("src/templates"))
    out = env.get_template(
        "admin/people/partials/_name_parts_editor.html"
    ).render(n={"id": "nid_x"}, parts=None, person_id="pid_x")
    assert "Details" in out
    assert "Structured parts" not in out
```

- [ ] **A.2: Run the test to verify it fails**

Run: `uv run pytest tests/api/admin/test_people_name_templates.py::test_parts_editor_summary_label_says_details --no-cov -q`
Expected: FAIL — current template renders "Structured parts".

- [ ] **A.3: Update the template**

In `src/templates/admin/people/partials/_name_parts_editor.html`:
- Change `Structured parts{% if parts %} <span class="badge badge--inactive">set</span>{% endif %}` to `Details{% if parts %} <span class="badge badge--inactive">set</span>{% endif %}`.

In `src/api/admin/people_name_parts.py`, update `_summary_oob_fragment` to emit `Details` instead of `Structured parts`. (This helper survives Task A and is exercised by existing route tests; Task D will delete it along with the standalone routes that call it.)

- [ ] **A.4: Run all parts-related tests to verify the rename is complete**

Run: `uv run pytest tests/api/admin/test_people_name_templates.py tests/api/admin/test_people_name_parts.py --no-cov -q`
Expected: PASS — including the new test. Any pre-existing tests that asserted `"Structured parts"` should be updated to assert `"Details"` instead.

- [ ] **A.5: Update read-row subtitle — drop the `parts:` prefix**

In `src/templates/admin/people/partials/_name_row.html`, change line 36 from:
```html
parts: {{ n.parts_summary }}
```
to:
```html
{{ n.parts_summary }}
```
Keep the surrounding `{% if n.parts_summary %}` wrapper (lines 34-38) — without it, rows with no parts would render an empty `<div>` where the prefix used to be.

Update / add tests in `tests/api/admin/test_people_name_templates.py`:
- Find the existing `test_read_row_renders_parts_subtitle_when_present` assertion that checks for `"parts: "` and update it to assert just the summary content with no prefix.

- [ ] **A.6: Run read-row tests**

Run: `uv run pytest tests/api/admin/test_people_name_templates.py -k 'read_row' --no-cov -q`
Expected: PASS.

- [ ] **A.7: Update `docs/CONVENTIONS.md`**

In the existing "Structured parts (`person_name_parts` sidecar)" section, append a sentence: "The admin UI surfaces this section as **Details** (issue #127); the DB / route names retain `parts` / `person_name_parts`."

- [ ] **A.8: Commit**

```bash
git add src/templates/admin/people/partials/_name_parts_editor.html \
        src/templates/admin/people/partials/_name_row.html \
        src/api/admin/people_name_parts.py \
        tests/api/admin/test_people_name_templates.py \
        docs/CONVENTIONS.md
git commit -m "#127 refactor: rename 'Structured parts' → 'Details' (UI label only)"
```

---

## Task B — CardStack array inputs (replaces 5-input fixed grid)

**Why second:** pure DOM/JS refactor; server-side already tolerates variable input counts (the cap-check is `len(vals) > ARRAY_CAP`). Sets up #126's reorder-buttons cleanly. Does **not** touch save flow yet.

**Files:**
- Modify: `src/templates/admin/people/partials/_name_parts_editor.html` (replace the `for i in range(5)` block).
- Create: `src/static/admin/person-name-parts-cardstack.js` (Add / Remove handlers).
- Modify: `src/templates/admin/people/detail.html` — add `<script src="/static/admin/person-name-parts-cardstack.js" defer></script>` to the head/body the same way `person-name-deadname-confirm.js` is included (verify the path during the task).
- Modify: `tests/api/admin/test_people_name_templates.py` — replace the "5 inputs per array" assertions with CardStack-shape assertions.
- Create: `tests/js/person-name-parts-cardstack.test.js` (Vitest).

### Steps

- [ ] **B.1: Write the failing template test — CardStack markup**

Replace the existing `test_parts_editor_renders_five_inputs_per_array` test (around line 371) with:

```python
def test_parts_editor_renders_cardstack_for_each_array_field():
    """Issue #127: arrays render as a vertical card stack.
    Each existing value gets one card (one input + remove button); a single
    Add button per field appends new cards up to the 5-cap. Empty arrays
    render zero cards (Add button only).
    """
    from jinja2 import Environment, FileSystemLoader
    env = Environment(loader=FileSystemLoader("src/templates"))
    parts = {
        "given_names": ["María", "José"],
        "family_names": ["García"],
        "additional_names": [],
        "honorific_prefix": None,
        "honorific_suffix": None,
        "primary_identifier": None,
    }
    out = env.get_template(
        "admin/people/partials/_name_parts_editor.html"
    ).render(n={"id": "nid_x"}, parts=parts, person_id="pid_x")
    # Each card carries a data-cardstack="<field>" hook for the JS.
    assert out.count('data-cardstack-card="given_names"') == 2
    assert out.count('data-cardstack-card="family_names"') == 1
    assert out.count('data-cardstack-card="additional_names"') == 0
    # One Add button per field (always present, even when array empty).
    assert out.count('data-cardstack-add="given_names"') == 1
    assert out.count('data-cardstack-add="family_names"') == 1
    assert out.count('data-cardstack-add="additional_names"') == 1
    # Stack hook for the JS to find the cards container.
    assert 'data-cardstack="given_names"' in out
    assert 'data-cardstack="family_names"' in out
    assert 'data-cardstack="additional_names"' in out


def test_parts_editor_drops_max_5_hint():
    """Issue #127: '(max 5)' hint removed; cap surfaced via disabled Add button."""
    from jinja2 import Environment, FileSystemLoader
    env = Environment(loader=FileSystemLoader("src/templates"))
    out = env.get_template(
        "admin/people/partials/_name_parts_editor.html"
    ).render(n={"id": "nid_x"}, parts=None, person_id="pid_x")
    assert "max 5" not in out
```

- [ ] **B.2: Run the new tests to verify they fail**

Run: `uv run pytest tests/api/admin/test_people_name_templates.py -k 'cardstack or drops_max' --no-cov -q`
Expected: FAIL — current template renders 5 fixed inputs.

- [ ] **B.3: Rewrite the array section of `_name_parts_editor.html`**

Replace the existing `{% for label, field, vals in [...] %}` block (lines 49-65 of the current template) with:

```jinja
{% for label, field, vals in [
     ('Given names',      'given_names',      _given),
     ('Family names',     'family_names',     _family),
     ('Additional names', 'additional_names', _additional),
   ] %}
<fieldset style="border:1px solid var(--color-border);padding:var(--space-2);margin:0">
  <legend style="font-size:0.75rem;padding:0 var(--space-1)">{{ label }}</legend>
  <div data-cardstack="{{ field }}" data-cardstack-cap="5"
       style="display:flex;flex-direction:column;gap:var(--space-1)">
    {% for v in vals %}
    <div data-cardstack-card="{{ field }}"
         style="display:flex;gap:var(--space-1);align-items:center">
      <input type="text" name="{{ field }}" value="{{ v }}"
             aria-label="{{ label }} {{ loop.index }}"
             style="flex:1">
      <button type="button" class="btn btn--sm btn--secondary"
              data-cardstack-remove="{{ field }}"
              aria-label="Remove this {{ label|lower|replace(' names', '') }} entry">×</button>
    </div>
    {% endfor %}
  </div>
  <button type="button" class="btn btn--sm btn--secondary"
          data-cardstack-add="{{ field }}"
          aria-label="Add another {{ label|lower|replace(' names', '') }} entry"
          style="margin-top:var(--space-1)">+ Add</button>
</fieldset>
{% endfor %}
```

- [ ] **B.4: Verify the template tests now pass**

Run: `uv run pytest tests/api/admin/test_people_name_templates.py -k 'cardstack or drops_max' --no-cov -q`
Expected: PASS.

- [ ] **B.5: Write the failing Vitest spec for the JS handler**

Create `tests/js/person-name-parts-cardstack.test.js`:

```javascript
/** @vitest-environment jsdom */
import { describe, it, expect, beforeEach } from 'vitest';
import { JSDOM } from 'jsdom';
import { readFileSync } from 'node:fs';

const SRC = readFileSync(
  'src/static/admin/person-name-parts-cardstack.js',
  'utf-8',
);

function setupDOM(initialCards = 0) {
  const cards = Array.from({ length: initialCards }, (_, i) => `
    <div data-cardstack-card="given_names">
      <input type="text" name="given_names" value="v${i}">
      <button type="button" data-cardstack-remove="given_names">×</button>
    </div>`).join('');
  const html = `
    <fieldset>
      <div data-cardstack="given_names" data-cardstack-cap="5">${cards}</div>
      <button type="button" data-cardstack-add="given_names">+ Add</button>
    </fieldset>`;
  const dom = new JSDOM(`<!doctype html><body>${html}</body>`, {
    runScripts: 'outside-only',
  });
  dom.window.eval(SRC);
  // Initial scan happens on script eval; expose helpers.
  return dom;
}

describe('person-name-parts-cardstack', () => {
  it('Add appends a new empty card', () => {
    const dom = setupDOM(1);
    const addBtn = dom.window.document.querySelector(
      '[data-cardstack-add="given_names"]',
    );
    addBtn.click();
    const cards = dom.window.document.querySelectorAll(
      '[data-cardstack-card="given_names"]',
    );
    expect(cards.length).toBe(2);
    expect(cards[1].querySelector('input').value).toBe('');
  });

  it('Remove drops the clicked card', () => {
    const dom = setupDOM(2);
    const firstRemove = dom.window.document.querySelector(
      '[data-cardstack-remove="given_names"]',
    );
    firstRemove.click();
    const cards = dom.window.document.querySelectorAll(
      '[data-cardstack-card="given_names"]',
    );
    expect(cards.length).toBe(1);
    expect(cards[0].querySelector('input').value).toBe('v1');
  });

  it('Add button disables when cap reached', () => {
    const dom = setupDOM(4);
    const addBtn = dom.window.document.querySelector(
      '[data-cardstack-add="given_names"]',
    );
    addBtn.click();
    expect(addBtn.disabled).toBe(true);
    const cards = dom.window.document.querySelectorAll(
      '[data-cardstack-card="given_names"]',
    );
    expect(cards.length).toBe(5);
  });

  it('Remove re-enables a disabled Add button', () => {
    const dom = setupDOM(5);
    const addBtn = dom.window.document.querySelector(
      '[data-cardstack-add="given_names"]',
    );
    expect(addBtn.disabled).toBe(true);
    const firstRemove = dom.window.document.querySelector(
      '[data-cardstack-remove="given_names"]',
    );
    firstRemove.click();
    expect(addBtn.disabled).toBe(false);
  });
});
```

- [ ] **B.6: Run Vitest to verify it fails (no JS file yet)**

Run: `npx vitest run tests/js/person-name-parts-cardstack.test.js`
Expected: FAIL — `src/static/admin/person-name-parts-cardstack.js` not found.

- [ ] **B.7: Implement the JS handler**

Create `src/static/admin/person-name-parts-cardstack.js`:

```javascript
/* person-name-parts-cardstack.js — vertical card stack for the parts editor
 * arrays (given_names / family_names / additional_names).
 *
 * Wires up Add / Remove buttons, enforces the 5-cap by disabling Add at the
 * cap, and rebinds itself after HTMX swaps so newly-rendered editors work
 * without a page reload.
 *
 * DOM contract (rendered by _name_parts_editor.html):
 *   <div data-cardstack="<field>" data-cardstack-cap="5">
 *     <div data-cardstack-card="<field>">
 *       <input name="<field>" value="…">
 *       <button data-cardstack-remove="<field>">×</button>
 *     </div>
 *     …
 *   </div>
 *   <button data-cardstack-add="<field>">+ Add</button>
 *
 * All cap / cardinality logic is data-attribute driven so the same JS works
 * for any field without special-casing.
 */
(function () {
  function stackFor(field) {
    return document.querySelector('[data-cardstack="' + field + '"]');
  }

  function cardsIn(field) {
    var stack = stackFor(field);
    if (!stack) return [];
    return Array.from(
      stack.querySelectorAll('[data-cardstack-card="' + field + '"]'),
    );
  }

  function cap(field) {
    var stack = stackFor(field);
    return stack ? parseInt(stack.dataset.cardstackCap, 10) || 5 : 5;
  }

  function syncAddBtn(field) {
    var btn = document.querySelector('[data-cardstack-add="' + field + '"]');
    if (!btn) return;
    btn.disabled = cardsIn(field).length >= cap(field);
  }

  function buildCard(field) {
    var card = document.createElement('div');
    card.setAttribute('data-cardstack-card', field);
    card.style.display = 'flex';
    card.style.gap = 'var(--space-1)';
    card.style.alignItems = 'center';

    var input = document.createElement('input');
    input.type = 'text';
    input.name = field;
    input.value = '';
    input.style.flex = '1';
    card.appendChild(input);

    var rm = document.createElement('button');
    rm.type = 'button';
    rm.className = 'btn btn--sm btn--secondary';
    rm.setAttribute('data-cardstack-remove', field);
    rm.setAttribute('aria-label', 'Remove this entry');
    rm.textContent = '×';
    card.appendChild(rm);
    return card;
  }

  document.addEventListener('click', function (e) {
    var addEl = e.target.closest('[data-cardstack-add]');
    if (addEl) {
      var field = addEl.getAttribute('data-cardstack-add');
      if (cardsIn(field).length >= cap(field)) return;
      var stack = stackFor(field);
      if (!stack) return;
      stack.appendChild(buildCard(field));
      syncAddBtn(field);
      return;
    }
    var rmEl = e.target.closest('[data-cardstack-remove]');
    if (rmEl) {
      var rmField = rmEl.getAttribute('data-cardstack-remove');
      var card = rmEl.closest('[data-cardstack-card="' + rmField + '"]');
      if (card) card.remove();
      syncAddBtn(rmField);
    }
  });

  function initAll(root) {
    if (!root || !root.querySelectorAll) return;
    var stacks = root.querySelectorAll('[data-cardstack]');
    stacks.forEach(function (s) { syncAddBtn(s.getAttribute('data-cardstack')); });
  }

  document.addEventListener('DOMContentLoaded', function () {
    initAll(document);
  });
  document.addEventListener('htmx:afterSwap', function (e) {
    initAll((e.detail && e.detail.target) || document);
  });
  initAll(document);
})();
```

- [ ] **B.8: Run Vitest to verify it passes**

Run: `npx vitest run tests/js/person-name-parts-cardstack.test.js`
Expected: PASS — all 4 specs.

- [ ] **B.9: Wire the script into the person-detail page**

`src/templates/admin/people/detail.html` line 12 includes the deadname-confirm script with a `?v=1` cache-bust suffix:
```html
<script src="/static/admin/person-name-deadname-confirm.js?v=1" defer></script>
```
Add an identically-shaped tag for the new file:
```html
<script src="/static/admin/person-name-parts-cardstack.js?v=1" defer></script>
```

Add template tests in `tests/api/admin/test_people_name_templates.py`:

```python
def test_detail_loads_parts_cardstack_script():
    """Issue #127: detail page loads the CardStack JS."""
    from pathlib import Path
    DETAIL = Path("src/templates/admin/people/detail.html").read_text()
    assert "person-name-parts-cardstack.js" in DETAIL


def test_detail_parts_cardstack_script_is_deferred():
    """Cache-bust suffix `?v=1` matches the deadname-confirm convention."""
    from pathlib import Path
    DETAIL = Path("src/templates/admin/people/detail.html").read_text()
    assert (
        'src="/static/admin/person-name-parts-cardstack.js?v=1" defer'
        in DETAIL
    )
```

- [ ] **B.10: Run the wiring tests + manual smoke**

Run: `uv run pytest tests/api/admin/test_people_name_templates.py -k 'cardstack' --no-cov -q`
Expected: PASS.

Manual: open `https://power-map.exe.xyz:8001/admin/people/<a person id with names>/`, click Edit on a name, expand Details, verify Add appends, Remove drops, cap of 5 disables Add.

- [ ] **B.11: Commit**

```bash
git add src/templates/admin/people/partials/_name_parts_editor.html \
        src/templates/admin/people/detail.html \
        src/static/admin/person-name-parts-cardstack.js \
        tests/api/admin/test_people_name_templates.py \
        tests/js/person-name-parts-cardstack.test.js
git commit -m "#127 feat: CardStack inputs for person_name_parts arrays"
```

---

## Task C — Labels + help text on every Details field

**Why third:** pure markup polish; no behavioral change. Safe to do before the bigger structural moves.

**Files:**
- Modify: `src/templates/admin/people/partials/_name_parts_editor.html` (every parts field gets `<label>` text + a `<small>` muted help line).
- Modify: `tests/api/admin/test_people_name_templates.py` — assertions for label + help-text presence.

### Help-text copy (final)

| Field | Label | Help text |
|---|---|---|
| `primary_identifier` | "Primary identifier" | "Which name part is the primary surname-equivalent (e.g. *family* in Japan; *patronymic* in Iceland; *mononym* for single-name traditions)." |
| `given_names` | "Given names" | "Personal names. Order matters when multiple are listed." |
| `family_names` | "Family names" | "Surnames or clan names." |
| `additional_names` | "Additional names" | "Middle names, religious names, or culturally-specific extras." |
| `honorific_prefix` | "Honorific prefix" | "Title that precedes (e.g. Dr., Hon., Sir)." |
| `honorific_suffix` | "Honorific suffix" | "Suffix that follows (e.g. Jr., PhD, II)." |

### Steps

- [ ] **C.1: Write the failing template test — labels and help text present**

Add to `tests/api/admin/test_people_name_templates.py`:

```python
def test_parts_editor_renders_labels_and_help_text():
    """Issue #127: every Details field has a clear label and one-line help."""
    from jinja2 import Environment, FileSystemLoader
    env = Environment(loader=FileSystemLoader("src/templates"))
    out = env.get_template(
        "admin/people/partials/_name_parts_editor.html"
    ).render(n={"id": "nid_x"}, parts=None, person_id="pid_x")
    expected_labels = [
        "Primary identifier", "Given names", "Family names",
        "Additional names", "Honorific prefix", "Honorific suffix",
    ]
    for label in expected_labels:
        assert label in out, f"missing label: {label!r}"
    # Help text fragments — one distinctive substring per field.
    for help_substring in (
        "primary surname-equivalent",
        "Order matters",
        "Surnames or clan",
        "Middle names",
        "Title that precedes",
        "Suffix that follows",
    ):
        assert help_substring in out, f"missing help: {help_substring!r}"
```

- [ ] **C.2: Run the test to verify it fails**

Run: `uv run pytest tests/api/admin/test_people_name_templates.py::test_parts_editor_renders_labels_and_help_text --no-cov -q`
Expected: FAIL.

- [ ] **C.3: Update the template**

In `src/templates/admin/people/partials/_name_parts_editor.html`:

For `primary_identifier`, replace the existing inline `<label style="font-size:0.75rem">primary_identifier <select…>` pattern with:
```jinja
<div class="form-group" style="margin-bottom:0;min-width:9rem">
  <label style="font-size:0.75rem;display:block">Primary identifier
    <select name="primary_identifier" aria-label="Primary identifier">
      <option value=""{% if not parts or not parts.primary_identifier %} selected{% endif %}>—</option>
      {% for v in ('family', 'given', 'patronymic', 'mononym') %}
      <option value="{{ v }}"{% if parts and parts.primary_identifier == v %} selected{% endif %}>{{ v }}</option>
      {% endfor %}
    </select>
  </label>
  <small style="color:var(--color-text-muted);font-size:0.7rem;display:block;margin-top:0.125rem">
    Which name part is the primary surname-equivalent (e.g. <em>family</em> in Japan; <em>patronymic</em> in Iceland; <em>mononym</em> for single-name traditions).
  </small>
</div>
```

For each array fieldset (given/family/additional), update the `<legend>` to use the human label (already done in Task B) and add a `<small>` directly after `<legend>` for the help-text:
```jinja
<legend style="font-size:0.75rem;padding:0 var(--space-1)">{{ label }}</legend>
<small style="color:var(--color-text-muted);font-size:0.7rem;display:block;margin-bottom:var(--space-1)">
  {{ help_text }}
</small>
```
Drive `help_text` from a parallel list in the existing `for label, field, vals in [...]` loop:
```jinja
{% for label, field, vals, help_text in [
     ('Given names',      'given_names',      _given,
      'Personal names. Order matters when multiple are listed.'),
     ('Family names',     'family_names',     _family,
      'Surnames or clan names.'),
     ('Additional names', 'additional_names', _additional,
      'Middle names, religious names, or culturally-specific extras.'),
   ] %}
```

For honorific prefix/suffix, replace the existing `<label style="font-size:0.75rem">honorific_prefix <input…>` pattern with explicit label + help:
```jinja
<div class="form-group" style="margin-bottom:0;flex:1;min-width:7rem">
  <label style="font-size:0.75rem;display:block">Honorific prefix
    <input type="text" name="honorific_prefix"
           value="{{ parts.honorific_prefix if parts and parts.honorific_prefix else '' }}"
           aria-label="Honorific prefix">
  </label>
  <small style="color:var(--color-text-muted);font-size:0.7rem;display:block;margin-top:0.125rem">
    Title that precedes (e.g. Dr., Hon., Sir).
  </small>
</div>
```
Same shape for `honorific_suffix` with the corresponding help line.

- [ ] **C.4: Run the test to verify it passes**

Run: `uv run pytest tests/api/admin/test_people_name_templates.py::test_parts_editor_renders_labels_and_help_text --no-cov -q`
Expected: PASS.

- [ ] **C.5: Run the full template test file to catch regressions**

Run: `uv run pytest tests/api/admin/test_people_name_templates.py --no-cov -q`
Expected: PASS.

- [ ] **C.6: Manual smoke**

Open the dev server, expand Details on a name, confirm labels + help lines render, no layout breakage.

- [ ] **C.7: Commit**

```bash
git add src/templates/admin/people/partials/_name_parts_editor.html \
        tests/api/admin/test_people_name_templates.py
git commit -m "#127 feat: labels + help text for Details fields"
```

---

## Task D — Unified Save (single form, combined upsert, drop standalone parts routes)

**Why fourth:** biggest structural change, highest regression risk; doing it after Tasks A–C means the markup is otherwise stable. This task subsumes issue #127's bullets #3 (single Save) and #6 (drop the explicit Remove button).

**The architectural change in plain words:**
1. The two `<form>` elements collapse into one. The outer name form already POSTs to `/edit-row/` (or `/` for create). The inner parts form is gone — its inputs become inputs of the outer form.
2. The standalone `POST /parts/` and `POST /parts/delete/` routes are deleted.
3. The name create + edit handlers in `_names_shared.py` accept the parts form fields and, when `supports_person_metadata=True`, perform the parts upsert (or DELETE-when-all-empty-and-row-exists) inside the same transaction as the name write.
4. The semantic flip: today an all-empty parts payload is a no-op. Tomorrow, when the name has an existing parts row and the user clears every parts field, Save **deletes** that row. This makes the explicit "Remove structured parts" button redundant — drop it.
5. Cancel reverts the entire row by re-fetching the read-row partial (existing behavior unchanged — single Cancel was already correct).

**Files:**
- Modify: `src/api/admin/people_name_parts.py` — keep `_PRIMARY_IDENTIFIERS`, `ARRAY_CAP`, `_trim_array` as helpers; add a new `upsert_or_delete_parts(db, name_id, …)` async function. **Drop**: `_summary_oob_fragment` (no OOB swap needed — the unified flow returns the full tbody), `_ensure_name_belongs_to_person` (the existing SELECT in `name_edit_row_post` already enforces the same guard via `WHERE id=$1 AND {entity_fk}=$2`), `_flash` (only used by the deleted handlers), the `name_parts_upsert` / `name_parts_delete` route handlers, and the `router = APIRouter(...)` export. Update the module docstring to reflect the helper-only role.
- Modify: `src/api/admin/router.py` — drop the `include_router(people_name_parts_module.router)` line and the import.
- Modify: `tests/core/test_visible_names_filter.py` — `people_name_parts.py` no longer makes raw `SELECT FROM person_names` (the dropped `_ensure_name_belongs_to_person` was the only such site). Remove the file from the `ALLOWED_DIRECT_ACCESS` allowlist (line 31). Run the lint test afterward to confirm.
- Modify: `src/api/admin/_names_shared.py` — extend `name_create` and `name_edit_row_post` Form signatures with the parts fields; gate parts handling behind `supports_person_metadata`; call `upsert_or_delete_parts` inside the existing `async with db.transaction():` block.
- Modify: `src/templates/admin/people/partials/_name_parts_editor.html` — strip the outer `<form>` and the "Save parts" / "Remove structured parts" buttons; the disclosure body becomes a fragment of inputs only.
- Modify: `src/templates/admin/people/partials/_name_form_row.html` — remove the `script` blocks for parts-form coordination (none today, but verify), keep the existing typeahead init scripts.
- Modify: `tests/api/admin/test_people_name_parts.py` — repoint integration tests from `/parts/` to `/edit-row/`; add new tests for the all-empty-deletes semantic and the combined transaction.
- Modify: `tests/api/admin/test_people_name_templates.py` — drop the `Save parts` / `Remove structured parts` / `parts-editor posts to upsert URL` assertions; add a new assertion that the parts editor no longer contains a `<form>` element.
- Modify: `tests/api/admin/test_people_names.py` — add tests for the combined name+parts payload on `/edit-row/` and `/`.

### Steps

- [ ] **D.1: Write the failing template tests — single form, no Save-parts button, no Remove button**

Replace the existing `test_parts_editor_posts_to_upsert_url`, `test_parts_editor_shows_remove_button_only_when_parts_exist`, and the inner `<form>` references in `tests/api/admin/test_people_name_templates.py` with:

```python
def test_parts_editor_has_no_inner_form():
    """Issue #127: the Details body is markup nested in the parent name form;
    no inner <form> element."""
    from jinja2 import Environment, FileSystemLoader
    env = Environment(loader=FileSystemLoader("src/templates"))
    out = env.get_template(
        "admin/people/partials/_name_parts_editor.html"
    ).render(n={"id": "nid_x"}, parts=None, person_id="pid_x")
    assert "<form" not in out


def test_parts_editor_has_no_save_parts_button():
    from jinja2 import Environment, FileSystemLoader
    env = Environment(loader=FileSystemLoader("src/templates"))
    out = env.get_template(
        "admin/people/partials/_name_parts_editor.html"
    ).render(n={"id": "nid_x"}, parts=None, person_id="pid_x")
    assert "Save parts" not in out


def test_parts_editor_has_no_remove_button_even_when_parts_exist():
    """Issue #127: clearing all fields + Save deletes the row; explicit
    Remove button is removed."""
    from jinja2 import Environment, FileSystemLoader
    env = Environment(loader=FileSystemLoader("src/templates"))
    out = env.get_template(
        "admin/people/partials/_name_parts_editor.html"
    ).render(
        n={"id": "nid_x"},
        parts={
            "given_names": ["Ada"], "family_names": None,
            "additional_names": None, "honorific_prefix": None,
            "honorific_suffix": None, "primary_identifier": "given",
        },
        person_id="pid_x",
    )
    assert "Remove structured parts" not in out
    assert "Remove Details" not in out
```

Delete or update the now-obsolete `test_parts_editor_posts_to_upsert_url` (it asserts the inner form's `hx-post`, which no longer exists), `test_parts_editor_shows_remove_button_only_when_parts_exist`, and any test asserting `Save parts`.

- [ ] **D.2: Run the new template tests to verify they fail**

Run: `uv run pytest tests/api/admin/test_people_name_templates.py -k 'inner_form or save_parts or remove_button' --no-cov -q`
Expected: FAIL.

- [ ] **D.3: Strip the inner `<form>` and the buttons from `_name_parts_editor.html`**

Replace lines 31-34 (`<form hx-post=…>`) with an opening `<div>` (or just drop the wrapper entirely — the inputs can be direct children of `<details>`). Drop the inner submit-buttons div (lines 82-84) and the entire `{% if parts %}<form hx-post=…/parts/delete/…>…{% endif %}` block (lines 88-96).

The remaining structure:
```jinja
{% if n %}
{%- set _given = … -%}
{%- set _family = … -%}
{%- set _additional = … -%}
<details id="parts-editor-{{ n.id }}" class="name-parts-editor" style="margin-top:var(--space-2)">
  <summary id="parts-summary-{{ n.id }}" style="cursor:pointer;…">
    Details{% if parts %} <span class="badge badge--inactive">set</span>{% endif %}
  </summary>
  <div style="margin-top:var(--space-2);display:flex;flex-direction:column;gap:var(--space-2)">
    <!-- primary_identifier (Task C) -->
    <!-- given/family/additional CardStacks (Task B) with help text (Task C) -->
    <!-- honorific prefix/suffix (Task C) -->
  </div>
</details>
{% endif %}
```

- [ ] **D.4: Run the template tests to verify the strip**

Run: `uv run pytest tests/api/admin/test_people_name_templates.py --no-cov -q`
Expected: PASS for all parts-editor tests.

- [ ] **D.5: Write the failing handler test — POST `/edit-row/` upserts parts**

In `tests/api/admin/test_people_name_parts.py` (this file is `pytest.mark.integration` — requires `TEST_DATABASE_URL` and runs against a real Postgres):

Replace any existing `client.post(f"/admin/people/{pid}/names/{nid}/parts/", …)` calls with `client.post(f"/admin/people/{pid}/names/{nid}/edit-row/", …)` and combine the parts payload with the required name fields (`name`, `name_type`, `is_canonical`).

Example new test:

```python
async def _setup_test_db():
    """Pattern after existing fixtures — apply schema + seed BCP47/ISO15924."""
    # … (lift the existing setup helpers in this file)


@pytest.mark.integration
def test_edit_row_post_creates_parts_row_alongside_name_update(
    client, person_with_legal_name,
):
    """Issue #127: a single POST to /edit-row/ updates the name AND upserts
    the parts row in one transaction."""
    pid = person_with_legal_name["pid"]
    nid = person_with_legal_name["nid"]
    resp = client.post(
        f"/admin/people/{pid}/names/{nid}/edit-row/",
        data={
            "name": "María José García López",
            "name_type": "legal",
            "is_canonical": "true",
            "given_names": ["María", "José"],
            "family_names": ["García", "López"],
            "primary_identifier": "family",
        },
        headers=HTMX_HEADERS,
    )
    assert resp.status_code == 200
    parts = asyncio.run(_fetch_parts(nid))
    assert parts is not None
    assert parts["given_names"] == ["María", "José"]
    assert parts["family_names"] == ["García", "López"]
    assert parts["primary_identifier"] == "family"


@pytest.mark.integration
def test_edit_row_post_with_all_empty_parts_deletes_existing_parts_row(
    client, person_with_parts,  # new fixture: name + pre-existing parts row
):
    """Issue #127: clearing every parts field on Save deletes the parts row."""
    pid = person_with_parts["pid"]
    nid = person_with_parts["nid"]
    # Pre-condition: parts row exists.
    assert asyncio.run(_fetch_parts(nid)) is not None
    resp = client.post(
        f"/admin/people/{pid}/names/{nid}/edit-row/",
        data={
            "name": person_with_parts["name"],
            "name_type": "legal",
            "is_canonical": "true",
            # No parts fields submitted.
        },
        headers=HTMX_HEADERS,
    )
    assert resp.status_code == 200
    assert asyncio.run(_fetch_parts(nid)) is None


@pytest.mark.integration
def test_edit_row_post_no_parts_fields_when_no_existing_row_is_no_op(
    client, person_with_legal_name,
):
    """Issue #127: when the row never had parts and none submitted, no row written."""
    pid = person_with_legal_name["pid"]
    nid = person_with_legal_name["nid"]
    resp = client.post(
        f"/admin/people/{pid}/names/{nid}/edit-row/",
        data={"name": "X", "name_type": "legal", "is_canonical": "true"},
        headers=HTMX_HEADERS,
    )
    assert resp.status_code == 200
    assert asyncio.run(_fetch_parts(nid)) is None


@pytest.mark.integration
def test_edit_row_post_parts_cap_violation_flashes_and_skips_name_update(
    client, person_with_legal_name,
):
    """Issue #127: parts validation rolls back the whole transaction.
    A 6-element given_names submission must NOT change the name."""
    pid = person_with_legal_name["pid"]
    nid = person_with_legal_name["nid"]
    original_name = "María José García López"
    resp = client.post(
        f"/admin/people/{pid}/names/{nid}/edit-row/",
        data={
            "name": "Changed Name",
            "name_type": "legal",
            "is_canonical": "true",
            "given_names": ["a", "b", "c", "d", "e", "f"],
        },
        headers=HTMX_HEADERS,
    )
    assert resp.status_code == 200  # HTMX flash response
    # Verify name was NOT updated.
    name_row = asyncio.run(_fetch_name_row(nid))
    assert name_row["name"] == original_name


@pytest.mark.integration
def test_create_name_with_parts_payload_inserts_both(client, person_only):
    """Issue #127: POST / (create) accepts parts fields and upserts both rows."""
    pid = person_only["pid"]
    resp = client.post(
        f"/admin/people/{pid}/names/",
        data={
            "name": "Ada Lovelace",
            "name_type": "legal",
            "is_canonical": "true",
            "given_names": ["Ada"],
            "family_names": ["Lovelace"],
            "primary_identifier": "family",
        },
        headers=HTMX_HEADERS,
    )
    assert resp.status_code == 200
    new_name = asyncio.run(_fetch_canonical_name(pid))
    assert new_name["name"] == "Ada Lovelace"
    parts = asyncio.run(_fetch_parts(new_name["id"]))
    assert parts["given_names"] == ["Ada"]
```

Before writing the new tests, scan the existing `test_people_name_parts.py` for fixture conventions:
- Confirm `person_with_legal_name` exists (it does today — see lines 41-72) and observe its `asyncio.run(setup())` / `asyncio.run(teardown())` shape.
- Add `person_with_parts` (a person + canonical name + a pre-seeded `person_name_parts` row) and `person_only` (a person with no names yet) following the same pattern.
- Add `_fetch_canonical_name(pid)` and `_fetch_name_row(nid)` async helpers next to the existing `_fetch_parts(name_id)` helper. Re-use the same `_dsn()` and `asyncpg.connect` shape so test runner mode (sync `TestClient`) stays compatible.

Drop tests that exercised only `/parts/` and `/parts/delete/` URL paths — their behaviors are now covered by the combined-flow tests above.

- [ ] **D.6: Run the new integration tests to verify they fail**

Set `TEST_DATABASE_URL` (per `docs/COMMANDS.md`) and run:

```bash
export $(cat /etc/power-map/.env | xargs) 2>/dev/null
export $(cat .env | xargs) 2>/dev/null
uv run pytest tests/api/admin/test_people_name_parts.py -m integration --no-cov -q
```

Expected: FAIL — handler doesn't accept parts fields yet.

- [ ] **D.7: Move the parts upsert/delete logic into a helper**

In `src/api/admin/people_name_parts.py`:
- Keep: `_PRIMARY_IDENTIFIERS`, `ARRAY_CAP`, `_trim_array`.
- Drop: `_summary_oob_fragment`, `_ensure_name_belongs_to_person`, `_flash`, the two `@router.post` handlers, the `router = APIRouter(...)` line, and the unused `RedirectResponse` / `HTTPException` / `escape` / `Jinja2Templates` / `flash_trigger` / `is_htmx` / `HTMLResponse` imports as appropriate. Rewrite the module docstring to describe the helper-only role and note that the visibility-allowlist comment no longer applies (the routes that justified it are gone).
- Add a new public function:

```python
async def upsert_or_delete_parts(
    db,
    *,
    name_id: str,
    given_names: list[str] | None,
    family_names: list[str] | None,
    additional_names: list[str] | None,
    honorific_prefix: str | None,
    honorific_suffix: str | None,
    primary_identifier: str | None,
) -> tuple[bool, str | None]:
    """Upsert (or delete-if-all-empty) the parts row for `name_id`.

    Returns (had_parts_after, error_message). When error_message is non-None
    the caller should surface it as a form error and roll back the
    transaction. `had_parts_after` is True iff a parts row exists for
    `name_id` after this call (used to refresh the summary badge).
    """
    # Cap check on raw arrays (matches today's _trim semantics).
    for label, vals in (
        ("given_names", given_names or []),
        ("family_names", family_names or []),
        ("additional_names", additional_names or []),
    ):
        if len(vals) > ARRAY_CAP:
            return (False, f"{label}: no more than {ARRAY_CAP} entries (got {len(vals)}).")

    given = _trim_array(given_names)
    family = _trim_array(family_names)
    additional = _trim_array(additional_names)
    pre = (honorific_prefix or "").strip() or None
    suf = (honorific_suffix or "").strip() or None
    pi_raw = (primary_identifier or "").strip()
    if pi_raw and pi_raw not in _PRIMARY_IDENTIFIERS:
        allowed = ", ".join(_PRIMARY_IDENTIFIERS)
        return (False, f"primary_identifier must be one of: {allowed} (got {pi_raw!r}).")
    pi: str | None = pi_raw or None

    has_any = bool(given or family or additional or pre or suf or pi)
    if not has_any:
        # Idempotent delete — issue #127 semantic flip. If the row never
        # existed this is a no-op; if it existed, it's now gone.
        await db.execute(
            "DELETE FROM person_name_parts WHERE person_name_id=$1", name_id,
        )
        return (False, None)

    await db.execute(
        "INSERT INTO person_name_parts ("
        "  person_name_id, given_names, family_names, additional_names,"
        "  honorific_prefix, honorific_suffix, primary_identifier"
        ") VALUES ($1, $2, $3, $4, $5, $6, $7)"
        " ON CONFLICT (person_name_id) DO UPDATE SET"
        "   given_names      = EXCLUDED.given_names,"
        "   family_names     = EXCLUDED.family_names,"
        "   additional_names = EXCLUDED.additional_names,"
        "   honorific_prefix = EXCLUDED.honorific_prefix,"
        "   honorific_suffix = EXCLUDED.honorific_suffix,"
        "   primary_identifier = EXCLUDED.primary_identifier",
        name_id, given or None, family or None, additional or None, pre, suf, pi,
    )
    return (True, None)
```

- Delete the `@router.post("…/parts/")` and `@router.post("…/parts/delete/")` handlers and the `router = APIRouter(...)` line.
- Update the module docstring to describe the new helper-only role.

In `src/api/admin/router.py`:
- Drop `from src.api.admin import people_name_parts as people_name_parts_module` (line 27) and `admin_router.include_router(people_name_parts_module.router)` (line 79).

- [ ] **D.8: Run a quick test to verify nothing else imports the removed router**

```bash
grep -rn "people_name_parts_module\|name_parts_upsert\|name_parts_delete" src/ tests/
```
Expected: no remaining references except internal cross-imports of the helpers we kept.

- [ ] **D.9: Wire the parts handler into `_names_shared.py`**

In `src/api/admin/_names_shared.py`:

Add module-level imports at the top (avoid circular import — `people_name_parts.py` no longer imports from `_names_shared.py`):

```python
from src.api.admin.people_name_parts import upsert_or_delete_parts
```

`HTMLResponse`, `RedirectResponse`, `flash_trigger`, `is_htmx`, `escape`, and `HTTPException` are already imported in this file — no additional imports needed for the new `_PartsValidationError` catch block.

Extend the `name_edit_row_post` signature with the parts fields (exactly mirroring today's `name_parts_upsert` signature):

```python
@router.post("/{name_id}/edit-row/")
async def name_edit_row_post(
    entity_id: str,
    name_id: str,
    request: Request,
    name: str = Form(...),
    name_type: str = Form("legal"),
    is_canonical: str = Form(""),
    visibility: PersonNameVisibility | None = Form(None),
    locale: str | None = Form(None),
    script: str | None = Form(None),
    sort_as: str | None = Form(None),
    reading_of_id: str | None = Form(None),
    # Parts fields — only consumed when supports_person_metadata=True.
    given_names: list[str] = Form([]),
    family_names: list[str] = Form([]),
    additional_names: list[str] = Form([]),
    honorific_prefix: str | None = Form(None),
    honorific_suffix: str | None = Form(None),
    primary_identifier: str | None = Form(None),
    user: AdminUser = Depends(get_admin_user),
    db=Depends(get_db),
):
    ...
```

Inside the existing `async with db.transaction():` block, after the `_update_name(...)` call, add (still inside the transaction, still inside the `try`):

```python
                if supports_person_metadata:
                    _, parts_err = await upsert_or_delete_parts(
                        db,
                        name_id=name_id,
                        given_names=given_names,
                        family_names=family_names,
                        additional_names=additional_names,
                        honorific_prefix=honorific_prefix,
                        honorific_suffix=honorific_suffix,
                        primary_identifier=primary_identifier,
                    )
                    if parts_err is not None:
                        # Transaction context manager rolls back on raise.
                        raise _PartsValidationError(parts_err)
```

Define `_PartsValidationError` as a private exception class at module top:

```python
class _PartsValidationError(Exception):
    """Internal signal that parts validation failed; transaction rolls back."""
```

After the `async with db.transaction():` block, catch the new exception and emit the same flash response shape as `_fk_violation_message`. The transaction is already rolled back by the time the `except` runs (the exception raised inside `async with db.transaction():` triggers asyncpg's rollback), so the name update is undone automatically:

```python
        except _PartsValidationError as exc:
            # Transaction already rolled back by the `async with` exit on raise —
            # both the name update and any partial parts write are undone.
            msg = str(exc)
            if not is_htmx(request):
                raise HTTPException(status_code=422, detail=msg) from exc
            return HTMLResponse(
                content="",
                status_code=200,
                headers=flash_trigger("error", escape(msg)),
            )
```

Apply the same shape to `name_create` so the create-name flow can also seed parts in a single Save.

- [ ] **D.10: Run the integration tests + the existing names-test suite**

```bash
uv run pytest tests/api/admin/test_people_name_parts.py tests/api/admin/test_people_names.py -m integration --no-cov -q
```
Expected: PASS — including the new tests from D.5.

- [ ] **D.11: Run the full template + handler test sweep**

```bash
uv run pytest tests/api/admin/ --no-cov -q
```
Expected: PASS. Investigate any failures referencing `_summary_oob_fragment` or remnant `/parts/` URL assertions.

- [ ] **D.12: Manual smoke**

Open `https://power-map.exe.xyz:8001/admin/people/<pid>/`, edit a name:
- Modify the name field + add `given_names=["Ada"]` → click Save → row reloads, both committed.
- Edit again, clear all parts fields → click Save → parts row gone, name preserved.
- Add 6 cards (need to bypass UI cap somehow — paste 6 inputs in DevTools or temporarily raise the JS cap) → submit → flash error, no name change.

- [ ] **D.13: Commit**

```bash
git add src/api/admin/people_name_parts.py \
        src/api/admin/_names_shared.py \
        src/api/admin/router.py \
        src/templates/admin/people/partials/_name_parts_editor.html \
        tests/api/admin/test_people_name_parts.py \
        tests/api/admin/test_people_name_templates.py \
        tests/api/admin/test_people_names.py \
        tests/core/test_visible_names_filter.py
git commit -m "#127 refactor: unify name + parts save into single Save button"
```

---

## Task E — Move metadata fields into the Details disclosure

**Why last:** the form is now structurally unified; this is a layout move only. Easier to verify nothing breaks once Task D's combined save is stable.

**Goal:** the inline row carries only `name`, `name_type`, `is_canonical`, Save, Cancel. Visibility, locale, script, sort_as, reading_of_id move into the Details disclosure (above the parts inputs, separated by an `<hr>` or a subheading). The disclosure auto-opens when any non-default metadata or parts value is present on the row being edited.

**Files:**
- Modify: `src/templates/admin/people/partials/_name_form_row.html` — move the metadata `<div class="form-group">` blocks for visibility, locale typeahead, script typeahead, sort_as, reading_of into the `_name_parts_editor.html` partial body (or restructure so they render before the parts editor inside a single Details `<details>` wrapper). Ensure typeahead `<script>` init still runs after the inputs are in the DOM.
- Modify: `src/templates/admin/people/partials/_name_parts_editor.html` — add a "Metadata" subheading + the metadata fields above the parts fields, separated by an `<hr>`. Compute the auto-open predicate once at the top.
- Modify: `tests/api/admin/test_people_name_templates.py` — auto-open predicate test, inline-row-only-has-core-fields test, metadata-inside-disclosure test.

### Auto-open predicate

The disclosure body is rendered both for existing rows (`n` is a row dict) and for the "new name" form (`n is None`). Guard the dereference with `n and (...)` so the new-row form doesn't crash on a None `n`:

```jinja
{%- set _meta_set = n and (
    (n.visibility and n.visibility != 'public') or
    n.locale or n.script or n.sort_as or n.reading_of_id
) -%}
{%- set _parts_set = parts is not none -%}
{%- set _disclosure_open = _meta_set or _parts_set -%}
<details … {% if _disclosure_open %}open{% endif %}>
```

### Steps

- [ ] **E.1: Write the failing template test — inline row carries only the core fields**

```python
def test_inline_row_excludes_metadata_fields():
    """Issue #127 bullet 1: name/type/canonical/Save/Cancel inline only."""
    from jinja2 import Environment, FileSystemLoader
    env = Environment(loader=FileSystemLoader("src/templates"))
    out = env.get_template(
        "admin/people/partials/_name_form_row.html"
    ).render(n={"id": "nid_x", "name": "X", "name_type": "legal",
                "is_canonical": True, "visibility": "public",
                "locale": None, "script": None, "sort_as": None,
                "reading_of_id": None, "reading_of_name": None},
             parts=None, person_id="pid_x")
    # Inline row (everything before <details>) should NOT contain visibility/
    # locale/script/sort_as/reading_of inputs.
    inline_section = out.split("<details", 1)[0]
    for needle in ('name="visibility"', 'name="locale"', 'name="script"',
                   'name="sort_as"', 'name="reading_of_id"'):
        assert needle not in inline_section, f"inline row leaks {needle!r}"
    # The Details disclosure body should contain them.
    details_section = out[out.index("<details"):]
    for needle in ('name="visibility"', 'name="locale"', 'name="script"',
                   'name="sort_as"', 'name="reading_of_id"'):
        assert needle in details_section, f"Details missing {needle!r}"


def test_disclosure_auto_opens_when_metadata_set():
    """Issue #127: auto-open Details when any non-default metadata present."""
    from jinja2 import Environment, FileSystemLoader
    env = Environment(loader=FileSystemLoader("src/templates"))
    n_with_locale = {"id": "nid_x", "name": "X", "name_type": "legal",
                     "is_canonical": True, "visibility": "public",
                     "locale": "ja-JP", "script": None, "sort_as": None,
                     "reading_of_id": None, "reading_of_name": None}
    out = env.get_template(
        "admin/people/partials/_name_form_row.html"
    ).render(n=n_with_locale, parts=None, person_id="pid_x")
    # Markup substring "<details … open" — match attribute presence.
    import re
    assert re.search(r"<details[^>]*\bopen\b", out)


def test_disclosure_closed_for_pristine_row():
    """No metadata set, no parts → Details closed by default."""
    from jinja2 import Environment, FileSystemLoader
    env = Environment(loader=FileSystemLoader("src/templates"))
    n = {"id": "nid_x", "name": "X", "name_type": "legal",
         "is_canonical": True, "visibility": "public",
         "locale": None, "script": None, "sort_as": None,
         "reading_of_id": None, "reading_of_name": None}
    out = env.get_template(
        "admin/people/partials/_name_form_row.html"
    ).render(n=n, parts=None, person_id="pid_x")
    import re
    assert not re.search(r"<details[^>]*\bopen\b", out)
```

- [ ] **E.2: Run the new tests to verify they fail**

Run: `uv run pytest tests/api/admin/test_people_name_templates.py -k 'inline_row_excludes or disclosure' --no-cov -q`
Expected: FAIL.

- [ ] **E.3: Restructure the templates**

In `_name_form_row.html`:
- Remove the `<div class="form-group">` blocks for visibility, locale, script, sort_as, reading_of (lines ~28-107) from the inline form.
- Keep them in a Jinja-rendered fragment (move into `_name_parts_editor.html` or pass through context — simplest: include them inline inside the `<details>` body). Easiest: just relocate the markup blocks into the part of the template currently occupied by the `{% include "admin/people/partials/_name_parts_editor.html" %}` line, by including a new combined details partial.

Cleanest restructuring path:
1. Keep the `_name_parts_editor.html` filename for git-history continuity; just expand its content to be the unified Details body.
2. The disclosure body now holds: `[Metadata] subheading → vis/locale/script/sort_as/reading_of inputs → <hr> → [Name parts] subheading → primary_identifier → CardStacks → honorifics`.
3. The inline form keeps only: name input, name_type select, is_canonical toggle, Save, Cancel — plus the disclosure include.

Jinja `{% include %}` propagates the parent context by default, so the metadata fields (which read `n.visibility`, `n.locale`, etc) and the typeahead inputs (which read `n.reading_of_id`, `n.reading_of_name`) work without any extra `with context` ceremony — keep the include statement as `{% include "admin/people/partials/_name_parts_editor.html" %}`.

Move the existing typeahead `<script>` init blocks (lines 130-158 of `_name_form_row.html`) to AFTER the include so they run when the inputs (now inside the disclosure) are in the DOM. Browsers happily query elements inside a `<details>` whether it's open or closed.

The reading_of show/hide JS (lines 145-158) keeps working — it queries by id, depth doesn't matter.

- [ ] **E.4: Run the failing tests to verify they pass; run the full suite**

Run: `uv run pytest tests/api/admin/test_people_name_templates.py --no-cov -q`
Expected: PASS — including the new tests.

Run: `uv run pytest tests/api/admin/ --no-cov -q`
Expected: PASS overall.

- [ ] **E.5: Manual smoke — end-to-end**

Open the dev server and verify:
1. **Pristine row** (default-only values): edit → Details closed → expand → all metadata + parts inputs present.
2. **Row with `locale=ja-JP`**: edit → Details auto-opens.
3. **Row with parts only**: edit → Details auto-opens.
4. **Row with deadname**: edit → name_type select shows on inline row → confirm dialog still triggers (deadname-confirm.js should still find the form by `hx-post*="/names/"`).
5. **Reading-of**: change name_type to `reading` inside the disclosure → typeahead block becomes visible.
6. **Locale typeahead**: type "es-" → suggestions land in the listbox inside the disclosure → select one → hidden input populated → Save → row reloads with the new locale.

- [ ] **E.6: Update STYLE.md**

Per project memory (`feedback_style_md_defer.md`): update STYLE.md alongside code changes. Add a paragraph to §32 describing the single-form / single-Details / single-Save pattern and the auto-open predicate, replacing any earlier reference to the two-form pattern.

- [ ] **E.7: Commit**

```bash
git add src/templates/admin/people/partials/_name_form_row.html \
        src/templates/admin/people/partials/_name_parts_editor.html \
        tests/api/admin/test_people_name_templates.py \
        docs/STYLE.md
git commit -m "#127 feat: unified Details disclosure for metadata + parts"
```

---

## Final verification

- [ ] **F.1: Full test suite**

```bash
export $(cat /etc/power-map/.env | xargs) 2>/dev/null
export $(cat .env | xargs) 2>/dev/null
uv run pytest --no-cov -q
```
Expected: all green except the pre-existing `test_get_address_normalizer_with_api_key_sets_config` env-leak failure noted in the worktree baseline (caused by `ADDRESS_VALIDATOR_RUN_VALIDATION` in `/etc/power-map/.env`; reproducible on `main`, unrelated to #127). If anything else fails, investigate before proceeding.

- [ ] **F.2: JS suite**

```bash
npx vitest run
```
Expected: PASS, including the new `person-name-parts-cardstack.test.js`.

- [ ] **F.3: Lint**

```bash
uv run ruff check
```
Expected: clean.

- [ ] **F.4: Manual end-to-end on dev server**

Walk through the issue's six bullets one at a time, confirming each is satisfied:
1. Inline row has only name/type/canonical/Save/Cancel.
2. Disclosure summary says "Details" not "Structured parts".
3. One Save button — saves both name and parts in one transaction.
4. Each Details field has a label and help text.
5. Array fields are CardStacks with Add button (disabled at 5).
6. No "Remove structured parts" button — clearing fields and saving deletes the row.

- [ ] **F.5: Push branch + open PR**

```bash
git push -u origin feat/127-name-editor-redesign
gh pr create --title "#127 feat: redesign person-name editor (single form, Details disclosure, CardStacks)" \
  --body "$(cat <<'EOF'
## Summary
- Inline row carries only name/type/canonical/Save/Cancel.
- Single "Details" disclosure for visibility, locale, script, sort_as, reading_of, primary_identifier, given/family/additional CardStacks, honorifics.
- One Save button — transactional combined upsert in `/edit-row/` and `/`.
- Standalone `/parts/` and `/parts/delete/` routes removed.
- "Remove structured parts" button removed; clearing fields + Save deletes the row.

Closes #127.

## Test plan
- [ ] `uv run pytest --no-cov -q` (excluding pre-existing env-leak in test_address.py)
- [ ] `npx vitest run`
- [ ] Manual walkthrough of issue #127 bullets 1–6 on dev server
EOF
)"
```

---

## Out of scope

- Issue #126 reorder buttons — lands on top of Task B's CardStack DOM in a follow-up PR.
- Migrating other entities (organizations) to a single-form pattern — orgs have no parts sidecar, so the existing two-form structure doesn't apply there.
- Backwards-compat shims for the deleted `/parts/` and `/parts/delete/` routes — nothing else in the repo calls them; no external consumers.
