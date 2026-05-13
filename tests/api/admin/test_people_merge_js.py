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


def test_guards_on_missing_merge_button():
    """Script must bail early on pages without the merge toggle button.

    The merge button lives in `.page-header` and is the only element
    guaranteed-stable across region swaps; if it's absent we're not on
    the People list. Table/merge-bar refs are now resolved lazily so
    their absence doesn't prevent script execution.
    """
    assert "if (!mergeBtn) return" in JS


def test_merge_mode_driven_by_dataset_flag():
    assert "mergeMode" in JS


def test_bar_visibility_gated_on_merge_mode_not_selection_count():
    """Bar appears at 0 selections (entry), not only at 2 selected.

    Implementation uses a module-scope `inMergeMode` flag rather than reading
    the table's dataset (which was unreliable after the CR #1 swap-survival
    refactor — the table can be replaced by an htmx swap mid-session).
    """
    assert "inMergeMode" in JS
    assert "if (!inMergeMode)" in JS
    assert "checked.length < 2" not in JS


def test_merge_mode_tracked_as_module_scope_flag():
    """CR #1 follow-up: `inMergeMode` survives region swaps; the dataset
    attribute on the table does not (it's a fresh DOM node post-swap)."""
    assert "var inMergeMode" in JS


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


def test_hx_target_is_people_list_region():
    """List-flow merge swaps the whole region so caption + sticky pagination
    stay in sync with the post-merge row count (CR #2 follow-up)."""
    assert "people-list-region" in JS


def test_reattaches_on_region_swap():
    """JS must re-apply merge-mode visual state after htmx:afterSwap of the
    region — otherwise filter/search/pagination breaks merge UI (CR #1)."""
    assert "htmx:afterSwap" in JS
    assert "people-list-region" in JS


def test_uses_event_delegation_for_checkbox_change():
    """Change handler must be bound at the document level so it survives
    region swaps (table element gets detached on swap)."""
    assert "document.addEventListener('change'" in JS
