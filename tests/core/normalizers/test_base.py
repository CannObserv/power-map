"""Tests for src.core.normalizers.base."""

from src.core.normalizers.base import NormalizationResult, is_null_like


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
