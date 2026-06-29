"""Structural tests for people-merge.js (the People consumer of the shared
merge-mode factory).

Engine invariants (delegation, module-scope flag, label shape, etc.) moved to
test_merge_mode_js.py when the implementation was extracted into merge-mode.js
(#250). This file pins down only the People-specific config the consumer feeds
to `window.createMergeMode`, plus the boost-safe wiring contract.
"""

from pathlib import Path

_PATH = Path("src/static/admin/people-merge.js")
JS = _PATH.read_text() if _PATH.exists() else ""


def test_people_merge_js_exists():
    assert _PATH.exists()


def test_delegates_to_shared_factory():
    """Consumer must call the shared factory rather than reimplement merge mode."""
    assert "createMergeMode" in JS


def test_guards_on_factory_presence():
    """If merge-mode.js failed to load, the consumer must no-op, not throw."""
    assert "typeof window.createMergeMode" in JS


def test_references_people_table_id():
    """Core anchor — renames here without updating list/region templates break the script."""
    assert "people-table" in JS


def test_references_people_merge_btn_id():
    assert "people-merge-btn" in JS


def test_references_people_merge_bar_id():
    assert "people-merge-bar" in JS


def test_references_people_list_region():
    """List-flow merge swaps the whole region so caption + sticky pagination
    stay in sync with the post-merge row count."""
    assert "people-list-region" in JS


def test_row_id_attr_is_person():
    assert "data-person-id" in JS


def test_noun_is_people():
    assert "people" in JS


def test_targets_people_merge_url():
    """Keep buttons must construct the people merge URL, not roles/orgs."""
    assert "/admin/people/" in JS
    assert "/merge/" in JS


def test_no_early_return_on_missing_merge_button():
    """The consumer must not bail at eval time on a missing merge button — only
    on a missing factory. The factory resolves element refs lazily so the Merge
    button delivered by a later boosted nav still binds (#249)."""
    assert "if (!mergeBtn) return" not in JS
