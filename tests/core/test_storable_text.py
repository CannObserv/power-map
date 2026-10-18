"""Text Postgres cannot store: NUL and lone surrogates (#663). Pure unit."""

import json

import pytest

from src.core.storable_text import (
    escape_unstorable,
    escape_unstorable_deep,
    find_unstorable,
    has_unstorable,
    json_may_hold_unstorable,
)

# The last is a pair split into code units: UTF-8 cannot encode either half.
UNSTORABLE = ["x\x00y", "\x00", "\ud800", "a\udfffb", "\ud83d" + "\ude00"]
STORABLE = ["", "plain", "\U0001f600", "\\u0000", "\x01\x1f\x7f", "\ufffd"]


@pytest.mark.parametrize("text", UNSTORABLE)
def test_has_unstorable_true(text):
    assert has_unstorable(text)


@pytest.mark.parametrize("text", STORABLE)
def test_has_unstorable_false(text):
    assert not has_unstorable(text)


def test_escape_unstorable_writes_literal_escapes():
    assert escape_unstorable("x\x00y\ud800") == "x\\u0000y\\ud800"


@pytest.mark.parametrize("text", UNSTORABLE)
def test_escaped_text_encodes_and_is_storable(text):
    escaped = escape_unstorable(text)
    escaped.encode("utf-8")
    assert not has_unstorable(escaped)


def test_escape_unstorable_leaves_storable_text_alone():
    for text in STORABLE:
        assert escape_unstorable(text) == text


def test_find_unstorable_none_for_clean_structures():
    assert find_unstorable({"a": ["b", 1, None, {"c": "\U0001f600"}], "d": 2.5}) is None


def test_find_unstorable_returns_path_to_value():
    assert find_unstorable({"ids": ["ok", "x\x00"]}) == (("ids", 1), "x\x00")


def test_find_unstorable_returns_path_to_key():
    # A key is text too: name the key itself, escaped so the loc renders.
    assert find_unstorable({"etags": {"\ud800": "w"}}) == (("etags", "\\ud800"), "\ud800")


def test_find_unstorable_bare_string():
    assert find_unstorable("\x00") == ((), "\x00")


def test_escape_unstorable_deep_walks_keys_and_values():
    got = escape_unstorable_deep({"k\x00": ["\ud800", 1, {"n": None}]})
    assert got == {"k\\u0000": ["\\ud800", 1, {"n": None}]}
    json.dumps(got, ensure_ascii=False).encode("utf-8")


def test_escape_unstorable_deep_handles_tuples():
    assert escape_unstorable_deep(("body", "\x00")) == ("body", "\\u0000")


@pytest.mark.parametrize(
    "raw",
    [
        b'{"q": "x\\u0000y"}',
        b'{"q": "\\ud800"}',
        b'{"q": "\\uDBFF"}',
        b'{"q": "\xed\xa0\x80"}',
        b'{"q": "\x00"}',
    ],
)
def test_prescreen_flags_candidates(raw):
    assert json_may_hold_unstorable(raw)


@pytest.mark.parametrize(
    "raw",
    [
        b'{"embedding": [0.1, -2.5e-3, 3]}',
        b'{"q": "\\u00e9\\u0041"}',
        b'{"q": "\xf0\x9f\x98\x80"}',  # 😀 as UTF-8
        b'{"q": "\xed\x9f\xbf"}',  # U+D7FF, the code point below the range
        b"",
    ],
)
def test_prescreen_passes_ordinary_bodies(raw):
    assert not json_may_hold_unstorable(raw)
