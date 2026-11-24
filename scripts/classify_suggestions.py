"""Record org-kind suggestions from a source into classification_suggestions (#877).

Sources only *suggest*; nothing here sets an org's kind (an operator does, in
the admin Kind panel). Phase 1 has one source:

- ``rules``: the kind an org's identifiers imply
  (``entity_identifier_types.implies_org_kind_id``). A row is written only when
  an org's answer changed: a new kind, ``none`` on conflicting identifiers, or
  ``none`` ("no rule applies") when an earlier answer lost its identifier. An
  absent rules row means "no rule". Archived orgs are skipped.

Phase 2 adds ``--source jev``.

Dry run by default: it echoes the target and prints what it would write.

Usage:
    uv run python -m scripts.classify_suggestions --source rules            # dry run
    uv run python -m scripts.classify_suggestions --source rules --execute  # write
"""

import asyncio
from collections import Counter
from dataclasses import dataclass, field

import asyncpg

from scripts._dsn import add_dsn_args, build_parser, resolve_dsn
from src.core.classification import RulesChange, apply_rules, plan_rules
from src.core.logging import configure_logging, get_logger

logger = get_logger(__name__)

SOURCES = ("rules",)


@dataclass
class Report:
    """What a run planned and wrote."""

    changes: list[RulesChange]
    written: int = 0
    counts: Counter = field(default_factory=Counter)


async def classify(conn: asyncpg.Connection, *, source: str, execute: bool) -> Report:
    """Plan (and with ``execute``, write) a source's suggestion rows."""
    if source not in SOURCES:
        raise ValueError(f"unknown source {source!r}; phase 1 knows {', '.join(SOURCES)}")
    changes = await plan_rules(conn)
    report = Report(changes=changes, counts=Counter(c.value for c in changes))
    if execute:
        report.written = await apply_rules(conn, changes)
    return report


async def run(dsn: str, *, source: str, execute: bool) -> None:
    """Connect, run one source, and log the summary."""
    conn = await asyncpg.connect(dsn)
    try:
        report = await classify(conn, source=source, execute=execute)
    finally:
        await conn.close()
    breakdown = ", ".join(f"{k}={n}" for k, n in sorted(report.counts.items())) or "nothing to do"
    replaced = sum(1 for c in report.changes if c.previous is not None)
    if execute:
        logger.info(
            "%s: wrote %d row(s) (%d replacing an earlier answer): %s",
            source,
            report.written,
            replaced,
            breakdown,
        )
    else:
        logger.info(
            "%s: dry run — would write %d row(s) (%d replacing an earlier answer): %s."
            " Pass --execute to write.",
            source,
            len(report.changes),
            replaced,
            breakdown,
        )


def main() -> None:
    """CLI entry point."""
    configure_logging()
    parser = build_parser(__doc__)
    add_dsn_args(parser)
    parser.add_argument("--source", choices=SOURCES, required=True, help="Suggestion source")
    parser.add_argument("--execute", action="store_true", help="Write rows (default: dry run)")
    args = parser.parse_args()
    dsn = resolve_dsn(args, parser)
    asyncio.run(run(dsn, source=args.source, execute=args.execute))


if __name__ == "__main__":
    main()
