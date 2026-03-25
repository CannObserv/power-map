/* admin-modal.js — global htmx:confirm override
 *
 * Intercepts hx-confirm and renders a styled, accessible confirmation modal
 * instead of the browser's native window.confirm() dialog.
 *
 * Template API (all on the trigger element):
 *   hx-confirm="<message>"          — modal body text (required)
 *   data-confirm-title="<text>"     — modal heading   (default: "Are you sure?")
 *   data-confirm-label="<text>"     — confirm btn text (default: "Confirm")
 *   data-confirm-variant="<class>"  — btn variant suffix (default: "danger")
 */
(function () {
  function escHtml(str) {
    return String(str)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  document.addEventListener('htmx:confirm', function (event) {
    event.preventDefault();

    var trigger = event.target;
    var message = event.detail.question;
    var title   = trigger.dataset.confirmTitle   || 'Are you sure?';
    var label   = trigger.dataset.confirmLabel   || 'Confirm';
    var variant = trigger.dataset.confirmVariant || 'danger';

    var backdrop = document.createElement('div');
    backdrop.className = 'modal-backdrop';
    backdrop.innerHTML =
      '<div class="modal" role="dialog" aria-modal="true" aria-labelledby="pm-confirm-title">' +
        '<h2 id="pm-confirm-title">' + escHtml(title) + '</h2>' +
        '<p>' + escHtml(message) + '</p>' +
        '<div class="modal__actions">' +
          '<button class="btn btn--ghost" type="button" id="pm-confirm-cancel">Cancel</button>' +
          '<button class="btn btn--' + escHtml(variant) + '" type="button" id="pm-confirm-ok">' + escHtml(label) + '</button>' +
        '</div>' +
      '</div>';

    document.body.appendChild(backdrop);

    var modal     = backdrop.querySelector('.modal');
    var cancelBtn = backdrop.querySelector('#pm-confirm-cancel');
    var okBtn     = backdrop.querySelector('#pm-confirm-ok');
    var focusable = [cancelBtn, okBtn];
    var savedFocus = document.activeElement;

    function close() {
      modal.removeEventListener('keydown', trap);
      backdrop.remove();
      if (savedFocus && savedFocus.focus) savedFocus.focus();
    }

    function trap(e) {
      if (e.key === 'Escape') { e.preventDefault(); close(); return; }
      if (e.key !== 'Tab') return;
      if (e.shiftKey) {
        if (document.activeElement === focusable[0]) {
          e.preventDefault();
          focusable[focusable.length - 1].focus();
        }
      } else {
        if (document.activeElement === focusable[focusable.length - 1]) {
          e.preventDefault();
          focusable[0].focus();
        }
      }
    }

    cancelBtn.addEventListener('click', close);
    okBtn.addEventListener('click', function () {
      close();
      event.detail.issueRequest();
    });
    modal.addEventListener('keydown', trap);
    cancelBtn.focus();
  });
})();
