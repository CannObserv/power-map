"""Daily schema-parity audit: prod vs a reference DB (issues #315, #331).

Snapshots every **constraint** (``CHECK`` / ``FOREIGN KEY`` / ``UNIQUE`` / ``PK``,
full ``pg_get_constraintdef``), **function** (``pg_get_functiondef``), and
**trigger** (``pg_get_triggerdef``) on a *reference* DB and on the *target*
(prod), and fails when the target is missing, or disagrees on, any reference
object. Catches the ``CREATE TABLE IF NOT EXISTS`` inline-constraint drift class
(#307→#312 CHECKs, #315's FK ON DELETE action) plus the ``CREATE OR REPLACE``
function/trigger body-drift window (#331) continuously and from *any* source —
manual DDL, a partial migration, a deploy whose ``apply_schema`` no-op'd a new
inline constraint, a hand-applied hotfix — not only at code-review time.

Reference vs target:
    --target-url     default DATABASE_URL             (prod; the DB under audit)
    --reference-url  default PARITY_REFERENCE_URL     (falls back to TEST_DATABASE_URL)

The reference must reflect current ``schema.sql``. The strongest reference is a
DB built from an *empty* schema via ``apply_schema`` (see module docstring in
``src.core.schema_parity`` for the residual gap when it is not). ``sync-schema-
to-do.sh`` keeps the default reference (``co_pm_db_test``) current on deploy.

Function/trigger defs are version-sensitive: ``pg_get_functiondef`` /
``pg_get_triggerdef`` formatting can legitimately differ across PG majors, so on
a major mismatch between reference and target those two kinds are skipped (loud
WARNING) rather than misreported as body drift. Constraints are version-stable
and always diff.

Exits 3 on drift — or on misconfiguration (an empty reference, or a reference
that is the same DB as the target), which would otherwise let the audit pass
vacuously — so the systemd unit shows as failed (visible in ``systemctl
--failed``; a hook for future ``OnFailure=`` alerting) — mirrors
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

from scripts._dsn import default_dsn, echo_target
from src.core.logging import configure_logging, get_logger
from src.core.schema_parity import (
    VERSION_SENSITIVE_KINDS,
    diff_defs,
    format_drift_report,
    snapshot_constraints,
    snapshot_functions,
    snapshot_triggers,
)

logger = get_logger(__name__)

#: Report/diff order: constraints first (always diffed, version-stable), then the
#: version-sensitive kinds. See ``_snapshot_all`` for why the snapshotters are
#: resolved by bare name, not a module-level dict of captured function objects.
_KINDS = ("constraint", "function", "trigger")


async def _snapshot_all(conn: asyncpg.Connection, kinds: tuple[str, ...]) -> dict[str, dict]:
    """Snapshot the requested ``kinds`` on ``conn`` → ``{kind: {key: def}}``.

    Resolves the ``snapshot_*`` names at call time (not a module-level dict of
    captured function objects) so tests can monkeypatch them per-kind. Skipped
    kinds (e.g. version-sensitive ones on a PG-major mismatch) are simply not
    requested, so no wasted ``pg_get_*def`` query runs for them.
    """
    snappers = {
        "constraint": snapshot_constraints,
        "function": snapshot_functions,
        "trigger": snapshot_triggers,
    }
    return {kind: await snappers[kind](conn) for kind in kinds}


def _redact(url: str) -> str:
    """Strip credentials from a DSN for safe logging (``user@host/db``)."""
    p = urlparse(url)
    host = p.hostname or "?"
    db = p.path.lstrip("/") or "?"
    user = p.username or "?"
    return f"{user}@{host}/{db}"


def _db_identity(url: str) -> tuple[str | None, int, str]:
    """Physical database identity ``(host, port, dbname)`` — user/creds excluded.

    Used only for the same-DB guard: two URLs address the same database iff these
    three match, regardless of which *user* connects (this project reaches
    ``co_pm_db_production`` as both the app and the migrations user) or of
    password/sslmode query strings. ``_redact`` is display-only and must not be
    reused here — it includes the user and omits the port, so it both misses
    same-db-different-user and false-trips on same-host-different-port. The port
    defaults to Postgres' 5432 so an implicit and an explicit-default port on the
    same DB compare equal.
    """
    p = urlparse(url)
    return (p.hostname, p.port or 5432, p.path.lstrip("/"))


async def run(*, reference_url: str, target_url: str) -> int:
    """Snapshot both DBs across all kinds, diff, log; return the total drift count.

    Drift count = summed missing-in-target + mismatched across constraints,
    functions, and triggers (``target_only`` is logged but excluded, matching
    ``SchemaObjectDrift.has_drift``). Returns a non-zero sentinel (1) instead of a
    drift count when the audit is misconfigured — an empty reference or a
    reference that is the same DB as the target — so the monitor fails loudly
    rather than passing vacuously (the silent-no-op class #315 targets).

    Version-sensitive kinds (functions, triggers) are skipped with a WARNING when
    reference and target run different PG majors — their ``pg_get_*def``
    formatting can legitimately differ, so a diff there would be a version
    artifact, not drift. Constraints are version-stable and always diff.
    """
    ref_label, tgt_label = _redact(reference_url), _redact(target_url)

    # Reference and target must be distinct DBs, else the audit compares prod to
    # itself and always reports 0 drift. Compare on (host, port, dbname) identity
    # — not the display label — so same-db-different-user (app vs migrations user
    # on the same DB) still trips, and same-host-different-port does not.
    if _db_identity(reference_url) == _db_identity(target_url):
        logger.warning(
            "Schema parity audit MISCONFIGURED — reference and target are the "
            "same database (%s); it would compare prod to itself and never detect "
            "drift. Set PARITY_REFERENCE_URL (or --reference-url) to a distinct "
            "reference DB.",
            tgt_label,
        )
        return 1

    # Open both connections up front and read both server majors *before* any
    # snapshot, so a version-sensitive kind that will be skipped (PG-major
    # mismatch) is never snapshotted on either side — no wasted pg_get_*def query.
    ref_conn = await asyncpg.connect(reference_url)
    try:
        tgt_conn = await asyncpg.connect(target_url)
        try:
            ref_major = ref_conn.get_server_version().major
            tgt_major = tgt_conn.get_server_version().major
            version_mismatch = ref_major != tgt_major

            # Derive the skipped set once, then the diffed set as its complement,
            # so the WARNING log and the actual skip can never diverge.
            skipped_kinds = (
                tuple(k for k in _KINDS if k in VERSION_SENSITIVE_KINDS) if version_mismatch else ()
            )
            diff_kinds = tuple(k for k in _KINDS if k not in skipped_kinds)

            # Log each dropped kind so the gap is visible in the journal rather
            # than silently absent.
            for kind in skipped_kinds:
                logger.warning(
                    "Schema %s parity SKIPPED — reference %s (PG %d) and target %s "
                    "(PG %d) run different PG majors; %s defs are version-formatted, "
                    "so a diff would report version artifacts, not drift. Point the "
                    "reference at a same-major DB to re-enable this check.",
                    kind,
                    ref_label,
                    ref_major,
                    tgt_label,
                    tgt_major,
                    kind,
                )

            reference = await _snapshot_all(ref_conn, diff_kinds)

            # A real schema always has constraints, so an empty reference means a
            # blank or wrong reference DB, not genuine parity — fail loudly.
            if not reference["constraint"]:
                logger.warning(
                    "Schema parity audit MISCONFIGURED — reference %s has no "
                    "constraints (blank or wrong DB); refusing to report parity "
                    "against an empty reference.",
                    ref_label,
                )
                return 1

            target = await _snapshot_all(tgt_conn, diff_kinds)
        finally:
            await tgt_conn.close()
    finally:
        await ref_conn.close()

    total_drift = 0
    for kind in diff_kinds:
        drift = diff_defs(kind=kind, reference=reference[kind], target=target[kind])

        if not drift.has_drift:
            logger.info(
                "Schema %s parity OK — target %s carries all %d reference %s(s) "
                "from %s (%d target-only, not drift)",
                kind,
                tgt_label,
                len(reference[kind]),
                kind,
                ref_label,
                len(drift.target_only),
            )
            continue

        logger.warning(
            "Schema %s DRIFT — target %s diverges from reference %s:\n%s",
            kind,
            tgt_label,
            ref_label,
            format_drift_report(drift, reference=ref_label, target=tgt_label),
        )
        total_drift += drift.drift_count

    return total_drift


def main() -> None:
    """CLI entry point — exits 3 on drift or misconfiguration (systemd failure hook)."""
    configure_logging()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--target-url",
        default=default_dsn(),
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

    # Two connections, so each gets its own labelled line rather than one
    # ambiguous "target:" — add_dsn_args does not fit a domain-named pair.
    echo_target(args.target_url, role="target")
    echo_target(args.reference_url, role="reference")

    drift_count = asyncio.run(run(reference_url=args.reference_url, target_url=args.target_url))
    if drift_count:
        sys.exit(3)


if __name__ == "__main__":
    main()
