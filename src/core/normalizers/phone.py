"""Phone number normalizer — E.164 output via libphonenumber."""

from dataclasses import dataclass

import phonenumbers

from src.core.normalizers.base import NormalizationResult, is_null_like


@dataclass
class PhoneNormalizer:
    """Normalizes raw phone strings to E.164 format.

    Args:
        default_region: ISO 3166-1 alpha-2 hint for numbers without country code.
    """

    default_region: str = "US"

    def normalize(self, raw: str | None) -> NormalizationResult:
        """Return E.164 string, or skipped result for null-like input.

        Raises:
            ValueError: If *raw* is not a parseable, valid phone number.
        """
        if is_null_like(raw):
            return NormalizationResult(value=None, skipped=True)
        raw = raw.strip()
        try:
            parsed = phonenumbers.parse(raw, self.default_region)
        except phonenumbers.NumberParseException:
            raise ValueError(f"invalid phone number: {raw!r}")
        if not phonenumbers.is_valid_number(parsed):
            raise ValueError(f"invalid phone number: {raw!r}")
        return NormalizationResult(
            value=phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164),
        )
