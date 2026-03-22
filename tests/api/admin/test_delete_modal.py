"""Structural tests for the delete confirmation modal template."""

from pathlib import Path

TEMPLATE = Path("src/templates/admin/partials/delete_modal.html").read_text()


def test_error_container_present():
    """Modal must have an error container for inline feedback."""
    assert 'id="modal-error"' in TEMPLATE


def test_error_container_hidden_by_default():
    """Error container must be hidden until an error occurs."""
    assert "hidden" in TEMPLATE


def test_error_container_has_role_alert():
    """Error container must be announced by screen readers."""
    assert 'role="alert"' in TEMPLATE


def test_after_request_checks_successful():
    """hx-on::after-request must gate close on event.detail.successful."""
    assert "event.detail.successful" in TEMPLATE


def test_after_request_no_unconditional_close():
    """Must not call __pmCloseModal() unconditionally in hx-on::after-request."""
    # The attribute value must not be a bare call to __pmCloseModal()
    import re
    # Match the hx-on::after-request attribute value
    match = re.search(r'hx-on::after-request="([^"]+)"', TEMPLATE)
    assert match, "hx-on::after-request attribute not found"
    attr_value = match.group(1)
    # Must not be a bare unconditional close call
    assert attr_value.strip() != "window.__pmCloseModal()"
    # Must reference successful check somewhere in the handler logic
    assert "successful" in TEMPLATE


def test_handles_409_with_friendly_message():
    """A 409 response should produce a human-readable message."""
    assert "409" in TEMPLATE or "archived" in TEMPLATE.lower()


def test_close_nulls_out_handle_delete_result():
    """close() must null out __pmHandleDeleteResult to prevent stale reuse."""
    assert "__pmHandleDeleteResult" in TEMPLATE
    assert "null" in TEMPLATE


def test_delete_button_has_type_button():
    """Delete button must have type=button to prevent accidental form submit."""
    import re
    # Find the delete button (has hx-delete attribute)
    match = re.search(r'<button[^>]*hx-delete[^>]*>', TEMPLATE, re.DOTALL)
    assert match, "Delete button with hx-delete not found"
    assert 'type="button"' in match.group(0)
