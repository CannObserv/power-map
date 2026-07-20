"""Per-key token-bucket rate limiting for the public API (#292).

Two token buckets per API key — ``read`` (GET/HEAD plus the read-semantic POST
endpoints in ``_POST_READ_PATHS``, #310) and ``write`` (everything else) —
enforced at the auth choke point (``src.api.public.deps``), so every
authenticated ``/api/v1/*`` request passes through exactly one ``check()``.

In-process by design: buckets live in a module dict keyed by
``(api_key_id, kind)`` and refill against a monotonic clock. No Redis on this
single-VM stack, and DB-backed counters would add a write per request — the
exact churn #292 is fighting. Production runs ``uvicorn --workers 2``, so each
worker holds its own buckets and the effective ceiling is ~2× the configured
rate; the defaults are sized as a backstop against runaway clients, not a
precise quota. Memory is bounded: only *valid* keys reach the limiter (auth
401s first), so the dict holds at most 2 entries per issued key.

Config via env (parity with ``API_REQUEST_LOG_MAX_PENDING``'s import-time
pattern): ``RATE_LIMIT_{READ,WRITE}_PER_S`` refill rates and
``RATE_LIMIT_{READ,WRITE}_BURST`` bucket capacities. A refill rate <= 0
disables limiting for that kind.
"""

import math
import os
import time
from dataclasses import dataclass

# Defaults sized from the #292 traffic audit: legitimate ingestion peaked at
# ~0.5 read/s and ~0.26 write/s (hourly averages), while the runaway client
# sustained ~4.9 read/s. Read burst 120 lets a full-catalog sync (~1,700
# resources) finish in ~15 min without a single 429.
_READ_REFILL_PER_S = float(os.environ.get("RATE_LIMIT_READ_PER_S", "2"))
_READ_BURST = int(os.environ.get("RATE_LIMIT_READ_BURST", "120"))
_WRITE_REFILL_PER_S = float(os.environ.get("RATE_LIMIT_WRITE_PER_S", "1"))
_WRITE_BURST = int(os.environ.get("RATE_LIMIT_WRITE_BURST", "60"))


@dataclass
class RateLimitDecision:
    """Outcome of a single bucket check, carrying header-ready values."""

    allowed: bool
    limit: int  # bucket burst capacity (X-RateLimit-Limit)
    remaining: int  # whole tokens left after this request (X-RateLimit-Remaining)
    retry_after_s: int  # seconds until the next token; 0 when allowed (Retry-After)


class _Bucket:
    __slots__ = ("tokens", "stamp")

    def __init__(self, tokens: float, stamp: float):
        self.tokens = tokens
        self.stamp = stamp


_buckets: dict[tuple[str, str], _Bucket] = {}


def reset() -> None:
    """Clear all bucket state (tests)."""
    _buckets.clear()


# Read-semantic POST endpoints (#310 CR): scoring/lookup calls that carry a
# request body for size reasons but only *read* — they hold voice_embeddings:read
# and must drain the read bucket, not contend with genuine embedding writes.
_POST_READ_PATHS = frozenset(
    {
        "/api/v1/people/identify",
        "/api/v1/people/verify",
        "/api/v1/people/verify-batch",
        "/api/v1/people/embeddings/presence",
    }
)


def kind_for_request(method: str, path: str | None) -> str:
    """Classify a request into the ``read`` or ``write`` bucket.

    GET/HEAD → read; POST on a ``_POST_READ_PATHS`` route → read; everything
    else — including a missing ``path`` (unit-test seam) — conservatively write.
    """
    m = method.upper()
    if m in ("GET", "HEAD"):
        return "read"
    if m == "POST" and path is not None and (path.rstrip("/") or "/") in _POST_READ_PATHS:
        return "read"
    return "write"


def _config_for(kind: str) -> tuple[float, int]:
    if kind == "read":
        return _READ_REFILL_PER_S, _READ_BURST
    return _WRITE_REFILL_PER_S, _WRITE_BURST


def check(
    api_key_id: str, method: str, path: str | None = None, now_s: float | None = None
) -> RateLimitDecision:
    """Consume one token from the key's bucket for this request; report the outcome.

    ``path`` routes read-semantic POSTs to the read bucket (#310); ``now_s``
    injects a clock for tests; production uses ``time.monotonic()``.
    A denied request consumes nothing. A refill rate <= 0 disables the kind
    entirely (always allowed).
    """
    kind = kind_for_request(method, path)
    refill_per_s, burst = _config_for(kind)
    if refill_per_s <= 0:
        return RateLimitDecision(allowed=True, limit=burst, remaining=burst, retry_after_s=0)

    now = time.monotonic() if now_s is None else now_s
    bucket = _buckets.get((api_key_id, kind))
    if bucket is None:
        bucket = _Bucket(tokens=float(burst), stamp=now)
        _buckets[(api_key_id, kind)] = bucket
    else:
        elapsed = max(0.0, now - bucket.stamp)
        bucket.tokens = min(float(burst), bucket.tokens + elapsed * refill_per_s)
        bucket.stamp = now

    if bucket.tokens >= 1.0:
        bucket.tokens -= 1.0
        return RateLimitDecision(
            allowed=True, limit=burst, remaining=int(bucket.tokens), retry_after_s=0
        )

    retry_after = math.ceil((1.0 - bucket.tokens) / refill_per_s)
    return RateLimitDecision(
        allowed=False, limit=burst, remaining=0, retry_after_s=max(1, retry_after)
    )
