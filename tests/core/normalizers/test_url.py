"""Tests for UrlNormalizer."""

import pytest

from src.core.normalizers.url import UrlNormalizer


@pytest.fixture
def normalizer():
    return UrlNormalizer()


def test_valid_https(normalizer):
    r = normalizer.normalize("https://example.com")
    assert r.value == "https://example.com"
    assert r.skipped is False


def test_lowercases_scheme_and_host(normalizer):
    r = normalizer.normalize("HTTPS://Example.COM/path")
    assert r.value.startswith("https://example.com")


def test_strips_trailing_slash_on_root(normalizer):
    r = normalizer.normalize("https://example.com/")
    assert r.value == "https://example.com"


def test_preserves_path(normalizer):
    r = normalizer.normalize("https://example.com/path/to/page")
    assert r.value == "https://example.com/path/to/page"


def test_strips_trailing_slash_on_path(normalizer):
    r = normalizer.normalize("https://example.com/path/")
    assert r.value == "https://example.com/path"


def test_null_like_skipped(normalizer):
    for v in (None, "", "N/A"):
        r = normalizer.normalize(v)
        assert r.skipped is True


def test_invalid_raises(normalizer):
    with pytest.raises(ValueError, match="invalid url"):
        normalizer.normalize("not a url")


def test_bare_domain_raises(normalizer):
    with pytest.raises(ValueError, match="invalid url"):
        normalizer.normalize("example.com")
