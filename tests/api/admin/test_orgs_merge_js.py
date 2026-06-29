"""Structural tests for orgs-merge.js (the Orgs consumer of the shared
merge-mode factory, #250).

Parity with test_people_merge_js.py — pins down the Orgs-specific config fed to
`window.createMergeMode`. Engine invariants live in test_merge_mode_js.py.
"""

from pathlib import Path

_PATH = Path("src/static/admin/orgs-merge.js")
JS = _PATH.read_text() if _PATH.exists() else ""


def test_orgs_merge_js_exists():
    assert _PATH.exists()


def test_delegates_to_shared_factory():
    assert "createMergeMode" in JS


def test_guards_on_factory_presence():
    assert "typeof window.createMergeMode" in JS


def test_references_orgs_table_id():
    assert "orgs-table" in JS


def test_references_orgs_merge_btn_id():
    assert "orgs-merge-btn" in JS


def test_references_orgs_merge_bar_id():
    assert "orgs-merge-bar" in JS


def test_references_orgs_list_region():
    assert "orgs-list-region" in JS


def test_row_id_attr_is_org():
    assert "data-org-id" in JS


def test_noun_is_organizations():
    assert "organizations" in JS


def test_targets_orgs_merge_url():
    """Keep buttons must construct the orgs merge URL, not people/roles."""
    assert "/admin/orgs/" in JS
    assert "/merge/" in JS
