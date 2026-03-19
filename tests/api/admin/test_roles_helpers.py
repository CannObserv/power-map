"""Unit tests for roles admin helpers."""

import pytest

from src.api.admin.roles import _like


def test_like_wraps_with_wildcards():
    assert _like("foo") == "%foo%"


def test_like_escapes_percent():
    assert _like("50%") == "%50\\%%"


def test_like_escapes_underscore():
    assert _like("foo_bar") == "%foo\\_bar%"


def test_like_escapes_backslash():
    assert _like("a\\b") == "%a\\\\b%"


def test_like_escapes_combined():
    # Input with all three special chars
    assert _like("a%b_c\\d") == "%a\\%b\\_c\\\\d%"


def test_like_empty_string():
    assert _like("") == "%%"
