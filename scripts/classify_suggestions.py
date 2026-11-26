"""Record org-kind suggestions into classification_suggestions (#877).

Sources only *suggest*; nothing here sets an org's kind (an operator does, in
the admin Kind panel, or with the bulk-accept script). Two sources:

- ``--source rules``: the kind an org's identifiers imply
  (``entity_identifier_types.implies_org_kind_id``). A row is written only when
  an org's answer changed. Archived orgs are skipped.
- ``--source all``: the drain (``src.core.classification.drain``), which runs
  rules and then asks TypeSafe Jev about the active orgs with no live answer
  from the pinned model, at most ``--limit`` (default ``JEV_MAX_PER_RUN``).
  It asks only with ``JEV_SUGGESTIONS_ENABLED`` on. ``--reask-ids FILE`` asks
  again about the org ids in FILE (one per line), replacing their live
  answers and skipping any org an operator has decided.

Dry run by default: it echoes the target and prints what it would write and
roughly what Jev would cost. ``--execute`` writes (and calls Jev).

The daily ``power-map-classification.timer`` runs ``--source all --execute
--origin timer --limit 200``. Its DB connect and its Jev calls retry
transient failures on one #833 budget. Exit 3 (``jev_failed``) when a Jev
failure is permanent (no key, a 4xx, a malformed reply, another model), or
when Jev failed and no run has stored an answer for ``STALE_AFTER``: one
blip is not an alert, an outage is.

Usage:
    uv run python -m scripts.classify_suggestions --source rules            # dry run
    uv run python -m scripts.classify_suggestions --source all              # backlog, est. tokens
    uv run python -m scripts.classify_suggestions --source all --execute    # drain
    uv run python -m scripts.classify_suggestions --source all --reask-ids ids.txt --execute
"""

import asyncio
import sys
from collections import Counter
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

import asyncpg

from scripts._dsn import add_dsn_args, build_parser, resolve_dsn
from src.core import classification as cls
from src.core import jev_client as jc
from src.core.classification import RulesChange, plan_rules
from src.core.logging import configure_logging, get_logger
from src.core.retry import Backoff, connect_with_retry, retry_async

logger = get_logger(__name__)

SOURCES = ("rules", "all")
ORIGINS = ("cli", "timer")
EXIT_JEV_FAILED = 3
STALE_AFTER = timedelta(hours=48)
# A rough per-org request size for the dry run's estimate (the spike: ~755).
EST_TOKENS_PER_ORG = 800
# Per Jev request from this script: bounded so the timer's worst case fits
# its TimeoutStartSec (tests/test_infra_retry.py).
JEV_TIMEOUT = 30.0
JEV_ATTEMPTS = 3
# A drain already running (the API's drainer, ≤ 50 orgs) makes this one
# `busy`. Wait it out so the timer's check-in reflects a drain that ran.
BUSY_RETRIES = 6
BUSY_WAIT = 10.0


@dataclass
class Report:
    """What a run planned and wrote."""

    changes: list[RulesChange]
    written: int = 0
    counts: Counter = field(default_factory=Counter)
    drain: cls.DrainResult | None = None
    jev_enabled: bool = False
    backlog: int | None = None
    would_ask: int | None = None
    est_tokens: int | None = None
    # Rules were skipped: a drain held the lock (and applies rules itself).
    busy: bool = False


def _record_rules(report: Report, applied: list[RulesChange] | None) -> None:
    """Fold :func:`cls.apply_rules_locked`'s result into *report* (None: busy)."""
    if applied is None:
        report.busy = True
        report.changes, report.counts, report.written = [], Counter(), 0
        return
    report.changes = applied
    report.counts = Counter(c.value for c in applied)
    report.written = len(applied)


def bounded_ask(backoff: Backoff) -> cls.AskFn:
    """``jc.ask_kind`` with this script's bounds, transient failures retried on *backoff*."""

    async def ask(org, crit, **kw):
        return await retry_async(
            lambda: jc.ask_kind(org, crit, timeout=JEV_TIMEOUT, max_attempts=JEV_ATTEMPTS, **kw),
            transient=lambda e: isinstance(e, jc.JevError) and e.transient,
            backoff=backoff,
            label="Jev request",
        )

    return ask


def verdict(result: cls.DrainResult | None, last_answer: datetime | None, now: datetime) -> int:
    """The timer's exit: 3 on a permanent Jev failure or a failing, stale Jev; else 0."""
    if result is None or not result.failed:
        return 0
    if result.permanent:
        return EXIT_JEV_FAILED
    if last_answer is None or now - last_answer > STALE_AFTER:
        return EXIT_JEV_FAILED
    return 0


async def classify(
    conn: asyncpg.Connection,
    *,
    source: str,
    execute: bool,
    origin: str = "cli",
    ask: cls.AskFn = jc.ask_kind,
    limit: int | None = None,
    reask_ids: Sequence[str] | None = None,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> Report:
    """Plan (and with *execute*, write) a source's suggestion rows.

    A drain that finds another holding the lock is retried ``BUSY_RETRIES``
    times, ``BUSY_WAIT`` seconds apart; still busy, the report says skipped.
    """
    if source not in SOURCES:
        raise ValueError(f"unknown source {source!r}; known: {', '.join(SOURCES)}")
    if reask_ids is not None and source != "all":
        raise ValueError("--reask-ids needs --source all")
    changes = await plan_rules(conn)
    report = Report(changes=changes, counts=Counter(c.value for c in changes))
    if source == "rules":
        if execute:
            _record_rules(report, await cls.apply_rules_locked(conn))
        return report

    report.jev_enabled = jc.is_enabled()
    cap = limit or jc.max_per_run()
    if reask_ids is not None:
        eligible = await cls.reask_eligible(conn, reask_ids, skip_decided=True)
        report.backlog = len(eligible)
        report.would_ask = len(eligible)
    else:
        report.backlog = await cls.jev_backlog_count(conn, jc.get_model())
        report.would_ask = min(report.backlog, cap)
    report.est_tokens = report.would_ask * EST_TOKENS_PER_ORG
    if not execute:
        return report
    if reask_ids is not None:
        _record_rules(report, await cls.apply_rules_locked(conn))
        report.drain = await cls.reask(conn, reask_ids, origin=origin, ask=ask, skip_decided=True)
        report.drain.rules_written = report.written
    else:
        for attempt in range(BUSY_RETRIES + 1):
            report.drain = await cls.drain(conn, origin=origin, ask=ask, limit=cap)
            if report.drain.status != "busy" or attempt == BUSY_RETRIES:
                break
            await sleep(BUSY_WAIT)
        if report.drain.status == "busy":
            _record_rules(report, None)
        else:
            report.written = report.drain.rules_written
    return report


def _log(report: Report, *, source: str, execute: bool) -> None:
    breakdown = ", ".join(f"{k}={n}" for k, n in sorted(report.counts.items())) or "no change"
    replaced = sum(1 for c in report.changes if c.previous is not None)
    if report.busy:
        logger.warning("rules: skipped — a drain holds the lock and applies rules itself")
    elif execute:
        logger.info(
            "rules: wrote %d row(s) (%d replacing): %s", report.written, replaced, breakdown
        )
    else:
        logger.info(
            "rules: dry run — would write %d row(s) (%d replacing): %s",
            len(report.changes),
            replaced,
            breakdown,
        )
    if source != "all":
        return
    d = report.drain
    if d is not None and d.status == "busy":
        logger.warning(
            "jev: skipped — another drain held the lock for %ds", BUSY_RETRIES * BUSY_WAIT
        )
    elif d is None:
        logger.info(
            "jev: dry run — %s; backlog %d, would ask %d (~%d input tokens)."
            " Pass --execute to write.",
            "enabled" if report.jev_enabled else "DISABLED (JEV_SUGGESTIONS_ENABLED off)",
            report.backlog,
            report.would_ask,
            report.est_tokens,
        )
    else:
        logger.info(
            "jev: %s — asked %d, answered %d, failed %d, %d input tokens%s",
            d.status,
            d.asked,
            d.answered,
            d.failed,
            d.input_tokens,
            f" (last error: {d.error})" if d.error else "",
        )


async def run(
    dsn: str,
    *,
    source: str,
    execute: bool,
    origin: str,
    limit: int | None,
    reask_ids: Sequence[str] | None,
) -> int:
    """Connect, run, log the summary, and return the exit code."""
    backoff = Backoff()
    conn = await connect_with_retry(dsn, backoff=backoff)
    try:
        report = await classify(
            conn,
            source=source,
            execute=execute,
            origin=origin,
            ask=bounded_ask(backoff),
            limit=limit,
            reask_ids=reask_ids,
        )
        last = await cls.last_answered_at(conn) if report.drain is not None else None
    finally:
        await conn.close()
    _log(report, source=source, execute=execute)
    code = verdict(report.drain, last, datetime.now(UTC))
    if code:
        logger.error("jev_failed: Jev is failing (%s)", report.drain.error)
    return code


def _read_ids(path: str) -> list[str]:
    lines = Path(path).read_text().splitlines()
    return [s for s in (line.strip() for line in lines) if s and not s.startswith("#")]


def main() -> None:
    """CLI entry point."""
    configure_logging()
    parser = build_parser(__doc__)
    add_dsn_args(parser)
    parser.add_argument("--source", choices=SOURCES, required=True, help="Suggestion source")
    parser.add_argument("--execute", action="store_true", help="Write rows (default: dry run)")
    parser.add_argument(
        "--origin", choices=ORIGINS, default="cli", help="Run row origin (the unit: timer)"
    )
    parser.add_argument("--limit", type=int, help="Orgs asked (default JEV_MAX_PER_RUN)")
    parser.add_argument("--reask-ids", metavar="FILE", help="Ask again about these org ids")
    args = parser.parse_args()
    if args.limit is not None and args.limit < 1:
        parser.error("--limit must be at least 1")
    dsn = resolve_dsn(args, parser)
    reask_ids = _read_ids(args.reask_ids) if args.reask_ids else None
    sys.exit(
        asyncio.run(
            run(
                dsn,
                source=args.source,
                execute=args.execute,
                origin=args.origin,
                limit=args.limit,
                reask_ids=reask_ids,
            )
        )
    )


if __name__ == "__main__":
    main()
