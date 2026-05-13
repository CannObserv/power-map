"""Structural tests for people-merge.js."""

from pathlib import Path

_PATH = Path("src/static/admin/people-merge.js")
JS = _PATH.read_text() if _PATH.exists() else ""


def test_people_merge_js_exists():
    assert _PATH.exists()


def test_references_people_table_id():
    """Core anchor — renames here without updating list/region templates break the script."""
    assert "people-table" in JS


def test_references_people_merge_btn_id():
    assert "people-merge-btn" in JS


def test_references_people_merge_bar_id():
    assert "people-merge-bar" in JS


def test_guards_on_missing_elements():
    """Script must bail early on pages without the people merge fixture."""
    assert "if (!table || !mergeBtn || !mergeBar) return" in JS


def test_merge_mode_driven_by_dataset_flag():
    assert "mergeMode" in JS


def test_bar_visibility_gated_on_merge_mode_not_selection_count():
    """Bar appears at 0 selections (entry), not only at 2 selected."""
    assert "mergeMode !== 'true'" in JS
    assert "checked.length < 2" not in JS


def test_zero_selection_label():
    assert "Select 2 people to merge" in JS


def test_one_selection_label():
    assert "Select 1 more" in JS


def test_one_selection_uses_selected_prefix():
    assert 'Selected: "' in JS


def test_two_selection_label():
    assert "Merge people:" in JS


def test_checkboxes_disabled_at_max():
    assert "atMax" in JS
    assert "cb.disabled = atMax" in JS


def test_no_shift_oldest_logic():
    """checked.shift() must stay absent — checkbox-disable makes it unreachable."""
    assert "checked.shift()" not in JS


def test_exits_merge_mode_on_show_flash():
    assert "showFlash" in JS
    assert "exitMergeMode" in JS


def test_htmx_reprocessed_after_button_update():
    """Dynamically set hx-post/hx-confirm requires htmx.process() to take effect."""
    assert "htmx.process" in JS


def test_targets_people_merge_url():
    """Keep buttons must construct the people merge URL, not roles."""
    assert "/admin/people/" in JS
    assert "/merge/" in JS


def test_hides_sticky_pagination_in_merge_mode():
    """People list has sticky pagination — JS must hide it on enter, restore on exit."""
    assert "pagination--sticky" in JS


def test_hx_target_is_people_table_body():
    """List-flow merge must target the rows tbody, not the whole region."""
    assert "people-table-body" in JS
