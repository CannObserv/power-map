"""JSON nested too deep to walk recursively (#674). Pure unit."""

import json

import pytest

from src.core.json_depth import (
    MAX_JSON_DEPTH,
    json_depth_exceeds,
    json_may_be_too_deep,
    loads_bounded,
)


def _nested(depth: int, leaf: object = "ok") -> object:
    """``leaf`` inside ``depth`` lists, built without recursion."""
    value = leaf
    for _ in range(depth):
        value = [value]
    return value


@pytest.mark.parametrize(
    ("value", "depth"),
    [
        ("ok", 0),
        (1.5, 0),
        (None, 0),
        ([], 1),
        ({}, 1),
        ({"a": [1, {"b": []}]}, 4),
        (("t", ("u",)), 2),  # a 422 error carries tuples
    ],
)
def test_depth_is_the_count_of_nested_containers(value, depth):
    assert not json_depth_exceeds(value, depth)
    assert json_depth_exceeds(value, depth - 1) == (depth > 0)


def test_the_bound_is_inclusive():
    assert not json_depth_exceeds(_nested(MAX_JSON_DEPTH))
    assert json_depth_exceeds(_nested(MAX_JSON_DEPTH + 1))


def test_the_deepest_branch_counts_not_the_first():
    value = {"shallow": [1], "deep": _nested(MAX_JSON_DEPTH)}
    assert json_depth_exceeds(value)


@pytest.mark.parametrize("depth", [1000, 5000])
def test_any_depth_json_can_parse_is_measured_without_recursion(depth):
    # json.loads parses to about 10,000; a recursive walk fails near 1,000.
    assert json_depth_exceeds(json.loads("[" * depth + "]" * depth))


def test_a_wide_flat_body_is_not_deep():
    assert not json_depth_exceeds({"embedding": [0.1] * 4096, "ids": ["x"] * 1000})


@pytest.mark.parametrize("depth", [0, 1, MAX_JSON_DEPTH, MAX_JSON_DEPTH + 1, 5000])
def test_prescreen_never_misses_a_deep_body(depth):
    raw = json.dumps({"ids": _nested(depth)}).encode()
    if json_depth_exceeds(json.loads(raw)):
        assert json_may_be_too_deep(raw)


def test_prescreen_passes_a_shallow_body():
    assert not json_may_be_too_deep(json.dumps({"ids": ["a", "b"], "n": {"x": 1}}).encode())


def test_prescreen_counts_brackets_inside_strings_as_candidates():
    # A candidate only: the full check on the parsed body decides.
    raw = json.dumps({"s": "[" * (MAX_JSON_DEPTH + 1)}).encode()
    assert json_may_be_too_deep(raw)
    assert not json_depth_exceeds(json.loads(raw))


def test_loads_bounded_parses_json_at_the_bound():
    text = json.dumps(_nested(MAX_JSON_DEPTH))
    assert loads_bounded(text) == _nested(MAX_JSON_DEPTH)


@pytest.mark.parametrize("depth", [MAX_JSON_DEPTH + 1, 5000, 100_000])
def test_loads_bounded_refuses_json_past_the_bound_as_value_error(depth):
    # 100,000 is past json.loads's own limit: its RecursionError is no ValueError.
    with pytest.raises(ValueError, match="nested deeper"):
        loads_bounded("[" * depth + "]" * depth)


def test_loads_bounded_keeps_json_decode_errors():
    with pytest.raises(json.JSONDecodeError):
        loads_bounded("{bad json")
