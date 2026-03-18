"""Tests for PhoneNormalizer."""

import pytest

from src.core.normalizers.phone import PhoneNormalizer


@pytest.fixture
def normalizer():
    return PhoneNormalizer()


def test_us_number(normalizer):
    r = normalizer.normalize("(206) 555-1234")
    assert r.value == "+12065551234"
    assert r.skipped is False


def test_already_e164(normalizer):
    r = normalizer.normalize("+12065551234")
    assert r.value == "+12065551234"


def test_dotted_format(normalizer):
    r = normalizer.normalize("206.555.1234")
    assert r.value == "+12065551234"


def test_null_like_returns_skipped(normalizer):
    for v in (None, "", "N/A", "n/a"):
        r = normalizer.normalize(v)
        assert r.skipped is True
        assert r.value is None


def test_invalid_raises(normalizer):
    with pytest.raises(ValueError, match="invalid phone"):
        normalizer.normalize("not-a-phone")


def test_local_number_raises(normalizer):
    """7-digit local numbers without area code are not valid."""
    with pytest.raises(ValueError, match="invalid phone"):
        normalizer.normalize("555-1234")
