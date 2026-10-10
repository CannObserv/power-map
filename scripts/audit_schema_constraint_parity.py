"""Daily schema-parity audit: prod vs a reference DB (issues #315, #331, #632).

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

Reference ahead (#632): the default reference is the shared test DB, which
worktrees apply their schema to (``apply-schema.sh --test``) before their PR
deploys. Each object missing in the target is classified against the deployed
``schema.sql`` (``--deployed-schema``, default this checkout's
``src/core/schema.sql``; the unit runs from the main checkout, which is the
deployed commit). Declared there → real drift. Not declared → the reference is
ahead of the deploy: a pending deploy, or a stray branch's apply — logged as a
WARNING naming the objects, not a failure. Matching rules (name-level, with
Postgres' implicit constraint names) are in ``src.core.schema_parity``.

An object that stays ahead for more than ``--escalate-after`` consecutive runs
(default 3; at most one run counts per UTC day) escalates to a failure: the
reference carries a long-lived or abandoned branch's schema, not a deploy that
is due. ``apply-schema.sh --test`` from main does not remove it (the apply is
additive): ship the branch, or drop the named objects from the reference or
rebuild it from an empty schema. The per-object streaks persist in
``--state-file`` (default ``data/schema_parity/reference_ahead.json``); a run
that never reaches the diff leaves them alone, and one that has a streak to keep
(or an unreadable file to replace) but cannot write it fails, since the
escalation would otherwise never fire.

Mismatched definitions are not classified: a branch that changes a function body
still reads as drift until it deploys.

Exit codes:
    0  parity
    3  drift, an escalated reference-ahead object, or misconfiguration (an
       empty reference, a reference that is the same DB as the target, an
       empty or unreadable deployed schema, an unwritable state file) — so the
       systemd unit shows as failed (visible in ``systemctl --failed``; a hook
       for future ``OnFailure=`` alerting) — mirrors
       ``scripts/check_api_anomalies.py``. 3, not 2, stays distinct from
       argparse usage errors.
    4  reference ahead only; the unit's ``SuccessExitStatus=4`` counts it as
       success, so it stays in the journal without turning the unit red.

Read-only on both databases; the only write is the local state file.

Usage:
    uv run python -m scripts.audit_schema_constraint_parity
    uv run python -m scripts.audit_schema_constraint_parity --reference-url "$TEST_DATABASE_URL"
"""

import argparse
import asyncio
import contextlib
import json
import os
import sys
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from urllib.parse import urlparse

import asyncpg

from scripts._dsn import build_parser, default_dsn, echo_target
from src.core.db import SCHEMA_PATH
from src.core.logging import configure_logging, get_logger
from src.core.schema_parity import (
    VERSION_SENSITIVE_KINDS,
    DeployedSchema,
    advance_streaks,
    classify_missing,
    diff_defs,
    format_drift_report,
    snapshot_constraints,
    snapshot_functions,
    snapshot_triggers,
)

logger = get_logger(__name__)

#: The checkout this script runs from; the unit runs it from the deployed main checkout.
DEFAULT_DEPLOYED_SCHEMA = SCHEMA_PATH
DEFAULT_STATE_PATH = (
    Path(__file__).resolve().parents[1] / "data" / "schema_parity" / "reference_ahead.json"
)
DEFAULT_ESCALATE_AFTER = 3
EXIT_FAILURE = 3
EXIT_REFERENCE_AHEAD = 4


@dataclass(frozen=True)
class AuditResult:
    """One run's outcome; ``exit_code`` maps it onto the unit's contract.

    ``ahead`` and ``escalated`` hold ``<kind>.<label>`` strings, the keys of the
    persisted streaks.
    """

    drift_count: int = 0
    ahead: tuple[str, ...] = ()
    escalated: tuple[str, ...] = ()
    misconfigured: bool = False

    @property
    def exit_code(self) -> int:
        """3 on any failure, 4 when the reference is only ahead, else 0."""
        if self.misconfigured or self.drift_count or self.escalated:
            return EXIT_FAILURE
        if self.ahead:
            return EXIT_REFERENCE_AHEAD
        return 0


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


def _is_streak(value: object) -> bool:
    """``{"runs": int, "last_day": str}`` — a JSON ``true`` is not a run count."""
    return (
        isinstance(value, dict)
        and type(value.get("runs")) is int
        and isinstance(value.get("last_day"), str)
    )


def _read_streaks(path: Path) -> dict[str, dict] | None:
    """The last run's per-object streaks: ``{}`` when absent, None when unreadable.

    An unreadable file restarts every streak, delaying an escalation by at most
    ``--escalate-after`` runs; the caller rewrites it.
    """
    try:
        streaks = json.loads(path.read_text())["streaks"]
        if isinstance(streaks, dict) and all(_is_streak(v) for v in streaks.values()):
            return streaks
        raise ValueError('streaks is not a {label: {"runs", "last_day"}} map')
    except FileNotFoundError:
        return {}
    except (OSError, ValueError, KeyError, TypeError) as exc:
        logger.warning(
            "Schema parity state %s unreadable (%s); reference-ahead streaks restart.",
            path,
            exc,
        )
        return None


def _write_streaks(path: Path, streaks: dict[str, dict]) -> bool:
    """Persist this run's streaks; False (logged) when the file cannot be written.

    Written to a sibling temp file and moved into place, so a run killed or failing
    mid-write leaves the previous file whole rather than truncated JSON that would
    restart every streak.
    """
    tmp = path.with_name(f".{path.name}.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps({"streaks": streaks}, indent=2, sort_keys=True) + "\n")
        os.replace(tmp, path)
    except OSError as exc:
        with contextlib.suppress(OSError):
            tmp.unlink(missing_ok=True)
        logger.warning(
            "Schema parity audit MISCONFIGURED — cannot write state %s (%s); without "
            "it a reference-ahead object would never escalate.",
            path,
            exc,
        )
        return False
    return True


async def run(
    *,
    reference_url: str,
    target_url: str,
    deployed: DeployedSchema,
    state_path: Path,
    escalate_after: int,
    today: date | None = None,
) -> AuditResult:
    """Snapshot both DBs across all kinds, diff, classify, log; return the outcome.

    Drift count = summed missing-in-target + mismatched across constraints,
    functions, and triggers (``target_only`` is logged but excluded, matching
    ``SchemaObjectDrift.has_drift``), after ``classify_missing`` moves the
    objects ``deployed`` does not declare to ``ahead``. Each ahead object's
    consecutive-run streak is advanced in ``state_path`` (one run per UTC day,
    ``today`` defaulting to now); one past ``escalate_after`` is ``escalated``.
    A misconfigured audit — an empty reference, a reference that is the same DB
    as the target, or an empty deployed schema, which would pass every missing
    object off as ahead — is reported as such, so the monitor fails loudly rather
    than passing vacuously (the silent-no-op class #315 targets).

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
        return AuditResult(misconfigured=True)

    if deployed.is_empty:
        logger.warning(
            "Schema parity audit MISCONFIGURED — the deployed schema creates no "
            "table (blank or wrong --deployed-schema); every object missing in the "
            "target would read as a pending deploy instead of drift."
        )
        return AuditResult(misconfigured=True)

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
                return AuditResult(misconfigured=True)

            target = await _snapshot_all(tgt_conn, diff_kinds)
        finally:
            await tgt_conn.close()
    finally:
        await ref_conn.close()

    total_drift = 0
    ahead: list[str] = []
    for kind in diff_kinds:
        drift = classify_missing(
            diff_defs(kind=kind, reference=reference[kind], target=target[kind]), deployed
        )
        ahead += [f"{kind}.{k.label}" for k in drift.reference_ahead]

        if not drift.has_drift and not drift.reference_ahead:
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

        report = format_drift_report(drift, reference=ref_label, target=tgt_label)
        if drift.has_drift:
            logger.warning(
                "Schema %s DRIFT — target %s diverges from reference %s:\n%s",
                kind,
                tgt_label,
                ref_label,
                report,
            )
            total_drift += drift.drift_count
        else:
            logger.warning(
                "Schema %s REFERENCE AHEAD — not drift (pending deploy?):\n%s", kind, report
            )

    # Skipped kinds were not looked at, so their streaks carry over untouched.
    read = _read_streaks(state_path)
    previous = read if read is not None else {}
    streaks = {
        label: streak
        for label, streak in previous.items()
        if label.split(".", 1)[0] in skipped_kinds
    }
    streaks |= advance_streaks(previous, ahead, today=today or datetime.now(UTC).date())
    # Nothing tracked before or now, and a sound file: skip the write, so a state
    # file only breaks the run when there is a streak to keep or a bad file to replace.
    needs_write = bool(previous or streaks) or read is None
    if needs_write and not _write_streaks(state_path, streaks):
        return AuditResult(drift_count=total_drift, ahead=tuple(ahead), misconfigured=True)

    escalated = tuple(label for label in ahead if streaks[label]["runs"] > escalate_after)
    if escalated:
        logger.warning(
            "Schema parity ESCALATED — %d object(s) ahead of the deployed schema.sql on "
            "more than %d consecutive daily runs: the reference %s carries a "
            "long-lived or abandoned branch's schema, not a deploy that is due. Ship "
            "the branch, or, once no worktree needs them, drop the objects from the "
            "reference or rebuild it from an empty schema (apply-schema.sh --test is "
            "additive and keeps them):\n%s",
            len(escalated),
            escalate_after,
            ref_label,
            "\n".join(f"  - {label} ({streaks[label]['runs']} runs)" for label in escalated),
        )
    return AuditResult(drift_count=total_drift, ahead=tuple(ahead), escalated=escalated)


def _non_negative_int(text: str) -> int:
    value = int(text)
    if value < 0:
        raise argparse.ArgumentTypeError(f"must be >= 0, got {value}")
    return value


def main() -> None:
    """CLI entry point — exits 3 on failure, 4 on reference ahead only (see docstring)."""
    configure_logging()
    parser = build_parser(__doc__)
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
    parser.add_argument(
        "--deployed-schema",
        type=Path,
        default=DEFAULT_DEPLOYED_SCHEMA,
        help="schema.sql of the deployed commit (default: this checkout's src/core/schema.sql)",
    )
    parser.add_argument(
        "--state-file",
        type=Path,
        default=DEFAULT_STATE_PATH,
        help="per-object reference-ahead run counts (default data/schema_parity/…)",
    )
    parser.add_argument(
        "--escalate-after",
        type=_non_negative_int,
        default=DEFAULT_ESCALATE_AFTER,
        help=(
            "consecutive runs (at most one per UTC day) an object may stay ahead of "
            f"the deployed schema before it fails (default {DEFAULT_ESCALATE_AFTER})"
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

    try:
        deployed = DeployedSchema.from_sql(args.deployed_schema.read_text())
    except (OSError, UnicodeDecodeError) as exc:
        logger.warning(
            "Schema parity audit MISCONFIGURED — cannot read the deployed schema %s (%s).",
            args.deployed_schema,
            exc,
        )
        sys.exit(EXIT_FAILURE)
    result = asyncio.run(
        run(
            reference_url=args.reference_url,
            target_url=args.target_url,
            deployed=deployed,
            state_path=args.state_file,
            escalate_after=args.escalate_after,
        )
    )
    if result.exit_code:
        sys.exit(result.exit_code)


if __name__ == "__main__":
    main()
