"""Bulk-accept org-kind suggestions, as an operator (#877 phase 3, design D7).

Sets ``organizations.org_kind_id`` for the backlog through
``classification.record_decision``, the only writer, one org per transaction,
recorded as ``--operator``'s ``accept`` with the note ``bulk accept <model>``.
Of the active orgs with no kind that no operator has touched (no manual row at
all, so a Clear in the Kind panel keeps its org out), it accepts:

- a **rules** kind, by definition (decision C, 2026-11-25), unless Jev answers
  anything else;
- a **Jev** kind at or above ``JEV_CONFIDENCE_THRESHOLD``, on a kind trusted
  for the answering model (``classification.TRUSTED_JEV_KINDS``, from the gold
  set), when no rules kind applies.

Everything else waits for the review queue, counted by reason: ``disagree``,
``conflict``, ``low``, ``untrusted``, ``none``, ``unanswered``.

Dry run by default: it echoes the target and prints the counts, the Jev backlog
still unanswered, and the **skim list**: each rules acceptance outside
``legislature``, by name, with its gold-set label when it has one (decision C).
Names go to this terminal only; post aggregates. ``--execute`` refuses while
Jev has orgs to answer (a rules kind can't be checked against an answer that
hasn't arrived) unless ``--allow-unanswered``.

Usage:
    uv run python -m scripts.accept_classification_suggestions --field org_kind --operator EMAIL
    uv run python -m scripts.accept_classification_suggestions --field org_kind --operator EMAIL \\
        --execute
"""

import asyncio
import logging
import sys
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field

import asyncpg

from scripts._dsn import add_dsn_args, build_parser, resolve_dsn
from scripts.org_kind_gold import GOLD_PATH, read_gold
from src.core import classification as cls
from src.core import jev_client as jc
from src.core.logging import configure_logging, get_logger

logger = get_logger(__name__)

FIELDS = ("org_kind",)
# record_decision logs each decision at INFO; a run makes ~1,400 of them, and
# the manual rows are the audit record. The run's own summary line remains.
QUIET_LOGGERS = ("src.core.classification",)
# Rules kinds accepted without a skim: the legislature's own identifiers.
UNSKIMMED_RULES_KINDS = frozenset({"legislature"})


class UsageError(Exception):
    """A run that must not proceed; the message is for the operator."""


@dataclass(frozen=True)
class SkimRow:
    """A rules acceptance for the operator to skim: the org's name and its gold label."""

    org_id: str
    name: str | None
    kind: str
    gold: str | None


@dataclass
class Report:
    """What a run planned and, with ``--execute``, wrote."""

    items: list[cls.BulkItem]
    unanswered: int
    skim: list[SkimRow] = field(default_factory=list)
    result: cls.BulkResult | None = None


def summarize(items: Iterable[cls.BulkItem]) -> tuple[Counter, Counter]:
    """Acceptances by ``(source, kind)``, and the queue by reason."""
    to_accept: Counter = Counter()
    queue: Counter = Counter()
    for i in items:
        if i.source:
            to_accept[(i.source, i.kind)] += 1
        else:
            queue[i.reason] += 1
    return to_accept, queue


def skim_items(items: Iterable[cls.BulkItem]) -> list[cls.BulkItem]:
    """The rules acceptances decision C asks the operator to skim."""
    return [i for i in items if i.source == "rules" and i.kind not in UNSKIMMED_RULES_KINDS]


async def _operator_id(db: asyncpg.Connection, email: str) -> str:
    uid = await db.fetchval("SELECT id FROM app_users WHERE lower(email) = lower($1)", email)
    if uid is None:
        raise UsageError(f"no admin user with email {email!r}: sign in to the admin once first")
    return uid


async def _skim_rows(
    db: asyncpg.Connection, items: list[cls.BulkItem], gold: dict[str, str]
) -> list[SkimRow]:
    names = {
        r["organization_id"]: r["display_name"]
        for r in await db.fetch(
            "SELECT organization_id, display_name FROM v_org_display_names"
            " WHERE organization_id = ANY($1::text[])",
            [i.org_id for i in items],
        )
    }
    return [SkimRow(i.org_id, names.get(i.org_id), i.kind, gold.get(i.org_id)) for i in items]


async def accept(
    db: asyncpg.Connection,
    *,
    operator: str,
    execute: bool,
    allow_unanswered: bool,
    threshold: float,
    gold: dict[str, str],
) -> Report:
    """Plan the bulk accept and, with *execute*, run it as *operator*."""
    user_id = await _operator_id(db, operator)
    items = await cls.plan_bulk_accept(db, threshold=threshold)
    unanswered = await cls.jev_backlog_count(db, jc.get_model())
    report = Report(
        items=items,
        unanswered=unanswered,
        skim=await _skim_rows(db, skim_items(items), gold),
    )
    if not execute:
        return report
    if unanswered and not allow_unanswered:
        raise UsageError(
            f"{unanswered} orgs are still unanswered by Jev; wait for the backlog to drain,"
            " or pass --allow-unanswered"
        )
    # Re-planned under the drain lock, so the acceptances match what is stored.
    ran = await cls.run_bulk_accept(db, threshold=threshold, user_id=user_id)
    if ran is None:
        raise UsageError("a classification drain is running; try again in a minute")
    report.items, report.result = ran
    return report


def report_lines(report: Report, *, execute: bool) -> list[str]:
    """The operator's summary. Only the skim list names orgs."""
    to_accept, queue = summarize(report.items)
    lines = [
        f"{'EXECUTE' if execute else 'dry run'}: {len(report.items)} untouched orgs with no kind;"
        f" Jev backlog still unanswered: {report.unanswered}",
        f"accept ({to_accept.total()}):",
        *(f"  {src:<5} {kind:<20} {n}" for (src, kind), n in sorted(to_accept.items())),
        f"review queue ({queue.total()}):",
        *(f"  {reason:<12} {queue[reason]}" for reason in cls.QUEUE_REASONS if queue[reason]),
        f"skim: rules acceptances outside the legislature ({len(report.skim)}):",
        *(
            f"  {r.kind:<16} {r.name or '(no name)'}  [{r.org_id}]"
            + (f"  gold: {r.gold}" if r.gold else "  not in the gold set")
            for r in sorted(report.skim, key=lambda r: (r.kind, r.name or ""))
        ),
    ]
    if report.result is not None:
        accepted, skipped = report.result.accepted, report.result.skipped
        lines.append(f"accepted: {accepted.total()}; skipped: {len(skipped)}")
        lines += [f"  skipped {org_id}: {why}" for org_id, why in skipped]
    return lines


def quiet_decision_logs() -> None:
    """Raise the per-decision loggers to WARNING, so the summary isn't buried."""
    for name in QUIET_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)


async def run(dsn: str, *, operator: str, execute: bool, allow_unanswered: bool) -> int:
    """Connect, plan (and run), print the summary; exit 2 on a refused run."""
    quiet_decision_logs()
    gold = dict(read_gold(GOLD_PATH))
    conn = await asyncpg.connect(dsn)
    try:
        report = await accept(
            conn,
            operator=operator,
            execute=execute,
            allow_unanswered=allow_unanswered,
            threshold=jc.confidence_threshold(),
            gold=gold,
        )
    except UsageError as e:
        print(f"refused: {e}", file=sys.stderr)
        return 2
    finally:
        await conn.close()
    print("\n".join(report_lines(report, execute=execute)))
    if report.result is not None:
        logger.info(
            "bulk accept: %d accepted, %d skipped",
            report.result.accepted.total(),
            len(report.result.skipped),
        )
    return 0


def main() -> None:
    """CLI entry point."""
    configure_logging()
    parser = build_parser(__doc__)
    add_dsn_args(parser)
    parser.add_argument("--field", choices=FIELDS, required=True, help="Classified field")
    parser.add_argument("--operator", required=True, metavar="EMAIL", help="Who is accepting")
    parser.add_argument("--execute", action="store_true", help="Accept (default: dry run)")
    parser.add_argument(
        "--allow-unanswered",
        action="store_true",
        help="Execute even while Jev has orgs to answer",
    )
    args = parser.parse_args()
    dsn = resolve_dsn(args, parser)
    sys.exit(
        asyncio.run(
            run(
                dsn,
                operator=args.operator,
                execute=args.execute,
                allow_unanswered=args.allow_unanswered,
            )
        )
    )


if __name__ == "__main__":
    main()
