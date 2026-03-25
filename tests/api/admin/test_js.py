"""Structural tests for admin JS files."""

from pathlib import Path

_MODAL_JS_PATH = Path("src/static/admin/admin-modal.js")
MODAL_JS = _MODAL_JS_PATH.read_text() if _MODAL_JS_PATH.exists() else ""


def test_admin_modal_js_exists():
    assert _MODAL_JS_PATH.exists()


def test_admin_modal_js_intercepts_htmx_confirm():
    """Core functional anchor — without this listener the browser confirm() fires."""
    assert "htmx:confirm" in MODAL_JS


def test_admin_modal_js_issues_request_with_skip_default():
    """issueRequest() without true still calls window.confirm() in HTMX 1.9.x.
    Must pass true to suppress the native dialog after our modal has confirmed."""
    assert "issueRequest(true)" in MODAL_JS


def test_admin_modal_js_reads_message_from_attribute():
    """HTMX 1.9.x does not reliably populate event.detail.question; read from element."""
    assert "getAttribute('hx-confirm')" in MODAL_JS


def test_admin_modal_js_guards_on_message():
    """htmx:confirm fires for ALL HTMX requests in 1.9.x, not just hx-confirm elements.
    Must return early when the attribute is absent or buttons without hx-confirm get intercepted."""
    assert "if (!message) return" in MODAL_JS


def test_admin_modal_js_has_aria_describedby():
    """Dialog must expose the message paragraph to screen readers via aria-describedby."""
    assert "aria-describedby" in MODAL_JS


def test_admin_modal_js_uses_data_action_selectors():
    """Buttons must be selected by data-confirm-action, not fragile hardcoded IDs."""
    assert 'data-confirm-action' in MODAL_JS
    assert '#pm-confirm-cancel' not in MODAL_JS
    assert '#pm-confirm-ok' not in MODAL_JS
