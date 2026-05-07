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
    return Array.from(stack.querySelectorAll('[data-cardstack-card="' + field + '"]'));
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
    stacks.forEach(function (s) {
      syncAddBtn(s.getAttribute('data-cardstack'));
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
