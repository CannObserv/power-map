"""Tests for src.core.db — schema helpers and ULID generation."""

from src.core.db import generate_id

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

