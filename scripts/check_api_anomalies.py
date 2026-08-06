"""Hourly per-key request-volume anomaly check (issue #294).

Queries ``api_request_log`` for the trailing hour, grouped per API key, and
emits a journal ``WARNING`` for every key at or above the threshold. Wired to
``infra/power-map-anomaly.timer`` (hourly, mirrors ``power-map-prune.timer``).

Exits 3 when any key is anomalous so the systemd unit shows as failed —
visible in ``systemctl --failed`` and a hook for future ``OnFailure=``
alerting. Exit 3 (not 2) keeps anomalies distinguishable from argparse usage
errors. A journal WARNING alone has the same visibility problem the #292 /
2026-07-11 incidents had (nobody watches the journal); the admin per-key panel
is the human-facing layer.

Threshold default (5000/hr) is deliberately below the rate-limit ceiling —
rationale in ``src.core.anomaly``. A threshold <= 0 disables the check
(mirrors the ``RATE_LIMIT_*`` "refill <= 0 disables" convention).

Usage:
    uv run python -m scripts.check_api_anomalies                    # env/default threshold
    uv run python -m scripts.check_api_anomalies --threshold 5000
    uv run python -m scripts.check_api_anomalies --threshold 0      # disabled, exit 0
"""

import argparse
import asyncio
import sys

import asyncpg

from scripts._dsn import add_dsn_args, resolve_dsn
from src.core.anomaly import HOURLY_REQUEST_THRESHOLD, KeyActivity, key_activity
from src.core.logging import configure_logging, get_logger

logger = get_logger(__name__)


def report(activities: list[KeyActivity], *, threshold: int) -> int:
    """Log a WARNING per anomalous key; return how many keys were anomalous.

    Internal to ``run()``, which owns the window (trailing hour) and guards
    the disabled case — callers must not pass a threshold <= 0.
    """
    anomalous = [a for a in activities if a.request_count >= threshold]
    for a in anomalous:
        label = a.key_label if a.api_key_id else "unauthenticated (no valid key)"
        logger.warning(
            "API key anomaly: %s (%s) made %d requests in the checked window "
            "(threshold %d; %d throttled with 429)",
            label,
            a.api_key_id or "-",
            a.request_count,
            threshold,
            a.throttled_count,
        )
    if not anomalous:
        total = sum(a.request_count for a in activities)
        logger.info(
            "No per-key anomalies — %d request(s) across %d key(s) in the checked window "
            "(threshold %d)",
            total,
            len(activities),
            threshold,
        )
    return len(anomalous)


async def run(dsn: str, *, threshold: int) -> int:
    """Fetch the trailing hour's per-key activity and report; return anomaly count.

    ``main`` short-circuits a threshold <= 0 before resolving a target, so this
    is reached only when the check is enabled.
    """
    conn = await asyncpg.connect(dsn)
    try:
        activities = await key_activity(conn, window_hours=1)
    finally:
        await conn.close()
    return report(activities, threshold=threshold)


def main() -> None:
    """CLI entry point — exits 3 when any key is anomalous (systemd failure hook)."""
    configure_logging()
    parser = argparse.ArgumentParser(description=__doc__)
    add_dsn_args(parser)
    parser.add_argument(
        "--threshold",
        type=int,
        default=HOURLY_REQUEST_THRESHOLD,
        help=(
            "Requests per key per hour at/above which to WARN; <= 0 disables "
            f"(default {HOURLY_REQUEST_THRESHOLD}; env API_ANOMALY_HOURLY_THRESHOLD)"
        ),
    )
    args = parser.parse_args()
    if args.threshold <= 0:
        # Resolved before the target, deliberately: echoing a database this run
        # will never contact would be a false attribution in the journal.
        logger.info("Anomaly check disabled (threshold %d <= 0)", args.threshold)
        return

    dsn = resolve_dsn(args, parser)
    anomalous = asyncio.run(run(dsn, threshold=args.threshold))
    if anomalous:
        sys.exit(3)


if __name__ == "__main__":
    main()
