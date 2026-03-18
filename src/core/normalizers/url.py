"""URL normalizer — validates and canonicalizes web URLs."""

from dataclasses import dataclass
from urllib.parse import urlparse, urlunparse

import validators

from src.core.normalizers.base import NormalizationResult, is_null_like


@dataclass
class UrlNormalizer:
    """Validates and canonicalizes URLs (scheme lowercase, host lowercase, no trailing slash)."""

    def normalize(self, raw: str | None) -> NormalizationResult:
        """Return canonical URL string, or skipped result for null-like input.

        Raises:
            ValueError: If *raw* is not a valid URL.
        """
        if is_null_like(raw):
            return NormalizationResult(value=None, skipped=True)
        raw = raw.strip()
        if not validators.url(raw):
            raise ValueError(f"invalid url: {raw!r}")
        parsed = urlparse(raw)
        canonical = urlunparse((
            parsed.scheme.lower(),
            parsed.netloc.lower(),
            parsed.path.rstrip("/") if parsed.path != "/" else "",
            parsed.params,
            parsed.query,
            parsed.fragment,
        ))
        return NormalizationResult(value=canonical)
