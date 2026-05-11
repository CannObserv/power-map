"""Tests for src.core.normalizers.base."""

from src.core.normalizers.base import (
    NormalizationResult,
    is_null_like,
    is_truthy_like,
)


def test_is_null_like_empty():
    assert is_null_like("") is True
    assert is_null_like(None) is True


def test_is_null_like_sentinels():
    for v in ("N/A", "n/a", "NA", "None", "null", "TBD", "unknown", "-", "--"):
        assert is_null_like(v) is True, f"expected {v!r} to be null-like"


def test_is_null_like_real_value():
    assert is_null_like("Acme Corp") is False
    assert is_null_like("(206) 555-1234") is False
    assert is_null_like("user@example.com") is False


def test_normalization_result_defaults():
    r = NormalizationResult(value="foo")
    assert r.skipped is False
    assert r.warnings == []
    assert r.confidence_hint == "unconfirmed"
    assert r.validation_detail is None


def test_normalization_result_skipped():
    r = NormalizationResult(value=None, skipped=True)
    assert r.skipped is True
    assert r.value is None


def test_is_truthy_like_canonical_forms():
    for v in ("1", "true", "yes", "on", "t", "y"):
        assert is_truthy_like(v) is True, f"expected {v!r} truthy"


def test_is_truthy_like_case_insensitive():
    for v in ("TRUE", "True", "YES", "Yes", "Y", "T", "ON"):
        assert is_truthy_like(v) is True, f"expected {v!r} truthy (case-insensitive)"


def test_is_truthy_like_whitespace_stripped():
    for v in (" 1 ", "\ttrue", "yes\n", "  Y"):
        assert is_truthy_like(v) is True, f"expected {v!r} truthy (whitespace-stripped)"


def test_is_truthy_like_falsy_inputs():
    for v in ("", "0", "false", "no", "off", "n", "f", "maybe", "Nope", None):
        assert is_truthy_like(v) is False, f"expected {v!r} not truthy"


def test_is_truthy_like_none_returns_false():
    """None is not truthy. Callers wanting default-True must handle None explicitly."""
    assert is_truthy_like(None) is False
