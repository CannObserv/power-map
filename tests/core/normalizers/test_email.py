"""Tests for EmailNormalizer."""

import pytest

from src.core.normalizers.email import EmailNormalizer


@pytest.fixture
def normalizer():
    return EmailNormalizer()


def test_valid_email(normalizer):
    r = normalizer.normalize("user@example.com")
    assert r.value == "user@example.com"
    assert r.skipped is False


def test_normalizes_domain_case(normalizer):
    r = normalizer.normalize("User@Example.COM")
    assert r.value.endswith("@example.com")


def test_null_like_returns_skipped(normalizer):
    for v in (None, "", "N/A"):
        r = normalizer.normalize(v)
        assert r.skipped is True


def test_invalid_raises(normalizer):
    with pytest.raises(ValueError, match="invalid email"):
        normalizer.normalize("not-an-email")


def test_empty_domain_raises(normalizer):
    with pytest.raises(ValueError, match="invalid email"):
        normalizer.normalize("user@")
