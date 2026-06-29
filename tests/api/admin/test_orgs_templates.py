"""Static assertions for org detail template correctness.

Tests cover:
- hx-params / name consistency on search inputs (co-location check)
- Parent org table-row structure
- Script placement inside <tr> (prevents listener accumulation on swap)
- ARIA combobox pattern on both search inputs
- Confirmation modal attributes on destructive buttons
- Active toggle: restore button present when org is archived
- Name-row dropdown driven by ORG_NAME_TYPES (single source of truth)
"""

import re
from pathlib import Path

import pytest
from jinja2 import Environment, FileSystemLoader

from src.core.types import ORG_NAME_TYPES

PARENT_FORM = Path("src/templates/admin/orgs/partials/_parent_form.html").read_text()
CHILD_FORM = Path("src/templates/admin/orgs/partials/_child_form_row.html").read_text()
PARENT_READ = Path("src/templates/admin/orgs/partials/_parent_read.html").read_text()
SEARCH_RESULTS = Path("src/templates/admin/orgs/partials/_search_results.html").read_text()
BASE_HTML = Path("src/templates/admin/base.html").read_text()
DETAIL_HTML = Path("src/templates/admin/orgs/detail.html").read_text()
PERSON_DETAIL_HTML = Path("src/templates/admin/people/detail.html").read_text()
ACTIVE_TOGGLE = Path("src/templates/admin/orgs/partials/_active_toggle.html").read_text()
NOTES_FORM = Path("src/templates/admin/orgs/partials/_notes_form.html").read_text()
LIST_HTML = Path("src/templates/admin/orgs/list.html").read_text()
REGION_HTML = Path("src/templates/admin/orgs/_region.html").read_text()
NAME_FORM_ROW = Path("src/templates/admin/orgs/partials/_name_form_row.html").read_text()


def _render_child_form(org_id: str = "org_x") -> str:
    """Render `_child_form_row.html` so `{{ row_key }}` is materialised.

    Issue #125: tests that grep for the suffixed ids (e.g.
    `child-search-results-new`) must operate on rendered output, not the
    raw template source which still contains the literal Jinja `{{ row_key }}`.
    """
    env = Environment(loader=FileSystemLoader("src/templates"))
    return env.get_template("admin/orgs/partials/_child_form_row.html").render(org_id=org_id)


# ---------------------------------------------------------------------------
# hx-params / name consistency
# ---------------------------------------------------------------------------


def test_parent_form_search_input_named_q():
    """hx-params='q' requires the input name to also be 'q'; mismatch sends empty query."""
    assert 'name="q"' in PARENT_FORM


def test_child_form_search_input_named_q():
    """hx-params='q' requires the input name to also be 'q'; mismatch sends empty query."""
    assert 'name="q"' in CHILD_FORM


def test_parent_form_hx_params_q_and_name_q_on_same_input():
    """hx-params='q' and name='q' must be on the same <input> element."""
    inputs = re.findall(r'<input\b[^>]*hx-params="q"[^>]*/?\s*>', PARENT_FORM, re.DOTALL)
    assert inputs, "No input with hx-params='q' found in _parent_form.html"
    for inp in inputs:
        assert 'name="q"' in inp, "Input with hx-params='q' must also have name='q'"


def test_child_form_hx_params_q_and_name_q_on_same_input():
    """hx-params='q' and name='q' must be on the same <input> element."""
    inputs = re.findall(r'<input\b[^>]*hx-params="q"[^>]*/?\s*>', CHILD_FORM, re.DOTALL)
    assert inputs, "No input with hx-params='q' found in _child_form_row.html"
    for inp in inputs:
        assert 'name="q"' in inp, "Input with hx-params='q' must also have name='q'"


# ---------------------------------------------------------------------------
# Parent org table-row structure
# ---------------------------------------------------------------------------


def test_parent_form_is_table_row():
    assert '<tr id="parent-row">' in PARENT_FORM


def test_parent_read_is_table_row():
    assert '<tr id="parent-row">' in PARENT_READ


def test_parent_read_shows_dash_for_empty():
    """Consistent with other empty fields on the detail screen."""
    assert ">-<" in PARENT_READ


# ---------------------------------------------------------------------------
# Script placement — must be inside <tr> so HTMX outerHTML swap removes it
# ---------------------------------------------------------------------------


def test_parent_form_script_inside_tr():
    """<script> after </tr> persists after swap and accumulates listeners."""
    tr_end = PARENT_FORM.rfind("</tr>")
    script_start = PARENT_FORM.rfind("<script>")
    assert tr_end > 0, "Expected a </tr> in _parent_form.html"
    assert script_start > 0, "Expected a <script> in _parent_form.html"
    assert script_start < tr_end, "<script> must appear before </tr>"


# ---------------------------------------------------------------------------
# ARIA combobox pattern
# ---------------------------------------------------------------------------


def test_parent_form_search_has_combobox_role():
    assert 'role="combobox"' in PARENT_FORM


def test_parent_form_search_has_aria_controls():
    assert 'aria-controls="parent-search-results"' in PARENT_FORM


def test_parent_form_search_has_aria_haspopup():
    assert 'aria-haspopup="listbox"' in PARENT_FORM


def test_child_form_search_has_combobox_role():
    assert 'role="combobox"' in CHILD_FORM


def test_child_form_search_has_aria_controls():
    # Issue #125: per-row id suffix; new-row form uses the literal `new` row-key,
    # so we render the template to materialise `{{ row_key }}` before asserting.
    assert 'aria-controls="child-search-results-new"' in _render_child_form()


def test_child_form_search_has_aria_haspopup():
    assert 'aria-haspopup="listbox"' in CHILD_FORM


def test_search_results_options_have_ids():
    """IDs required for aria-activedescendant keyboard navigation."""
    assert 'id="opt-{{ r.id }}"' in SEARCH_RESULTS


# ---------------------------------------------------------------------------
# Confirmation modal — hx-confirm + data-confirm-label on destructive buttons
# ---------------------------------------------------------------------------


def test_unlink_button_has_hx_confirm():
    """hx-confirm triggers the custom modal; without it the button fires immediately."""
    assert 'hx-confirm="Remove parent organization?"' in PARENT_FORM


def test_unlink_button_has_confirm_label():
    """data-confirm-label overrides the default 'Confirm' text in the modal."""
    assert 'data-confirm-label="Unlink"' in PARENT_FORM


def test_unlink_hx_confirm_and_label_on_same_button():
    """Both attrs must be on the same element so the modal shows the right label."""
    buttons = re.findall(r"<button\b[^>]*hx-confirm=[^>]*>[^<]*</button>", PARENT_FORM, re.DOTALL)
    unlink = [b for b in buttons if 'hx-confirm="Remove parent organization?"' in b]
    assert unlink, "No button with hx-confirm='Remove parent organization?' found"
    assert all('data-confirm-label="Unlink"' in b for b in unlink), (
        "Unlink button with hx-confirm must also carry data-confirm-label='Unlink'"
    )


# ---------------------------------------------------------------------------
# admin-modal.js loaded in base layout
# ---------------------------------------------------------------------------


def test_base_loads_admin_modal_js():
    """admin-modal.js must be loaded for the htmx:confirm override to take effect."""
    assert "admin-modal.js" in BASE_HTML


def test_base_admin_modal_js_has_defer():
    """Non-critical script; must not block page render."""
    scripts = re.findall(r"<script\b[^>]*admin-modal\.js[^>]*>", BASE_HTML)
    assert scripts, "admin-modal.js script tag not found in base.html"
    assert all("defer" in s for s in scripts), "admin-modal.js script tag must have defer"


def test_base_admin_modal_js_is_in_head():
    """Must be in <head> — body scripts are re-executed on every hx-boost navigation,
    causing duplicate document.addEventListener registrations."""
    head = BASE_HTML.split("</head>")[0]
    assert "admin-modal.js" in head, "admin-modal.js must be in <head> (hx-boost re-execution)"


# flash.js loaded in base layout
# ---------------------------------------------------------------------------


def test_base_loads_flash_js():
    """flash.js must be loaded for the showFlash HX-Trigger listener to be active."""
    assert "flash.js" in BASE_HTML


def test_base_flash_js_has_defer():
    """Non-critical script; must not block page render."""
    scripts = re.findall(r"<script\b[^>]*flash\.js[^>]*>", BASE_HTML)
    assert scripts, "flash.js script tag not found in base.html"
    assert all("defer" in s for s in scripts), "flash.js script tag must have defer"


def test_base_flash_js_is_in_head():
    """Must be in <head> — body scripts are re-executed on every hx-boost navigation,
    causing duplicate document.addEventListener registrations."""
    head = BASE_HTML.split("</head>")[0]
    assert "flash.js" in head, "flash.js must be in <head> to avoid hx-boost re-execution"


# ---------------------------------------------------------------------------
# Detail-page interaction scripts must load site-wide via base.html <head>.
#
# Regression guard for #237. hx-boost (admin-layout) strips the <head> from
# boosted navigation responses (htmx makeFragment drops <head>…</head>), so a
# script placed only in a detail template's {% block extra_head %} NEVER
# executes when the page is reached by clicking a link — its document
# listeners are never registered. Loading these in base.html's <head> means
# they run once on the first full page load and persist across every boosted
# swap, exactly like flash.js. Each script is defensive (no-ops when its
# elements are absent), so loading it on every admin page is safe.
# ---------------------------------------------------------------------------

# Scripts that were previously trapped in detail-page extra_head blocks.
_SITEWIDE_DETAIL_SCRIPTS = [
    "org-detail.js",  # updateOrgHeader listener
    "person-detail.js",  # updatePersonHeader listener
    "role-merge.js",  # roles-table merge mode
    "people-merge.js",  # People list merge mode (#249)
    "merge-mode.js",  # shared list-merge factory (#250)
    "orgs-merge.js",  # Orgs list merge mode (#250)
    "add-row-guard.js",  # generic +Add duplicate-row guard (#238)
    "person-name-deadname-confirm.js",  # deadname save confirmation
    "person-name-parts-reorder.js",  # parts editor reorder arrows
    "person-name-parts-cardstack.js",  # parts editor add/remove
    "person-name-row-typeahead.js",  # per-row locale/script/reading typeaheads
]


@pytest.mark.parametrize("script", _SITEWIDE_DETAIL_SCRIPTS)
def test_base_loads_detail_script_site_wide(script):
    """Must load in base.html so the listener registers before any boosted nav.

    If it lives only in a detail template's extra_head, hx-boost strips it and
    the live-update / guard behavior silently dies (#237)."""
    assert script in BASE_HTML, f"{script} must be loaded in base.html (site-wide)"


@pytest.mark.parametrize("script", _SITEWIDE_DETAIL_SCRIPTS)
def test_base_detail_script_in_head(script):
    """Must be in <head> — body scripts re-execute on every hx-boost navigation,
    duplicating document.addEventListener registrations."""
    head = BASE_HTML.split("</head>")[0]
    assert script in head, f"{script} must be in <head> to survive hx-boost"


@pytest.mark.parametrize("script", _SITEWIDE_DETAIL_SCRIPTS)
def test_base_detail_script_has_defer(script):
    """Must use defer so the script runs after DOM parse and HTMX is available."""
    tags = re.findall(rf"<script\b[^>]*{re.escape(script)}[^>]*>", BASE_HTML)
    assert tags, f"{script} script tag not found in base.html"
    assert all("defer" in s for s in tags), f"{script} script tag must have defer"


@pytest.mark.parametrize("script", _SITEWIDE_DETAIL_SCRIPTS)
def test_detail_templates_do_not_reload_sitewide_scripts(script):
    """Detail templates must NOT re-load these in a <script> tag — that
    double-loads them on the initial full render and re-runs their top-level
    code. (Bare filename mentions in comments are fine; only script tags count.)"""
    pat = re.compile(rf"<script\b[^>]*{re.escape(script)}")
    assert not pat.search(DETAIL_HTML), f"{script} must not be re-declared in orgs/detail.html"
    assert not pat.search(PERSON_DETAIL_HTML), (
        f"{script} must not be re-declared in people/detail.html"
    )


# ---------------------------------------------------------------------------
# Active toggle — restore button when archived
# ---------------------------------------------------------------------------


def test_active_toggle_restore_button_inside_archived_block():
    """Restore form must be inside the {% if org.archived_at %} block."""
    start = ACTIVE_TOGGLE.rindex("{% if org.archived_at %}")
    end = ACTIVE_TOGGLE.index("{% endif %}", start)
    if_block = ACTIVE_TOGGLE[start:end]
    assert "unarchive/" in if_block, "restore form must be inside {% if org.archived_at %} block"


def test_active_toggle_restore_button_is_form_post():
    """Must be a plain form POST — no HTMX, consistent with archive button."""
    assert 'method="POST"' in ACTIVE_TOGGLE


def test_active_toggle_restore_button_text():
    """Button text must be descriptive."""
    assert "Restore from archive" in ACTIVE_TOGGLE


# ---------------------------------------------------------------------------
# Notes edit form — Save/Cancel in header row (not below textarea)
# ---------------------------------------------------------------------------


def test_notes_form_save_before_textarea():
    """Save must be in the header row (above the textarea), not in a form-actions div below."""
    save_pos = NOTES_FORM.index('type="submit"')
    textarea_pos = NOTES_FORM.index("<textarea")
    assert save_pos < textarea_pos, "Save button must appear before <textarea> (in header row)"


def test_notes_form_cancel_before_textarea():
    """Cancel must be in the header row (above the textarea), not in a form-actions div below."""
    cancel_pos = NOTES_FORM.index('type="button"')
    textarea_pos = NOTES_FORM.index("<textarea")
    assert cancel_pos < textarea_pos, "Cancel button must appear before <textarea> (in header row)"


def test_notes_form_no_form_actions():
    """Buttons live in the header row; form-actions div is not used."""
    assert "form-actions" not in NOTES_FORM


def test_notes_form_label_for_textarea():
    """label[for=notes-textarea] must be present for screen-reader association."""
    assert 'for="notes-textarea"' in NOTES_FORM


# ---------------------------------------------------------------------------
# Child form row — scoped search endpoint
# ---------------------------------------------------------------------------


def test_child_form_uses_scoped_search_endpoint():
    """Must hit /{org_id}/children/search/, not the generic /search/.

    The scoped endpoint excludes existing children and self; the generic endpoint
    would include them, allowing the user to accidentally re-link an already-linked child.
    """
    assert "children/search/" in CHILD_FORM
    assert 'hx-get="/admin/orgs/search/"' not in CHILD_FORM


def test_child_form_targets_own_row_on_submit():
    """On successful add, the form row must replace itself (outerHTML swap).

    Targeting tbody with afterbegin inserts the new child row but leaves the
    form row in the DOM — the row never clears after submit.

    Issue #125: hx-target is now `#child-row-{{ row_key }}`, so render the
    template to materialise the suffix before asserting.
    """
    rendered = _render_child_form()
    assert 'hx-target="#child-row-new"' in rendered
    assert 'hx-swap="outerHTML"' in rendered


# ---------------------------------------------------------------------------
# List page — pagination placement and dup notice position
# ---------------------------------------------------------------------------


def test_orgs_region_has_single_pagination_call():
    """Top pagination was removed; only the sticky call should remain.

    Two calls would render a redundant pagination bar above the table.
    """
    assert REGION_HTML.count("pagination(") == 1


def test_orgs_list_dup_slot_precedes_filter_card():
    """Dup banner slot must appear above the filter card in list.html (not inside
    #orgs-list-region), so the banner survives HTMX filter changes."""
    slot_pos = LIST_HTML.index('hx-get="/admin/_dup-badge/orgs/?variant=banner"')
    filter_pos = LIST_HTML.index('class="filter-card"')
    assert slot_pos < filter_pos, "dup banner slot must precede the filter card in list.html"


def test_orgs_list_has_no_inline_dup_count():
    """Inline org_dup_count removed; list.html must not contain the old template variable."""
    assert "org_dup_count" not in LIST_HTML


def test_orgs_region_has_no_dup_notice():
    """Dup notice must not appear in _region.html.

    If it were there, every HTMX filter change would restore the
    banner even after the user dismissed it.
    """
    assert "_dup-badge/orgs/" not in REGION_HTML
    assert "org_dup_count" not in REGION_HTML


# ---------------------------------------------------------------------------
# Child form row — scoped search endpoint
# ---------------------------------------------------------------------------


def test_child_search_input_has_explicit_innerhtml_swap():
    """Search input must declare hx-swap="innerHTML" to override the form's outerHTML swap.

    Without it, HTMX inherits outerHTML from the parent <form>, replacing the
    entire <ul#child-search-results-new> with bare <li> elements on each keystroke —
    the ul disappears from the DOM and the typeahead breaks.

    Issue #125: hx-target now points at the row-key-suffixed listbox id, so we
    render the template before pattern-matching.
    """
    rendered = _render_child_form()
    pattern = r'<input\b[^>]*hx-target="#child-search-results-new"[^>]*/?\s*>'
    inputs = re.findall(pattern, rendered, re.DOTALL)
    assert inputs, "No input with hx-target='#child-search-results-new' found"
    for inp in inputs:
        assert 'hx-swap="innerHTML"' in inp, 'Search input must have hx-swap="innerHTML"'


# ---------------------------------------------------------------------------
# Name form row — name_type dropdown driven by src.core.types.ORG_NAME_TYPES
# ---------------------------------------------------------------------------


def test_name_form_row_renders_an_option_for_every_org_name_type():
    """Rendered dropdown must expose every value the schema CHECK allows.

    The template iterates ``name_types`` from the route context; this
    test drives the actual Jinja render with ``ORG_NAME_TYPES`` so a
    future schema expansion (caught by ``tests/core/test_types.py``)
    propagates here automatically.
    """
    from jinja2 import Environment, FileSystemLoader

    env = Environment(loader=FileSystemLoader("src/templates"))
    rendered = env.get_template("admin/orgs/partials/_name_form_row.html").render(
        n=None, org_id="o-test", name_types=ORG_NAME_TYPES
    )
    for t in ORG_NAME_TYPES:
        assert f'<option value="{t}"' in rendered, (
            f"name_type option {t!r} missing from rendered org name form row"
        )


def test_name_form_row_does_not_hardcode_org_name_type_list():
    """Guard against re-introducing a hardcoded literal list.

    Mirrors the people-side guard: dropdown block must iterate
    ``name_types`` and contain no quoted-literal name_type tokens.
    """
    assert "{% for t in name_types %}" in NAME_FORM_ROW
    select_open = NAME_FORM_ROW.index('<select name="name_type"')
    select_close = NAME_FORM_ROW.index("</select>", select_open)
    block = NAME_FORM_ROW[select_open:select_close]
    for t in ORG_NAME_TYPES:
        assert f"'{t}'" not in block, f"name_type dropdown contains hardcoded literal {t!r}"
        assert f'"{t}"' not in block, f"name_type dropdown contains hardcoded literal {t!r}"
