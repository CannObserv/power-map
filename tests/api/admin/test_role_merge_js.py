"""Structural tests for role-merge.js."""

from pathlib import Path

_PATH = Path("src/static/admin/role-merge.js")
JS = _PATH.read_text() if _PATH.exists() else ""


def test_role_merge_js_exists():
    assert _PATH.exists()


def test_references_roles_table_id():
    """Core anchor — if the ID changes without updating this script, nothing works."""
    assert "roles-table" in JS


def test_references_roles_merge_btn_id():
    """Merge/cancel button must be found by this ID."""
    assert "roles-merge-btn" in JS


def test_references_roles_merge_bar_id():
    """Action bar must be found by this ID."""
    assert "roles-merge-bar" in JS


def test_guards_on_missing_elements():
    """Script must bail early if any expected element is absent — prevents errors on other pages."""
    assert "if (!table || !mergeBtn || !mergeBar) return" in JS


def test_merge_mode_driven_by_dataset_flag():
    """Bar visibility gated on mergeMode dataset attribute — renaming breaks the state machine."""
    assert "mergeMode" in JS


def test_bar_visibility_gated_on_merge_mode_not_selection_count():
    """Bar visibility must be gated on mergeMode flag, not checked.length — this ensures the bar
    appears at 0 selections (immediately on entry) rather than only after 2 are chosen."""
    assert "mergeMode !== 'true'" in JS
    assert "checked.length < 2" not in JS


def test_zero_selection_label():
    """Prompt text when no roles are selected yet."""
    assert "Select 2 roles to merge" in JS


def test_one_selection_label():
    """Prompt text when exactly one role is selected."""
    assert "Select 1 more" in JS


def test_one_selection_uses_selected_prefix():
    """Selected role shown with 'Selected:' prefix in 1-selected state — not bare quotes."""
    assert 'Selected: "' in JS


def test_two_selection_label():
    """Action label when both roles are selected and merge is ready."""
    assert "Merge roles:" in JS


def test_checkboxes_disabled_at_max():
    """Unchosen checkboxes must be disabled once 2 roles are selected."""
    assert "atMax" in JS
    assert "cb.disabled = atMax" in JS


def test_no_shift_oldest_logic():
    """checked.shift() was removed when checkbox-disable made it unreachable — must stay gone."""
    assert "checked.shift()" not in JS


def test_exits_merge_mode_on_show_flash():
    """Successful merge fires showFlash; the listener must exit merge mode to reset the UI."""
    assert "showFlash" in JS
    assert "exitMergeMode" in JS


def test_htmx_reprocessed_after_button_update():
    """HTMX attributes are set dynamically; htmx.process() must be called so HTMX picks them up."""
    assert "htmx.process" in JS
