"""Tests for src.core.db — schema helpers, validators, and ULID generation."""

import re

import pytest

from src.core.db import (
    generate_id,
    normalize_phone,
    validate_email,
)

# ---------------------------------------------------------------------------
# generate_id
# ---------------------------------------------------------------------------


def test_generate_id_returns_string():
    assert isinstance(generate_id(), str)


def test_generate_id_is_26_chars():
    """ULIDs are 26 characters in Crockford base32."""
    assert len(generate_id()) == 26


def test_generate_id_unique():
    ids = {generate_id() for _ in range(100)}
    assert len(ids) == 100


def test_generate_id_timestamp_nondecreasing():
    """The 10-char timestamp prefix of sequential ULIDs must be non-decreasing.

    The full ULID (timestamp + random) is not guaranteed to be in ascending
    order within the same millisecond, because the random component is
    independently generated each call. Pinning to the timestamp portion
    tests the only guarantee the spec makes: that time moves forward.
    """
    ids = [generate_id() for _ in range(10)]
    timestamps = [uid[:10] for uid in ids]
    assert timestamps == sorted(timestamps)


# ---------------------------------------------------------------------------
# normalize_phone
# ---------------------------------------------------------------------------

E164_RE = re.compile(r"^\+[1-9]\d{6,14}$")


def test_normalize_phone_us_number():
    result = normalize_phone("(206) 555-1234", default_region="US")
    assert result == "+12065551234"
    assert E164_RE.match(result)


def test_normalize_phone_already_e164():
    result = normalize_phone("+12065551234")
    assert result == "+12065551234"


def test_normalize_phone_invalid_raises():
    with pytest.raises(ValueError, match="invalid"):
        normalize_phone("not-a-phone")


def test_normalize_phone_empty_raises():
    with pytest.raises(ValueError, match="invalid"):
        normalize_phone("")


# ---------------------------------------------------------------------------
# validate_email
# ---------------------------------------------------------------------------


def test_validate_email_valid():
    result = validate_email("user@example.com")
    assert result == "user@example.com"


def test_validate_email_normalizes_case():
    """RFC 5321: local part is case-sensitive, domain is not."""
    result = validate_email("User@Example.COM")
    # Domain should be lowercased; local part preserved
    assert result.endswith("@example.com")


def test_validate_email_invalid_raises():
    with pytest.raises(ValueError, match="invalid"):
        validate_email("not-an-email")


def test_validate_email_empty_raises():
    with pytest.raises(ValueError, match="invalid"):
        validate_email("")


def test_validate_email_missing_domain_raises():
    with pytest.raises(ValueError, match="invalid"):
        validate_email("user@")
