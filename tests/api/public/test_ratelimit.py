"""Unit tests for the per-key token-bucket rate limiter (#292).

Pure unit — no DB, no app. The clock is injected via ``now_s`` so refill math
is deterministic; bucket state is reset by the package-wide autouse fixture in
``conftest.py``.
"""

import pytest

from src.api.public import ratelimit as rl


@pytest.fixture
def small_limits(monkeypatch):
    """Tiny, deterministic limits: read 2/s burst 3; write 1/s burst 2."""
    monkeypatch.setattr(rl, "_READ_REFILL_PER_S", 2.0)
    monkeypatch.setattr(rl, "_READ_BURST", 3)
    monkeypatch.setattr(rl, "_WRITE_REFILL_PER_S", 1.0)
    monkeypatch.setattr(rl, "_WRITE_BURST", 2)


# ---------------------------------------------------------------------------
# kind_for_method
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("method", ["GET", "HEAD"])
def test_read_methods_classify_as_read(method):
    assert rl.kind_for_method(method) == "read"


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
def test_mutating_methods_classify_as_write(method):
    assert rl.kind_for_method(method) == "write"


# ---------------------------------------------------------------------------
# Bucket behavior
# ---------------------------------------------------------------------------


def test_burst_allows_then_denies(small_limits):
    for _ in range(3):
        assert rl.check("key1", "GET", now_s=100.0).allowed
    decision = rl.check("key1", "GET", now_s=100.0)
    assert not decision.allowed


def test_denied_reports_retry_after(small_limits):
    for _ in range(3):
        rl.check("key1", "GET", now_s=100.0)
    decision = rl.check("key1", "GET", now_s=100.0)
    # Bucket empty; refill 2/s -> 1 token in 0.5s -> ceil to 1.
    assert decision.retry_after_s == 1


def test_refill_restores_tokens(small_limits):
    for _ in range(3):
        rl.check("key1", "GET", now_s=100.0)
    assert not rl.check("key1", "GET", now_s=100.0).allowed
    # 1 second at 2 tokens/s -> 2 tokens available again.
    assert rl.check("key1", "GET", now_s=101.0).allowed
    assert rl.check("key1", "GET", now_s=101.0).allowed
    assert not rl.check("key1", "GET", now_s=101.0).allowed


def test_refill_caps_at_burst(small_limits):
    rl.check("key1", "GET", now_s=100.0)
    # A long idle period must not accumulate beyond burst capacity.
    for _ in range(3):
        assert rl.check("key1", "GET", now_s=1000.0).allowed
    assert not rl.check("key1", "GET", now_s=1000.0).allowed


def test_read_and_write_buckets_are_independent(small_limits):
    for _ in range(3):
        assert rl.check("key1", "GET", now_s=100.0).allowed
    assert not rl.check("key1", "GET", now_s=100.0).allowed
    # Write bucket untouched by read exhaustion.
    assert rl.check("key1", "POST", now_s=100.0).allowed


def test_keys_are_isolated(small_limits):
    for _ in range(3):
        rl.check("key1", "GET", now_s=100.0)
    assert not rl.check("key1", "GET", now_s=100.0).allowed
    assert rl.check("key2", "GET", now_s=100.0).allowed


def test_remaining_counts_down(small_limits):
    assert rl.check("key1", "GET", now_s=100.0).remaining == 2
    assert rl.check("key1", "GET", now_s=100.0).remaining == 1
    assert rl.check("key1", "GET", now_s=100.0).remaining == 0


def test_limit_reports_burst_capacity(small_limits):
    assert rl.check("key1", "GET", now_s=100.0).limit == 3
    assert rl.check("key1", "POST", now_s=100.0).limit == 2


def test_allowed_has_zero_retry_after(small_limits):
    assert rl.check("key1", "GET", now_s=100.0).retry_after_s == 0


def test_zero_refill_disables_limiting(small_limits, monkeypatch):
    monkeypatch.setattr(rl, "_READ_REFILL_PER_S", 0.0)
    for _ in range(50):
        assert rl.check("key1", "GET", now_s=100.0).allowed


def test_retry_after_scales_with_deficit(small_limits, monkeypatch):
    monkeypatch.setattr(rl, "_WRITE_REFILL_PER_S", 0.1)
    rl.check("key1", "POST", now_s=100.0)
    rl.check("key1", "POST", now_s=100.0)
    decision = rl.check("key1", "POST", now_s=100.0)
    # Empty bucket at 0.1 tokens/s -> 10s until the next token.
    assert not decision.allowed
    assert decision.retry_after_s == 10
