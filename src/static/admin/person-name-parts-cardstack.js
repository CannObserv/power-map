/* person-name-parts-cardstack.js — vertical card stack for the parts editor
 * arrays (given_names / family_names / additional_names).
 *
 * Wires up Add / Remove buttons, enforces the per-field cap by disabling Add
 * at the cap, and rebinds itself after HTMX swaps so newly-rendered editors
 * work without a page reload.
 *
 * DOM contract (rendered by _name_parts_editor.html):
 *   <form>                              ← unique per name-row editor instance
 *     <div data-cardstack="<field>" data-cardstack-cap="<N>">
 *       <div data-cardstack-card="<field>">
 *         <input name="<field>" value="…">
 *         <button data-cardstack-remove="<field>">×</button>
 *       </div>
 *       …
 *     </div>
 *     <button data-cardstack-add="<field>">+ Add</button>
 *   </form>
 *
 * `<N>` is interpolated server-side from the Python `ARRAY_CAP` constant
 * (src/api/admin/people_name_parts.py) via the Jinja global wired in
 * src/api/admin/assets.py. There is no JS-side fallback: if the attribute
 * is missing or non-numeric, `cap()` throws so the plumbing break is loud.
 *
 * All lookups are scoped to a `root` element (the closest <form> ancestor of
 * the clicked button, or a swap-target node) so that two rows open in edit
 * mode simultaneously do not interfere with each other.
 *
 * All cap / cardinality logic is data-attribute driven so the same JS works
 * for any field without special-casing.
 */
(function () {
  function stackFor(field, root) {
    return root.querySelector('[data-cardstack="' + field + '"]');
  }

  function cardsIn(field, root) {
    var stack = stackFor(field, root);
    if (!stack) return [];
    return Array.from(stack.querySelectorAll('[data-cardstack-card="' + field + '"]'));
  }

  function cap(field, root) {
    // No fallback: the server-rendered cap comes from the Python
    // ARRAY_CAP constant via Jinja (`data-cardstack-cap="{{ ARRAY_CAP }}"`).
    // A missing or non-numeric attribute means the plumbing broke — throw
    // so the regression surfaces in the console rather than masquerading
    // as a different cap.
    var stack = stackFor(field, root);
    if (!stack) throw new Error('cardstack: no stack found for field "' + field + '"');
    var raw = stack.dataset.cardstackCap;
    var n = parseInt(raw, 10);
    if (Number.isNaN(n)) {
      throw new Error(
        'cardstack: missing/invalid data-cardstack-cap on stack for field "' +
          field +
          '" (got ' +
          JSON.stringify(raw) +
          '); expected an integer interpolated from Python ARRAY_CAP',
      );
    }
    return n;
  }

  function syncAddBtn(field, root) {
    var btn = root.querySelector('[data-cardstack-add="' + field + '"]');
    if (!btn) return;
    btn.disabled = cardsIn(field, root).length >= cap(field, root);
  }

  /* Derive a human-readable label fragment from a field name.
   * "given_names"      → "given"
   * "family_names"     → "family"
   * "additional_names" → "additional"
   * Any other value    → the field name as-is (safe fallback).
   */
  function labelFragment(field) {
    return field.replace(/_names$/, '');
  }

  function buildCard(field) {
    // Mirror the server-rendered card shape from
    // `_name_parts_editor.html`: each card is a flex row holding a
    // `.form-group`-wrapped <input> and the Remove button. The wrapper
    // is what lets the input inherit the baseline `.form-group input`
    // rule (font-size, padding, min-height: 44px); a bare <input>
    // falls back to browser-default sizing and renders smaller than
    // the rest of the form.
    var card = document.createElement('div');
    card.setAttribute('data-cardstack-card', field);
    card.style.display = 'flex';
    card.style.gap = 'var(--space-1)';
    card.style.alignItems = 'center';

    var wrapper = document.createElement('div');
    wrapper.className = 'form-group';
    wrapper.style.marginBottom = '0';
    wrapper.style.flex = '1';

    var input = document.createElement('input');
    input.type = 'text';
    input.name = field;
    input.value = '';
    wrapper.appendChild(input);
    card.appendChild(wrapper);

    var rm = document.createElement('button');
    rm.type = 'button';
    rm.className = 'btn btn--sm btn--secondary';
    rm.setAttribute('data-cardstack-remove', field);
    rm.setAttribute('aria-label', 'Remove this ' + labelFragment(field) + ' entry');
    rm.textContent = '×';
    card.appendChild(rm);
    return card;
  }

  document.addEventListener('click', function (e) {
    var addEl = e.target.closest('[data-cardstack-add]');
    if (addEl) {
      var field = addEl.getAttribute('data-cardstack-add');
      var addRoot = addEl.closest('form') || document;
      if (cardsIn(field, addRoot).length >= cap(field, addRoot)) return;
      var stack = stackFor(field, addRoot);
      if (!stack) return;
      stack.appendChild(buildCard(field));
      syncAddBtn(field, addRoot);
      return;
    }
    var rmEl = e.target.closest('[data-cardstack-remove]');
    if (rmEl) {
      var rmField = rmEl.getAttribute('data-cardstack-remove');
      var rmRoot = rmEl.closest('form') || document;
      var card = rmEl.closest('[data-cardstack-card="' + rmField + '"]');
      if (card) card.remove();
      syncAddBtn(rmField, rmRoot);
    }
  });

  function initAll(root) {
    if (!root || !root.querySelectorAll) return;
    var stacks = root.querySelectorAll('[data-cardstack]');
    stacks.forEach(function (s) {
      var stackRoot = s.closest('form') || root;
      try {
        syncAddBtn(s.getAttribute('data-cardstack'), stackRoot);
      } catch (err) {
        // Match `cap()`'s fail-loud intent without taking down peer
        // stacks on the same page: log this stack's failure and let
        // siblings continue initialising.
        console.error('cardstack init failed for', s, err);
      }
    });
  }

  document.addEventListener('DOMContentLoaded', function () {
    initAll(document);
  });
  document.addEventListener('htmx:afterSwap', function (e) {
    initAll((e.detail && e.detail.target) || document);
  });
  initAll(document);
})();
