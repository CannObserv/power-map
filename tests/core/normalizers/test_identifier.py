"""Tests for IdentifierNormalizer."""

import pytest

from src.core.normalizers.identifier import IdentifierNormalizer


@pytest.fixture
def normalizer():
    return IdentifierNormalizer()


def test_strips_whitespace(normalizer):
    r = normalizer.normalize("  603 123 456  ")
    assert r.value == "603 123 456"


def test_null_like_skipped(normalizer):
    for v in (None, "", "N/A"):
        r = normalizer.normalize(v)
        assert r.skipped is True


def test_valid_ubi(normalizer):
    r = normalizer.normalize("603 123 456")
    assert r.value == "603 123 456"
    assert r.skipped is False
