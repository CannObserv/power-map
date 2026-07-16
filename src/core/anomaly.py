"""Per-key request-volume surfacing over ``api_request_log`` (issue #294).

Two consumers share the same aggregate:

- the admin Activity → Requests per-key panel (``src.api.admin.activity_requests``),
- the hourly anomaly check (``scripts/check_api_anomalies.py`` under
  ``infra/power-map-anomaly.timer``).

Motivation: two runaway-client incidents (1.7M requests over 4 days each, #292
and the 2026-07-11 recurrence) were invisible until manually audited, despite
``api_request_log`` holding everything needed.

Threshold semantics: ``HOURLY_REQUEST_THRESHOLD`` is deliberately *below* the
rate-limit ceiling (2 workers × 2 req/s ≈ 14.4k/hr sustained at defaults) —
the 2026-07-11 runaway ran at ~17.5k/hr, only ~20% above that ceiling, so a
"well above ceiling" threshold would have missed it. 5000/hr sits above every
legitimate hour observed to date (~2k peak) and also catches 429 retry-hammering,
since throttled requests are captured too.
"""

import os
from dataclasses import dataclass
from datetime import datetime

import asyncpg

#: Requests per key per hour at (or above) which a key is anomalous. <= 0
#: disables anomaly surfacing (mirrors the RATE_LIMIT_* "refill <= 0 disables"
#: convention). Env-tunable; see AGENTS.md § Environment Variables.
HOURLY_REQUEST_THRESHOLD = int(os.environ.get("API_ANOMALY_HOURLY_THRESHOLD", "5000"))


@dataclass(frozen=True)
class KeyActivity:
    """Aggregate traffic for one API key (or the NULL/unauthenticated bucket)."""

    api_key_id: str | None
    key_label: str | None
    request_count: int
    throttled_count: int  # 429 responses within the window
    last_seen: datetime


_KEY_ACTIVITY_SQL = """
SELECT
    r.api_key_id,
    k.label AS key_label,
    COUNT(*) AS request_count,
    COUNT(*) FILTER (WHERE r.status_code = 429) AS throttled_count,
    MAX(r.occurred_at) AS last_seen
FROM api_request_log r
LEFT JOIN api_keys k ON k.id = r.api_key_id
WHERE r.occurred_at >= NOW() - make_interval(hours => $1::int)
GROUP BY r.api_key_id, k.label
ORDER BY request_count DESC, r.api_key_id
"""


async def key_activity(conn: asyncpg.Connection, *, window_hours: int) -> list[KeyActivity]:
    """Per-key request counts over the trailing window, hottest key first.

    Rows with a NULL ``api_key_id`` (unauthenticated / invalid key) aggregate
    into a single bucket with ``api_key_id=None``. The planner serves the
    ``occurred_at`` range via ``idx_arl_occurred`` and hash-aggregates the keys.
    """
    rows = await conn.fetch(_KEY_ACTIVITY_SQL, window_hours)
    return [
        KeyActivity(
            api_key_id=r["api_key_id"],
            key_label=r["key_label"],
            request_count=r["request_count"],
            throttled_count=r["throttled_count"],
            last_seen=r["last_seen"],
        )
        for r in rows
    ]
