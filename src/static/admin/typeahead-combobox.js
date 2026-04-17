/**
 * typeahead-combobox.js — factory for HTMX-backed typeahead combobox inputs.
 *
 * Usage (inline <script> in an HTMX partial, runs after the factory is loaded):
 *   window.initTypeaheadCombobox({ inputId, listboxId, hiddenId });
 *
 * The input must have:
 *   hx-get="<search endpoint>"
 *   hx-trigger="input changed delay:200ms"
 *   hx-target="#<listboxId>"
 *   hx-swap="innerHTML"
 *
 * Results are rendered as <li data-id="..." data-label="..."> elements.
 *
 * Keyboard: ArrowDown/Up navigates, Enter selects, Escape closes.
 * Mouse: mousedown with preventDefault selects without blurring the input.
 *   Using mousedown (not click) avoids the blur → click-target-lost race that
 *   causes mouse selection to silently fail in some browsers: mousedown fires
 *   first, moves focus away from the input, and the subsequent click may be
 *   swallowed or re-routed before the listbox handler can run.
 */
window.initTypeaheadCombobox = function initTypeaheadCombobox({
  inputId,
  listboxId,
  hiddenId,
  onSelect,
}) {
  var inp = document.getElementById(inputId);
  var ul = document.getElementById(listboxId);
  var hidden = document.getElementById(hiddenId);
  var activeIdx = -1;

  function getItems() {
    return Array.from(ul.querySelectorAll('li[data-id]'));
  }

  function setActive(idx) {
    var items = getItems();
    activeIdx = Math.max(-1, Math.min(idx, items.length - 1));
    items.forEach(function (li, i) {
      li.classList.toggle('is-active', i === activeIdx);
      if (i === activeIdx) li.scrollIntoView({ block: 'nearest' });
    });
    inp.setAttribute('aria-activedescendant', activeIdx >= 0 ? items[activeIdx].id || '' : '');
  }

  function selectItem(li) {
    hidden.value = li.dataset.id;
    inp.value = li.dataset.label;
    closeDropdown();
    if (onSelect) onSelect(li.dataset.id);
  }

  function openDropdown() {
    var r = inp.getBoundingClientRect();
    ul.style.top = r.bottom + 'px';
    ul.style.left = r.left + 'px';
    ul.style.width = r.width + 'px';
    ul.style.display = 'block';
    activeIdx = -1;
    inp.setAttribute('aria-expanded', 'true');
    document.addEventListener('click', outsideClick);
    document.addEventListener('scroll', onScroll, true);
  }

  function closeDropdown() {
    ul.style.display = 'none';
    ul.innerHTML = '';
    activeIdx = -1;
    inp.setAttribute('aria-expanded', 'false');
    inp.setAttribute('aria-activedescendant', '');
    document.removeEventListener('click', outsideClick);
    document.removeEventListener('scroll', onScroll, true);
  }

  function outsideClick(e) {
    if (!ul.contains(e.target) && e.target !== inp) closeDropdown();
  }

  function onScroll(e) {
    if (e.target !== ul) closeDropdown();
  }

  ul.addEventListener('htmx:afterSwap', function () {
    // Scope opt-* IDs to this listbox to prevent collisions when multiple
    // typeaheads (e.g. parent + child) are open simultaneously.
    ul.querySelectorAll('li[id]').forEach(function (li) {
      li.id = ul.id + '-' + li.id;
    });
    if (ul.children.length) openDropdown();
    else closeDropdown();
  });

  inp.addEventListener('keydown', function (e) {
    var items = getItems();
    if (!items.length && e.key !== 'Escape') return;
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      setActive(activeIdx + 1);
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      setActive(activeIdx - 1);
    } else if (e.key === 'Enter' && activeIdx >= 0) {
      e.preventDefault();
      selectItem(items[activeIdx]);
    } else if (e.key === 'Escape') {
      e.preventDefault();
      closeDropdown();
    }
  });

  // mousedown + preventDefault: capture selection before the browser can blur
  // the input.  A plain 'click' listener fails in some browsers because mousedown
  // fires first and moves focus away; the subsequent click is then swallowed or
  // re-routed before this handler can run.  preventDefault keeps focus on inp.
  ul.addEventListener('mousedown', function (e) {
    var li = e.target.closest('[data-id]');
    if (!li) return;
    e.preventDefault();
    selectItem(li);
  });
};
