"""Unit tests for the per-key token-bucket rate limiter (#292).

No DB. The clock is injected via ``now_s`` so refill math is deterministic;
bucket state is reset by the package-wide autouse fixture in ``conftest.py``.
The app is imported only for the route-metadata drift guard (#310) — importing
it installs the derived read-semantic POST path set.
"""

import pytest
from fastapi.routing import APIRoute

from src.api.main import app
from src.api.public import ratelimit as rl


@pytest.fixture
def small_limits(monkeypatch):
    """Tiny, deterministic limits: read 2/s burst 3; write 1/s burst 2."""
    monkeypatch.setattr(rl, "_READ_REFILL_PER_S", 2.0)
    monkeypatch.setattr(rl, "_READ_BURST", 3)
    monkeypatch.setattr(rl, "_WRITE_REFILL_PER_S", 1.0)
    monkeypatch.setattr(rl, "_WRITE_BURST", 2)


# ---------------------------------------------------------------------------
# kind_for_request
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("method", ["GET", "HEAD"])
def test_read_methods_classify_as_read(method):
    assert rl.kind_for_request(method, "/api/v1/people") == "read"


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
def test_mutating_methods_classify_as_write(method):
    assert rl.kind_for_request(method, "/api/v1/people/observations") == "write"


@pytest.fixture
def installed_read_paths(monkeypatch):
    """Install a known read-semantic POST path set, isolated per test."""
    monkeypatch.setattr(
        rl,
        "_post_read_paths",
        frozenset({"/api/v1/people/verify", "/api/v1/people/identify"}),
    )


def test_read_semantic_posts_classify_as_read(installed_read_paths):
    """#310 CR: installed scoring/lookup POST paths drain the read bucket."""
    assert rl.kind_for_request("POST", "/api/v1/people/verify") == "read"
    assert rl.kind_for_request("POST", "/api/v1/people/identify") == "read"


def test_uninstalled_post_path_classifies_as_write(installed_read_paths):
    assert rl.kind_for_request("POST", "/api/v1/people/observations") == "write"


def test_set_post_read_paths_installs_frozenset():
    before = rl._post_read_paths
    try:
        rl.set_post_read_paths(["/x", "/y"])
        assert rl.kind_for_request("POST", "/x") == "read"
        assert rl.kind_for_request("POST", "/z") == "write"
    finally:
        rl.set_post_read_paths(before)


def test_app_derives_read_paths_from_route_metadata():
    """Drift guard (#310 CR round 2): the installed set is derived from route
    ``openapi_extra`` markers at app import — renaming a marked route can never
    silently desynchronize a hardcoded list."""
    marked = {
        route.path
        for route in app.routes
        if isinstance(route, APIRoute)
        and "POST" in route.methods
        and (route.openapi_extra or {}).get(rl.BUCKET_EXTRA_KEY) == "read"
    }
    assert marked == {
        "/api/v1/people/identify",
        "/api/v1/people/verify",
        "/api/v1/people/verify-batch",
        "/api/v1/people/embeddings/presence",
    }
    assert rl._post_read_paths == marked


def test_read_semantic_post_tolerates_trailing_slash(installed_read_paths):
    assert rl.kind_for_request("POST", "/api/v1/people/verify/") == "read"


def test_read_semantic_path_requires_post_method():
    """The allowlist is POST-specific — other mutating methods stay write."""
    assert rl.kind_for_request("PUT", "/api/v1/people/verify") == "write"


def test_post_without_path_classifies_as_write():
    """No path context (unit-test seam) — conservative fallback to write."""
    assert rl.kind_for_request("POST", None) == "write"


def test_check_uses_read_bucket_for_read_semantic_post(small_limits):
    """POST /verify consumes read tokens: limit reports the read burst (3)."""
    decision = rl.check("key1", "POST", "/api/v1/people/verify", now_s=100.0)
    assert decision.limit == 3


def test_check_read_semantic_post_shares_read_bucket_with_gets(small_limits):
    for _ in range(2):
        assert rl.check("key1", "GET", now_s=100.0).allowed
    assert rl.check("key1", "POST", "/api/v1/people/verify", now_s=100.0).allowed
    assert not rl.check("key1", "GET", now_s=100.0).allowed
    # Write bucket untouched.
    assert rl.check("key1", "POST", "/api/v1/people/observations", now_s=100.0).allowed


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
