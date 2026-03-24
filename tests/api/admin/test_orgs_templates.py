"""Static assertions for org detail template correctness.

Tests cover:
- hx-params / name consistency on search inputs (co-location check)
- Parent org table-row structure
- Script placement inside <tr> (prevents listener accumulation on swap)
- ARIA combobox pattern on both search inputs
"""
import re
from pathlib import Path

PARENT_FORM = Path("src/templates/admin/orgs/partials/_parent_form.html").read_text()
CHILD_FORM = Path("src/templates/admin/orgs/partials/_child_form_row.html").read_text()
PARENT_READ = Path("src/templates/admin/orgs/partials/_parent_read.html").read_text()
SEARCH_RESULTS = Path("src/templates/admin/orgs/partials/_search_results.html").read_text()


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
    assert 'aria-controls="child-search-results"' in CHILD_FORM


def test_child_form_search_has_aria_haspopup():
    assert 'aria-haspopup="listbox"' in CHILD_FORM


def test_search_results_options_have_ids():
    """IDs required for aria-activedescendant keyboard navigation."""
    assert 'id="opt-{{ r.id }}"' in SEARCH_RESULTS
