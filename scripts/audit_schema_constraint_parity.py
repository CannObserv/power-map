"""Daily schema constraint-parity audit: prod vs a fresh reference (issue #315).

Snapshots every constraint (``CHECK`` / ``FOREIGN KEY`` / ``UNIQUE`` / ``PK``,
full ``pg_get_constraintdef``) on a *reference* DB and on the *target* (prod),
and fails when the target is missing, or disagrees on, any reference constraint.
Catches the ``CREATE TABLE IF NOT EXISTS`` drift class (#307→#312 CHECKs, #315's
FK ON DELETE action) continuously and from *any* source — manual DDL, a partial
migration, a deploy whose ``apply_schema`` no-op'd a new inline constraint — not
only at code-review time.

Reference vs target:
    --target-url     default DATABASE_URL             (prod; the DB under audit)
    --reference-url  default PARITY_REFERENCE_URL     (falls back to TEST_DATABASE_URL)

The reference must reflect current ``schema.sql``. The strongest reference is a
DB built from an *empty* schema via ``apply_schema`` (see module docstring in
``src.core.schema_parity`` for the residual gap when it is not). ``sync-schema-
to-do.sh`` keeps the default reference (``co_pm_db_test``) current on deploy.

Exits 3 when any drift is found so the systemd unit shows as failed (visible in
``systemctl --failed``; a hook for future ``OnFailure=`` alerting) — mirrors
``scripts/check_api_anomalies.py``. Exit 3 (not 2) stays distinct from argparse
usage errors. Read-only: never writes to either database.

Usage:
    uv run python -m scripts.audit_schema_constraint_parity
    uv run python -m scripts.audit_schema_constraint_parity --reference-url "$TEST_DATABASE_URL"
"""

import argparse
import asyncio
import os
import sys
from urllib.parse import urlparse

import asyncpg

from src.core.logging import configure_logging, get_logger
from src.core.schema_parity import (
    diff_constraints,
    format_drift_report,
    snapshot_constraints,
)

logger = get_logger(__name__)


def _redact(url: str) -> str:
    """Strip credentials from a DSN for safe logging (``user@host/db``)."""
    p = urlparse(url)
    host = p.hostname or "?"
    db = p.path.lstrip("/") or "?"
    user = p.username or "?"
    return f"{user}@{host}/{db}"


async def run(*, reference_url: str, target_url: str) -> int:
    """Snapshot both DBs, diff, log; return the drift-constraint count.

    Drift count = missing-in-target + mismatched (``target_only`` is logged but
    excluded, matching ``ConstraintDrift.has_drift``).
    """
    ref_label, tgt_label = _redact(reference_url), _redact(target_url)

    ref_conn = await asyncpg.connect(reference_url)
    try:
        reference = await snapshot_constraints(ref_conn)
    finally:
        await ref_conn.close()

    tgt_conn = await asyncpg.connect(target_url)
    try:
        target = await snapshot_constraints(tgt_conn)
    finally:
        await tgt_conn.close()

    drift = diff_constraints(reference=reference, target=target)

    if not drift.has_drift:
        logger.info(
            "Schema constraint parity OK — target %s carries all %d reference "
            "constraint(s) from %s (%d target-only, not drift)",
            tgt_label,
            len(reference),
            ref_label,
            len(drift.target_only),
        )
        return 0

    logger.warning(
        "Schema constraint DRIFT — target %s diverges from reference %s:\n%s",
        tgt_label,
        ref_label,
        format_drift_report(drift, reference=ref_label, target=tgt_label),
    )
    return len(drift.missing_in_target) + len(drift.mismatched)


def main() -> None:
    """CLI entry point — exits 3 when any drift is found (systemd failure hook)."""
    configure_logging()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--target-url",
        default=os.environ.get("DATABASE_URL"),
        help="DB under audit (default DATABASE_URL)",
    )
    parser.add_argument(
        "--reference-url",
        default=(os.environ.get("PARITY_REFERENCE_URL") or os.environ.get("TEST_DATABASE_URL")),
        help=(
            "Reference DB reflecting current schema.sql "
            "(default PARITY_REFERENCE_URL, then TEST_DATABASE_URL)"
        ),
    )
    args = parser.parse_args()
    if not args.target_url:
        parser.error("no target: set DATABASE_URL or pass --target-url")
    if not args.reference_url:
        parser.error(
            "no reference: set PARITY_REFERENCE_URL or TEST_DATABASE_URL, or pass --reference-url"
        )

    drift_count = asyncio.run(run(reference_url=args.reference_url, target_url=args.target_url))
    if drift_count:
        sys.exit(3)


if __name__ == "__main__":
    main()
