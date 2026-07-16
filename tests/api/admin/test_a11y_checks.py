"""Unit tests for the rendered-DOM a11y checker (GH #246).

Exercises ``tests.api.admin.a11y`` against synthetic HTML — no DB, no routes.
Each of the three #244 static-lint blind spots has a dedicated case:

1. Two controls under one wrapping ``<label>`` — only the first labelable
   descendant is named; the second must be flagged.
2. Include-expansion — moot at this layer (the checker sees resolved HTML),
   but the wrapping-label ancestry logic it depends on is covered here.
3. Dangling ``aria-labelledby`` / ``<label for>`` references.
"""

from tests.api.admin.a11y import (
    controls_missing_accessible_name,
    count_controls,
    dangling_id_refs,
    is_full_document,
)

# --- accessible-name resolution -------------------------------------------


def test_wrapping_label_names_only_first_labelable_descendant():
    """#244 blind spot 1: the second control under one <label> is unnamed."""
    html = '<label>Name <input name="a"> <input name="b"></label>'
    missing = controls_missing_accessible_name(html)
    assert len(missing) == 1
    assert 'name="b"' in missing[0]


def test_wrapping_label_with_for_names_its_target_not_descendants():
    """A <label for=...> names the for-target; a wrapped control is NOT named."""
    html = '<label for="x">City <input name="inner"></label><input id="x" name="target">'
    missing = controls_missing_accessible_name(html)
    assert len(missing) == 1
    assert 'name="inner"' in missing[0]


def test_label_for_names_control_by_id():
    html = '<label for="q">Search</label><input id="q" name="q">'
    assert controls_missing_accessible_name(html) == []


def test_aria_label_names_control():
    html = '<input aria-label="Search" name="q">'
    assert controls_missing_accessible_name(html) == []


def test_empty_aria_label_does_not_name_control():
    html = '<input aria-label="  " name="q">'
    missing = controls_missing_accessible_name(html)
    assert len(missing) == 1


def test_aria_labelledby_passes_name_check():
    """Presence satisfies the name check; dangling refs are a separate check."""
    html = '<input aria-labelledby="lbl" name="q">'
    assert controls_missing_accessible_name(html) == []


def test_placeholder_is_not_a_label():
    html = '<input placeholder="Search…" name="q">'
    assert len(controls_missing_accessible_name(html)) == 1


def test_hidden_input_is_exempt_and_does_not_steal_the_label():
    """Hidden inputs are not labelable: the select is still the first
    labelable descendant and receives the wrapping label's name."""
    html = '<label>Kind <input type="hidden" name="h"><select name="k"></select></label>'
    assert controls_missing_accessible_name(html) == []


def test_button_steals_first_labelable_descendant_slot():
    """Per the HTML spec a <button> is labelable: a select after it under the
    same wrapping <label> is NOT named by that label."""
    html = '<label>Kind <button>x</button><select name="k"></select></label>'
    missing = controls_missing_accessible_name(html)
    assert len(missing) == 1
    assert 'name="k"' in missing[0]


def test_select_and_textarea_require_names():
    html = '<select name="s"></select><textarea name="t"></textarea>'
    missing = controls_missing_accessible_name(html)
    assert len(missing) == 2


def test_nested_label_in_earlier_sibling_does_not_name_later_control():
    """A <label> only names its own subtree — the static lint's open/close
    counting heuristic would be fooled by this; real ancestry is not."""
    html = "<div><label>A <input name='a'></label></div><div><input name='b'></div>"
    missing = controls_missing_accessible_name(html)
    assert len(missing) == 1
    assert 'name="b"' in missing[0] or "name='b'" in missing[0]


def test_multi_root_fragment_is_parsed():
    html = '<tr><td><input name="a"></td></tr><tr><td><input aria-label="B"></td></tr>'
    missing = controls_missing_accessible_name(html)
    assert len(missing) == 1
    assert 'name="a"' in missing[0]


def test_wrapping_label_resolution_is_stable_on_large_documents():
    """Regression: naming resolution must not rely on ``id()`` of lxml element
    proxies collected in a separate traversal — proxies are only
    identity-stable while referenced, so on large documents a label-side
    ``id()`` set silently stops matching and every wrapped control gets
    flagged. Reproduced via a full document large enough for proxy GC."""
    block = '<div class="g"><label>Field <input type="checkbox" name="f"></label></div>'
    html = f"<!DOCTYPE html><html><body>{block * 200}</body></html>"
    assert controls_missing_accessible_name(html) == []


# --- dangling id references ------------------------------------------------


def test_dangling_label_for_is_reported():
    html = '<label for="nope">X</label><input id="yes" aria-label="x">'
    problems = dangling_id_refs(html)
    assert len(problems) == 1
    assert "nope" in problems[0]


def test_dangling_aria_labelledby_token_is_reported():
    """Multi-token labelledby: each token must resolve individually."""
    html = '<span id="a">A</span><input aria-labelledby="a b" name="q">'
    problems = dangling_id_refs(html)
    assert len(problems) == 1
    assert "'b'" in problems[0]


def test_dangling_aria_describedby_is_reported():
    html = '<input aria-label="q" aria-describedby="hint" name="q">'
    problems = dangling_id_refs(html)
    assert len(problems) == 1
    assert "hint" in problems[0]


def test_resolved_refs_produce_no_problems():
    html = (
        '<span id="lbl">Name</span><span id="hint">Optional</span>'
        '<label for="x">X</label><input id="x">'
        '<input aria-labelledby="lbl" aria-describedby="hint" name="q">'
    )
    assert dangling_id_refs(html) == []


# --- control counting --------------------------------------------------------


def test_count_controls_counts_named_controls_excluding_hidden():
    html = (
        '<input name="a"><select name="b"></select><textarea name="c"></textarea>'
        '<input type="hidden" name="h"><button>x</button>'
    )
    # a, b, c count; hidden and button do not.
    assert count_controls(html) == 3


def test_count_controls_zero_on_control_free_markup():
    assert count_controls("<div><p>No controls here</p></div>") == 0


# --- document detection ------------------------------------------------------


def test_is_full_document():
    assert is_full_document("<!DOCTYPE html><html><body></body></html>")
    assert is_full_document("\n  <!doctype html>\n<html></html>")
    assert is_full_document("<html><body></body></html>")
    assert not is_full_document('<tr><td><input name="a"></td></tr>')
    assert not is_full_document('<div class="card"></div>')
