"""Structural tests for merge-mode.js — the shared list-merge engine (#250).

These invariants were extracted from the old people-merge.js structural tests
when the implementation became a parameterized factory shared by the People and
Orgs list flows. Behavioural coverage (rendered labels, DOM state) lives in the
vitest suites; these guard the boost-safe lifecycle contract at the source level
so a future refactor can't silently regress it.
"""

from pathlib import Path

_PATH = Path("src/static/admin/merge-mode.js")
JS = _PATH.read_text() if _PATH.exists() else ""


def test_merge_mode_js_exists():
    assert _PATH.exists()


def test_exposes_factory_on_window():
    """Consumers (people-merge.js, orgs-merge.js) call window.createMergeMode."""
    assert "window.createMergeMode" in JS


def test_button_click_is_document_delegated():
    """The Merge button must be driven by a document-level click delegate (not a
    direct mergeBtn.addEventListener) so it survives <head>-stripping boosted
    navs and full-region swaps that replace the button element (#249)."""
    assert "document.addEventListener('click'" in JS


def test_uses_event_delegation_for_checkbox_change():
    """Change handler bound at document level so it survives region swaps."""
    assert "document.addEventListener('change'" in JS


def test_merge_mode_driven_by_dataset_flag():
    assert "mergeMode" in JS


def test_bar_visibility_gated_on_merge_mode_not_selection_count():
    """Bar appears at 0 selections (entry), not only at 2 selected — gated on a
    closure-scope inMergeMode flag, not the table dataset (unreliable post-swap)."""
    assert "inMergeMode" in JS
    assert "if (!inMergeMode)" in JS
    assert "checked.length < 2" not in JS


def test_merge_mode_tracked_as_closure_scope_flag():
    """inMergeMode survives region swaps; the dataset attribute on the table does
    not (it's a fresh DOM node post-swap)."""
    assert "var inMergeMode" in JS


def test_no_early_return_on_missing_merge_button():
    """Loaded site-wide: must NOT bail at eval time when the merge button is
    absent. Element refs are resolved lazily instead (#249)."""
    assert "if (!mergeBtn) return" not in JS


def test_zero_selection_label_built_from_noun():
    assert "'Select 2 ' + nounPlural + ' to merge:'" in JS


def test_one_selection_label():
    assert "Select 1 more" in JS


def test_one_selection_uses_selected_prefix():
    assert 'Selected: "' in JS


def test_two_selection_label_built_from_noun():
    assert "'Merge ' + nounPlural" in JS


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
    """Dynamically set hx-get (open-preview) requires htmx.process() to take effect."""
    assert "htmx.process" in JS


def test_hides_sticky_pagination_in_merge_mode():
    """List has sticky pagination — engine hides it on enter, restores on exit."""
    assert "pagination--sticky" in JS


def test_reattaches_on_region_swap():
    """Engine must re-apply merge-mode visual state after htmx:afterSwap of the
    region — otherwise filter/search/pagination breaks merge UI."""
    assert "htmx:afterSwap" in JS


def test_keep_buttons_open_preview_modal_in_portal():
    """#255: Keep buttons hx-get the merge-preview modal into the shared portal
    (the modal is the confirm step). The list region is still tracked for
    post-swap state, but it's no longer the Keep-button target."""
    assert "previewTarget" in JS
    assert "'#merge-modal-portal'" in JS
    assert "hx-get" in JS
    # The region selector is still used for swap detection / re-apply.
    assert "listRegionSelector" in JS


def test_keep_buttons_carry_no_hx_confirm():
    """#255: the bare browser confirm is gone — the modal replaces it. The engine
    must not (re)set hx-confirm on the Keep buttons, and must clear it on reset."""
    assert "'hx-confirm'," not in JS  # the setAttribute('hx-confirm', …) call is gone
    assert "removeAttribute('hx-confirm')" in JS


# ── #251 same-org predicate extension point ──────────────────────────────────


def test_optional_can_merge_predicate_gates_two_selection():
    """Roles list (#251): an optional canMerge(a, b) predicate blocks the
    two-selection enable point so a cross-org pair can't open the preview."""
    assert "config.canMerge" in JS
    assert "if (canMerge && !canMerge(rowA, rowB))" in JS


def test_cannot_merge_label_shown_when_predicate_fails():
    assert "cannotMergeLabel" in JS


def test_group_key_captured_per_row_via_optional_group_attr():
    """The predicate needs a per-row group key (e.g. data-org-id) captured into
    the checked entry; gated on the optional groupAttr config so People / Orgs
    (no groupAttr) are unaffected."""
    assert "config.groupAttr" in JS
    assert "entry.group" in JS


def test_build_preview_url_receives_entries_for_group_scoped_url():
    """An org-scoped preview URL needs the shared group, so buildPreviewUrl is
    called with the winner/loser entries (carrying .group), not just their ids."""
    assert "buildPreviewUrl(rowA.id, rowB.id, rowA, rowB)" in JS
    assert "buildPreviewUrl(rowB.id, rowA.id, rowB, rowA)" in JS
