"""The request-log detail's body pretty-printer (#674). Pure unit."""

import pytest

from src.api.admin.activity_requests import _pretty


def test_pretty_indents_a_body():
    assert _pretty('{"a": [1]}') == '{\n  "a": [\n    1\n  ]\n}'


@pytest.mark.parametrize("depth", [1500, 5000])
def test_pretty_shows_a_too_deep_body_raw(depth):
    # json.dumps(indent=2) is the pure-Python encoder: it recursed past the
    # limit near 1,000 levels. Rows logged before #674 may be that deep (CR 5).
    raw = "[" * depth + "]" * depth
    assert _pretty(raw) == raw
