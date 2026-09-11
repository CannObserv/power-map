"""Apply the desired state to PM — dry run by default (#499).

Step 3 of the #490 PM-side chain, after the puller (#496), the PM-table export
and the build (#497): diff `data/desired_state/` against live Postgres and
report the minimal changes — or, with `--execute`, write them in one verified
transaction. Row scope is the live `producer_crosswalk`; column scope is the
ownership manifest; the Python knows neither WA nor a table name.

Every run writes `data/applier/<run-id>/{diff.jsonl,summary.json,summary.md}`
and appends a line to `data/applier/ledger.jsonl`. `--execute` is offered only
after `streak` consecutive clean dry runs carrying this run's diff digest. A
refused `--execute` is recorded as `refused`, not as a dry run: an attempt at
the gate does not count towards opening it. A diff holding an actionable
producer merge is a merge phase (#514): its verdict weighs merges, conflicts and
stale only, and its execute folds the merges and writes nothing else.

Exit codes: 0 a dry run completed (verdict clean or blocked — both recorded,
neither fails the timer) or an execute applied and verified; 1 an execute was
refused or rolled back; 2 usage; 3 a person is needed now — a stale desired
state, a missing table, a load error.

Usage:
    uv run --group mapping "${env_args[@]}" python -m scripts.apply_desired_state
    uv run --group mapping "${env_args[@]}" python -m scripts.apply_desired_state --execute
    uv run --group mapping "${env_args[@]}" python -m scripts.apply_desired_state \\
        --execute --allow-creates 4 --max-updates 60
    uv run --group mapping "${env_args[@]}" python -m scripts.apply_desired_state \\
        --execute --allow-merges 1 --streak 1     # a merge phase: merges only
"""

import argparse
import asyncio
import dataclasses
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import asyncpg

from scripts._dsn import add_dsn_args, resolve_dsn
from src.core.ingestion.applier import ApplierError, DesiredState, diff_desired
from src.core.ingestion.applier_pg import PostgresLiveStore
from src.core.ingestion.applier_report import (
    LEDGER,
    Verdict,
    append_ledger,
    diff_digest,
    ledger_line,
    may_execute,
    read_ledger,
    run_id_for,
    verdict_for,
    write_report,
)
from src.core.ingestion.applier_write import VerificationFailed, apply_diff
from src.core.ingestion.crosswalk import PRODUCER_SOURCE
from src.core.ingestion.mapping import load_manifest
from src.core.ingestion.mapping.manifest import Thresholds
from src.core.logging import configure_logging, get_logger

logger = get_logger(__name__)

DEFAULT_DESIRED = "data/desired_state"
DEFAULT_OUT = "data/applier"
SOURCE = PRODUCER_SOURCE  # the seed's value; a mismatch here scopes zero rows

EXIT_OK = 0
EXIT_REFUSED = 1
EXIT_NEEDS_A_PERSON = 3


def thresholds_with(
    base: Thresholds | None = None,
    *,
    allow_creates: int | None = None,
    allow_merges: int | None = None,
    max_updates: int | None = None,
) -> Thresholds:
    """The manifest's thresholds with this run's overrides."""
    base = base if base is not None else load_manifest().thresholds
    changes = {}
    if allow_creates is not None:
        changes["creates"] = allow_creates
    if allow_merges is not None:
        changes["merges"] = allow_merges
    if max_updates is not None:
        changes["updates"] = max_updates
    return dataclasses.replace(base, **changes)


def _at_least(minimum: int) -> Callable[[str], int]:
    """An argparse type for a count flag that must not fall below ``minimum`` (CR 2).

    `--streak 0` opened the gate on an empty ledger, and a negative threshold
    blocks a run that has nothing to block. Both are caught at parse time, so
    neither reaches a connection.
    """

    def count(raw: str) -> int:
        # Named for argparse, which builds "invalid <name> value" from it (CR 16).
        value = int(raw)
        if value < minimum:
            raise argparse.ArgumentTypeError(f"must be {minimum} or more, not {value}")
        return value

    return count


def _exceeded(verdict: Verdict) -> str:
    return ", ".join(f"{k} {n} > {limit}" for k, (n, limit) in verdict.exceeded.items())


async def run(
    conn,
    *,
    desired: Path,
    out: Path,
    execute: bool,
    thresholds: Thresholds | None = None,
    streak: int | None = None,
    store_factory: Callable = PostgresLiveStore,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> int:
    """One run: diff, verdict, artifact, ledger — and, gated, the write. Returns the exit code."""
    manifest = load_manifest()
    thresholds = thresholds if thresholds is not None else manifest.thresholds
    streak = streak if streak is not None else manifest.streak
    state = DesiredState.load(desired, manifest)
    store = store_factory(conn)
    started = now()
    run_id = run_id_for(started)
    out = Path(out)
    ledger_path = out / LEDGER

    diff = await diff_desired(state, manifest, store, source=SOURCE)
    verdict = verdict_for(diff, thresholds)
    digest = diff_digest(diff)
    mode, code = "dry", EXIT_OK

    if execute:
        ok, why = may_execute(read_ledger(ledger_path), digest=digest, streak=streak)
        if verdict.verdict != "clean":
            ok, why = False, f"this run is {verdict.verdict} ({_exceeded(verdict)})"
        if not ok:
            logger.error("execute refused: %s — recorded as a refused attempt", why)
            mode, code = "refused", EXIT_REFUSED
        else:
            mode = "execute"

            async def rediff(minted):
                return await diff_desired(state, manifest, store, source=SOURCE, minted=minted)

            try:
                result = await apply_diff(diff, manifest, conn, source=SOURCE, rediff=rediff)
                logger.info(
                    "applied %d statement(s), %d entity(ies) minted; verified in-transaction",
                    result.written,
                    len(result.minted),
                )
            except (VerificationFailed, asyncpg.PostgresError) as exc:
                # A trigger or constraint (the org-cycle guard, a unique index)
                # is a rollback like a failed verification: nothing landed.
                logger.error("rolled back: %s", exc)
                verdict = dataclasses.replace(verdict, verdict="rolled_back")
                code = EXIT_REFUSED

    summary = write_report(
        out / run_id,
        run_id=run_id,
        mode=mode,
        diff=diff,
        verdict=verdict,
        thresholds=thresholds,
        build_info=state.build_info,
        source=SOURCE,
        started_at=started,
        finished_at=now(),
    )
    append_ledger(ledger_path, ledger_line(summary))
    _log_summary(summary, out / run_id, ledger_path, streak)
    if verdict.verdict == "stale" and code == EXIT_OK:
        code = EXIT_NEEDS_A_PERSON
    return code


def _log_summary(summary: dict, run_dir: Path, ledger_path: Path, streak: int) -> None:
    counts = ", ".join(f"{k} {n}" for k, n in summary["counts"].items() if n)
    logger.info(
        "run %s (%s): verdict %s — %s",
        summary["run_id"],
        summary["mode"],
        summary["verdict"],
        counts,
    )
    if summary["exceeded"]:
        over = ", ".join(f"{k} {n} > {limit}" for k, (n, limit) in summary["exceeded"].items())
        logger.warning("  thresholds exceeded: %s", over)
    if summary["phase"] == "merge":
        deferred = ", ".join(f"{k} {n}" for k, n in summary["deferred"].items()) or "nothing"
        logger.info(
            "  merge phase: %d merge(s) to apply first; deferred to the next diff: %s",
            len(summary["merges"]),
            deferred,
        )
    logger.info("  report: %s", run_dir)
    if summary["mode"] == "dry":
        ok, why = may_execute(read_ledger(ledger_path), digest=summary["digest"], streak=streak)
        logger.info("  --execute %s: %s", "would be offered" if ok else "not yet offered", why)


async def _run_against(dsn: str, **kw) -> int:
    conn = await asyncpg.connect(dsn)
    try:
        return await run(conn, **kw)
    finally:
        await conn.close()


def main(argv: list[str] | None = None) -> int:
    """Entry point; returns the process exit code."""
    configure_logging()
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    add_dsn_args(parser)
    parser.add_argument(
        "--execute", action="store_true", help="Write the diff (default: dry run, report only)"
    )
    parser.add_argument(
        "--desired", type=Path, default=Path(DEFAULT_DESIRED), help=f"default {DEFAULT_DESIRED}"
    )
    parser.add_argument(
        "--out", type=Path, default=Path(DEFAULT_OUT), help=f"Run artifacts (default {DEFAULT_OUT})"
    )
    parser.add_argument(
        "--allow-creates",
        type=_at_least(0),
        default=None,
        metavar="N",
        help="Raise the creates threshold for this run (manifest default 0)",
    )
    parser.add_argument(
        "--allow-merges",
        type=_at_least(0),
        default=None,
        metavar="N",
        help="Raise the merges threshold for this run (manifest default 0; #514)",
    )
    parser.add_argument(
        "--max-updates",
        type=_at_least(0),
        default=None,
        metavar="N",
        help="Cap updates + inserts for this run (manifest default: unlimited)",
    )
    parser.add_argument(
        "--streak",
        type=_at_least(1),
        default=None,
        metavar="N",
        help="Clean dry runs required before --execute (manifest default)",
    )
    args = parser.parse_args(argv)
    dsn = resolve_dsn(args, parser)
    thresholds = thresholds_with(
        allow_creates=args.allow_creates,
        allow_merges=args.allow_merges,
        max_updates=args.max_updates,
    )
    streak = args.streak if args.streak is not None else load_manifest().streak
    try:
        return asyncio.run(
            _run_against(
                dsn,
                desired=args.desired,
                out=args.out,
                execute=args.execute,
                thresholds=thresholds,
                streak=streak,
            )
        )
    except ApplierError as exc:
        logger.error("%s", exc)
        return EXIT_NEEDS_A_PERSON


if __name__ == "__main__":
    sys.exit(main())
