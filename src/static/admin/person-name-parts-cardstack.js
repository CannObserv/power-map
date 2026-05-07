/* person-name-parts-cardstack.js — vertical card stack for the parts editor
 * arrays (given_names / family_names / additional_names).
 *
 * Wires up Add / Remove buttons, enforces the 5-cap by disabling Add at the
 * cap, and rebinds itself after HTMX swaps so newly-rendered editors work
 * without a page reload.
 *
 * DOM contract (rendered by _name_parts_editor.html):
 *   <form>                              ← unique per name-row editor instance
 *     <div data-cardstack="<field>" data-cardstack-cap="5">
 *       <div data-cardstack-card="<field>">
 *         <input name="<field>" value="…">
 *         <button data-cardstack-remove="<field>">×</button>
 *       </div>
 *       …
 *     </div>
 *     <button data-cardstack-add="<field>">+ Add</button>
 *   </form>
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
    var stack = stackFor(field, root);
    return stack ? parseInt(stack.dataset.cardstackCap, 10) || 5 : 5;
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
      syncAddBtn(s.getAttribute('data-cardstack'), stackRoot);
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
