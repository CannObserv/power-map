"""Identifier normalizer — strips whitespace, checks presence."""

from dataclasses import dataclass

from src.core.normalizers.base import NormalizationResult, is_null_like


@dataclass
class IdentifierNormalizer:
    """Normalizes identifier strings (UBI, WA PDC IDs, etc.) by stripping whitespace."""

    def normalize(self, raw: str | None) -> NormalizationResult:
        """Return stripped identifier, or skipped result for null-like input."""
        if is_null_like(raw):
            return NormalizationResult(value=None, skipped=True)
        return NormalizationResult(value=raw.strip())
