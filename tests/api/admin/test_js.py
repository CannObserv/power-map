"""Structural tests for admin JS files."""

from pathlib import Path

_MODAL_JS_PATH = Path("src/static/admin/admin-modal.js")
MODAL_JS = _MODAL_JS_PATH.read_text() if _MODAL_JS_PATH.exists() else ""

_FLASH_JS_PATH = Path("src/static/admin/flash.js")
FLASH_JS = _FLASH_JS_PATH.read_text() if _FLASH_JS_PATH.exists() else ""

_ORG_DETAIL_JS_PATH = Path("src/static/admin/org-detail.js")
ORG_DETAIL_JS = _ORG_DETAIL_JS_PATH.read_text() if _ORG_DETAIL_JS_PATH.exists() else ""

_PERSON_DETAIL_JS_PATH = Path("src/static/admin/person-detail.js")
PERSON_DETAIL_JS = _PERSON_DETAIL_JS_PATH.read_text() if _PERSON_DETAIL_JS_PATH.exists() else ""


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


def test_flash_js_exists():
    assert _FLASH_JS_PATH.exists()


def test_flash_js_listens_for_show_flash_event():
    """Listener must be keyed to the showFlash event dispatched by HTMX from HX-Trigger header.
    Any other event name breaks delivery silently."""
    assert "showFlash" in FLASH_JS


def test_flash_js_guards_on_missing_detail():
    """HTMX may dispatch other custom events; guard prevents TypeError on missing detail."""
    assert "if (!f)" in FLASH_JS or "if (!e.detail)" in FLASH_JS


def test_flash_js_targets_flash_region():
    """Flash must be injected into #flash-region — changing this breaks the layout contract."""
    assert "flash-region" in FLASH_JS


def test_flash_js_auto_dismisses_via_settimeout():
    """Flash must auto-dismiss — without setTimeout toasts linger indefinitely."""
    assert "setTimeout" in FLASH_JS


def test_flash_js_pauses_dismiss_on_mouseenter():
    """Hover-pause prevents flash from disappearing while the user is reading it."""
    assert "mouseenter" in FLASH_JS


def test_flash_js_resumes_dismiss_on_mouseleave():
    """Dismiss timer must restart when the cursor leaves."""
    assert "mouseleave" in FLASH_JS


# ---------------------------------------------------------------------------
# org-detail.js
# ---------------------------------------------------------------------------


def test_org_detail_js_exists():
    assert _ORG_DETAIL_JS_PATH.exists()


def test_org_detail_js_listens_for_update_org_header():
    """Listener must be keyed to updateOrgHeader — any other name breaks sync silently."""
    assert "updateOrgHeader" in ORG_DETAIL_JS


def test_org_detail_js_targets_page_heading():
    """Must target id='page-heading' on the <h1> — changing the ID breaks live sync."""
    assert "page-heading" in ORG_DETAIL_JS


def test_org_detail_js_targets_breadcrumb_current():
    """Must target id='breadcrumb-current' on the breadcrumb span."""
    assert "breadcrumb-current" in ORG_DETAIL_JS


def test_org_detail_js_updates_document_title():
    """Must update document.title — tab title sync is the third live-update target."""
    assert "document.title" in ORG_DETAIL_JS


# ---------------------------------------------------------------------------
# person-detail.js
# ---------------------------------------------------------------------------


def test_person_detail_js_exists():
    assert _PERSON_DETAIL_JS_PATH.exists()


def test_person_detail_js_listens_for_update_person_header():
    """Listener must be keyed to updatePersonHeader — any other name breaks sync silently."""
    assert "updatePersonHeader" in PERSON_DETAIL_JS


def test_person_detail_js_targets_page_heading():
    """Must target id='page-heading' on the <h1> — changing the ID breaks live sync."""
    assert "page-heading" in PERSON_DETAIL_JS


def test_person_detail_js_targets_breadcrumb_current():
    """Must target id='breadcrumb-current' on the breadcrumb span."""
    assert "breadcrumb-current" in PERSON_DETAIL_JS


def test_person_detail_js_updates_document_title():
    """Must update document.title — tab title sync is the third live-update target."""
    assert "document.title" in PERSON_DETAIL_JS
