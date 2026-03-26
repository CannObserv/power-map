"""Email address normalizer — validates and normalizes to canonical form."""

from dataclasses import dataclass

from email_validator import EmailNotValidError
from email_validator import validate_email as _ev_validate

from src.core.normalizers.base import NormalizationResult, is_null_like


@dataclass
class EmailNormalizer:
    """Validates email addresses and normalizes to canonical form.

    Uses the email-validator library, which normalizes the full address
    (local part and domain) per RFC 5321 / SMTPUTF8 rules.
    """

    def normalize(self, raw: str | None) -> NormalizationResult:
        """Return normalized email, or skipped result for null-like input.

        Raises:
            ValueError: If *raw* is not a valid email address.
        """
        if is_null_like(raw):
            return NormalizationResult(value=None, skipped=True)
        raw = raw.strip()
        try:
            info = _ev_validate(raw, check_deliverability=False)
            return NormalizationResult(value=info.normalized)
        except EmailNotValidError as exc:
            raise ValueError(f"invalid email address: {raw!r}") from exc
