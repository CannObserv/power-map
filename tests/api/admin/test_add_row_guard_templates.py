"""Template-side wiring for the generic "+ Add" duplicate-row guard (#238).

`add-row-guard.js` disables a button while its `data-new-row-id` row exists; the
JS mechanism is covered by `tests/js/add-row-guard.test.js`. These tests lock in
the template contract the JS relies on, which the synthetic-DOM JS tests cannot
see:

  * every "+ Add" button opts into the guard via `data-new-row-id`;
  * every guarded button also carries `hx-sync="this:drop"` so a fast
    double-click can't fire a *second* request before the first row's swap
    disables the button (the JS guard only fires on that first afterSwap, so it
    can't see the in-flight window);
  * every new-row inline Cancel dispatches `powerMap:newRowClosed` so the guard
    re-enables the button on a client-side row removal (no HTMX round-trip).

`hx-sync` (request-lifecycle dedup) and the JS guard (the `disabled` UI
invariant) own separate concerns and never both write `disabled` — see #238 CR.
`hx-disabled-elt` was rejected for this because htmx re-enables it after the
swap, clobbering the guard's disable.
"""

import re
from pathlib import Path

import pytest

TEMPLATES = Path("src/templates")

# (template, expected data-new-row-id values on its "+ Add" buttons).
# The literal #238 subjects (org name/acronym) live in the first row.
ADD_BUTTON_WIRING = [
    (
        "admin/orgs/detail.html",
        [
            "name-row-new",
            "acronym-row-new",
            "child-row-new",
            "org-event-row-new",
            "email-row-new",
            "phone-row-new",
            "address-row-new",
            "link-row-new",
            "identifier-row-new",
            "role-row-new",
        ],
    ),
    (
        "admin/people/detail.html",
        [
            "name-row-new",
            "person-event-row-new",
            "email-row-new",
            "phone-row-new",
            "address-row-new",
            "link-row-new",
            "identifier-row-new",
            "person-assignment-row-new",
        ],
    ),
    ("admin/roles/detail.html", ["assignment-row-new"]),
    ("admin/settings/identifier_types.html", ["identifier-type-row-new"]),
    ("admin/settings/api_keys.html", ["api-key-row-new"]),
    ("admin/settings/link_types.html", ["link-type-row-new"]),
]

# Inline form-row partials whose new-row Cancel removes the row client-side and
# must signal the guard. `_*_edit_row.html` partials carry the new-row branch too.
FORM_ROW_PARTIALS = [
    "admin/orgs/partials/_name_form_row.html",
    "admin/orgs/partials/_acronym_form_row.html",
    "admin/orgs/partials/_child_form_row.html",
    "admin/orgs/partials/_contact_form_row.html",
    "admin/orgs/partials/_address_form_row.html",
    "admin/orgs/partials/_link_form_row.html",
    "admin/orgs/partials/_identifier_form_row.html",
    "admin/orgs/partials/_role_form_row.html",
    "admin/people/partials/_name_form_row.html",
    "admin/people/partials/_contact_form_row.html",
    "admin/people/partials/_address_form_row.html",
    "admin/people/partials/_link_form_row.html",
    "admin/people/partials/_identifier_form_row.html",
    "admin/people/partials/_assignment_form_row.html",
    "admin/roles/partials/_assignment_form_row.html",
    "admin/settings/partials/_api_key_edit_row.html",
    "admin/settings/partials/_identifier_type_edit_row.html",
    "admin/settings/partials/_link_type_edit_row.html",
    "admin/shared/_event_form_row.html",
]

# A <button …> opening tag (attributes span multiple lines, no '>' until close).
_BUTTON_OPEN_TAG = re.compile(r"<button\b[^>]*>", re.S)


@pytest.mark.parametrize("template,row_ids", ADD_BUTTON_WIRING)
def test_add_buttons_opt_into_guard(template, row_ids):
    """Each "+ Add" button must carry data-new-row-id so add-row-guard.js can
    disable it while its unsaved row is present."""
    html = (TEMPLATES / template).read_text()
    for rid in row_ids:
        assert f'data-new-row-id="{rid}"' in html, (
            f"{template}: + Add button must carry data-new-row-id={rid!r}"
        )


@pytest.mark.parametrize("template", [t for t, _ in ADD_BUTTON_WIRING])
def test_guarded_buttons_drop_inflight_duplicate_request(template):
    """Every guarded "+ Add" button carries hx-sync="this:drop" so htmx drops a
    second request fired while the first is in flight — closing the
    fast-double-click window the JS guard (which only fires on the first row's
    afterSwap) can't see. Unlike hx-disabled-elt, hx-sync never writes
    `disabled`, so the guard stays its sole owner (#238 CR)."""
    html = (TEMPLATES / template).read_text()
    guarded = [t for t in _BUTTON_OPEN_TAG.findall(html) if "data-new-row-id" in t]
    assert guarded, f"{template}: expected at least one guarded + Add button"
    for tag in guarded:
        assert 'hx-sync="this:drop"' in tag, (
            f"{template}: a guarded + Add button is missing hx-sync"
        )
        assert "hx-disabled-elt" not in tag, (
            f"{template}: guarded + Add button must not use hx-disabled-elt "
            "(htmx re-enables it after the swap, clobbering the guard's disable)"
        )


@pytest.mark.parametrize("partial", FORM_ROW_PARTIALS)
def test_new_row_cancel_dispatches_close_event(partial):
    """The new-row inline Cancel removes the row and dispatches the shared
    powerMap:newRowClosed so add-row-guard.js re-enables the + Add button."""
    text = (TEMPLATES / partial).read_text()
    assert (
        "this.closest('tr').remove(); "
        "document.dispatchEvent(new CustomEvent('powerMap:newRowClosed'));"
    ) in text, f"{partial}: new-row Cancel must dispatch powerMap:newRowClosed"


def test_form_row_partial_inventory_is_complete():
    """Guard against a new inline form-row partial silently skipping the guard:
    every _*_form_row / _*_edit_row partial with a client-side Cancel remove
    must be in FORM_ROW_PARTIALS."""
    found = set()
    for path in TEMPLATES.glob("admin/**/_*row.html"):
        text = path.read_text()
        if "this.closest('tr').remove()" in text:
            found.add(str(path.relative_to(TEMPLATES)))
    assert found == set(FORM_ROW_PARTIALS), (
        "FORM_ROW_PARTIALS drifted from the templates with a client-side "
        f"Cancel remove. Only in templates: {found - set(FORM_ROW_PARTIALS)}; "
        f"only in list: {set(FORM_ROW_PARTIALS) - found}"
    )
