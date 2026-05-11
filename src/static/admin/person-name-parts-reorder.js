/* person-name-parts-reorder.js — up/down reorder arrows for the parts editor
 * arrays (given_names / family_names / additional_names).
 *
 * Companion to `person-name-parts-cardstack.js`. Kept separate so the
 * cardstack file stays narrowly focused on Add/Remove cap enforcement.
 *
 * DOM contract (rendered by _name_parts_editor.html):
 *   <div data-cardstack-card="<field>">
 *     <div class="form-group"><input name="<field>" value="…"></div>
 *     <button data-cardstack-reorder="up"   data-cardstack-field="<field>">↑</button>
 *     <button data-cardstack-reorder="down" data-cardstack-field="<field>">↓</button>
 *     <button data-cardstack-remove="<field>">×</button>
 *   </div>
 *
 * Mechanics: clicking ↑ swaps the input's `value` with the previous card's
 * input value; ↓ swaps with the next. Document order of inputs drives the
 * server's parsing (see `upsert_or_delete_parts`), so a value swap is
 * sufficient — no DOM reparenting required.
 *
 * Disabled state: topmost ↑ and bottommost ↓ are disabled. The script
 * re-syncs after every reorder, exposes `window.__cardstackReorderSync(root)`
 * for `person-name-parts-cardstack.js` to call after Add/Remove, and re-runs
 * init on DOMContentLoaded + htmx:afterSwap.
 */
(function () {
  function cardsIn(stack) {
    var field = stack.getAttribute('data-cardstack');
    return Array.from(stack.querySelectorAll('[data-cardstack-card="' + field + '"]'));
  }

  function syncStack(stack) {
    var cards = cardsIn(stack);
    cards.forEach(function (card, idx) {
      var up = card.querySelector('[data-cardstack-reorder="up"]');
      var down = card.querySelector('[data-cardstack-reorder="down"]');
      if (up) up.disabled = idx === 0;
      if (down) down.disabled = idx === cards.length - 1;
    });
  }

  function syncAll(root) {
    if (!root || !root.querySelectorAll) return;
    var stacks = root.querySelectorAll('[data-cardstack]');
    stacks.forEach(function (stack) {
      try {
        syncStack(stack);
      } catch (err) {
        console.error('reorder sync failed for', stack, err);
      }
    });
  }

  // Exposed for person-name-parts-cardstack.js to call after Add/Remove.
  window.__cardstackReorderSync = syncAll;

  document.addEventListener('click', function (e) {
    var btn = e.target.closest('[data-cardstack-reorder]');
    if (!btn || btn.disabled) return;
    var direction = btn.getAttribute('data-cardstack-reorder');
    var field = btn.getAttribute('data-cardstack-field');
    if (!field) return;
    var root = btn.closest('form') || document;
    var stack = root.querySelector('[data-cardstack="' + field + '"]');
    if (!stack) return;
    var card = btn.closest('[data-cardstack-card="' + field + '"]');
    if (!card) return;
    var cards = cardsIn(stack);
    var idx = cards.indexOf(card);
    var neighbor = direction === 'up' ? cards[idx - 1] : cards[idx + 1];
    if (!neighbor) return;
    var input = card.querySelector('input[name="' + field + '"]');
    var neighborInput = neighbor.querySelector('input[name="' + field + '"]');
    if (!input || !neighborInput) return;
    var tmp = input.value;
    input.value = neighborInput.value;
    neighborInput.value = tmp;
    syncStack(stack);
  });

  document.addEventListener('DOMContentLoaded', function () {
    syncAll(document);
  });
  document.addEventListener('htmx:afterSwap', function (e) {
    syncAll((e.detail && e.detail.target) || document);
  });
  syncAll(document);
})();
