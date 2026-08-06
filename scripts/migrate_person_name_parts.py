"""Phase-3 person_name_parts migration: consume the triaged analyser CSV and
upsert parts via `upsert_or_delete_parts`.

Issue #135. Closes the loop that started with
`scripts/analyse_person_name_parts.py`:

    Phase 2 (analyser)  → CSV bucketed by confidence (trivial / ambiguous / skip)
    Phase 2.5 (operator) → hand-edits the CSV: corrects ambiguous rows,
                           promotes them to 'trivial' (or accepts the
                           ambiguous label and re-runs Phase 3 with
                           `--include-ambiguous`)
    Phase 3 (this)       → reads CSV, calls upsert_or_delete_parts on each
                           selected row, dry-run-by-default

Confidence filter:
    Default is ``{"trivial"}``. Use ``--include-ambiguous`` to also commit
    rows the operator left labelled ``ambiguous``. ``skip`` rows are never
    written — they represent name_types whose parts decomposition is
    semantically meaningless.

The migration runs every selected row's upsert inside a single
savepoint. Any validation error from ``upsert_or_delete_parts`` rolls
the whole batch back, so partial application can't happen.

Usage:
    uv run python -m scripts.migrate_person_name_parts                    # dry run
    uv run python -m scripts.migrate_person_name_parts --execute
    uv run python -m scripts.migrate_person_name_parts --include-ambiguous --execute
    uv run python -m scripts.migrate_person_name_parts -i other.csv

Pre-conditions:
    * `DATABASE_URL` set
    * Phase 1 (`migrate_person_names_locale_script.py --execute`) has run
      so locale/script are populated.
    * Phase 2 (`analyse_person_name_parts.py`) has run; default CSV
      path is `tmp/person_name_parts_analysis.csv` (overridable).
"""

import argparse
import asyncio
import csv
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import asyncpg

from scripts._dsn import add_dsn_args, resolve_dsn
from src.api.admin.people_name_parts import upsert_or_delete_parts
from src.core.logging import configure_logging, get_logger

logger = get_logger(__name__)


DEFAULT_CSV_PATH = Path("tmp/person_name_parts_analysis.csv")


@dataclass
class MigrationStats:
    """Summary returned by `run_migration`."""

    applied: int = 0
    dry_run: bool = True
    skipped_by_confidence: Counter = field(default_factory=Counter)


# ---------------------------------------------------------------------------
# CSV row -> upsert kwargs
# ---------------------------------------------------------------------------


def _split(value: str) -> list[str]:
    """Reverse the analyser's pipe-join. Empty string -> empty list."""
    return value.split("|") if value else []


def _parse_csv_row(row: dict) -> dict:
    """Turn a CSV DictReader row into the kwargs `upsert_or_delete_parts` wants."""
    return {
        "name_id": row["id"],
        "given_names": _split(row["given_names"]),
        "family_names": _split(row["family_names"]),
        "additional_names": _split(row["additional_names"]),
        "honorific_prefix": row["honorific_prefix"] or None,
        "honorific_suffix": row["honorific_suffix"] or None,
        "primary_identifier": row["primary_identifier"] or None,
    }


# ---------------------------------------------------------------------------
# Migration runner
# ---------------------------------------------------------------------------


async def run_migration(
    conn: asyncpg.Connection,
    *,
    csv_path: Path,
    dry_run: bool = True,
    confidence_filter: set[str] | None = None,
) -> MigrationStats:
    """Apply parts upserts for CSV rows in the confidence filter.

    Wraps everything in a single savepoint — atomic on `--execute`,
    silent rollback on dry-run. Caller owns the connection lifecycle
    and any outer transaction (the savepoint nests inside whatever
    transaction state the connection has).

    Raises:
        ValueError: if `upsert_or_delete_parts` rejects any row's data.
            The whole batch rolls back; nothing persists.
        FileNotFoundError: if `csv_path` doesn't exist.
    """
    if confidence_filter is None:
        confidence_filter = {"trivial"}

    stats = MigrationStats(dry_run=dry_run)

    with csv_path.open() as fh:
        rows = list(csv.DictReader(fh))

    sp = conn.transaction()
    await sp.start()
    try:
        for row in rows:
            confidence = row["confidence"]
            if confidence not in confidence_filter:
                stats.skipped_by_confidence[confidence] += 1
                continue
            kwargs = _parse_csv_row(row)
            err = await upsert_or_delete_parts(conn, **kwargs)
            if err is not None:
                # validation error from upsert_or_delete_parts; raise to
                # roll back the entire batch.
                raise ValueError(f"row id={kwargs['name_id']!r}: {err}")
            stats.applied += 1
    except Exception:
        await sp.rollback()
        raise

    if dry_run:
        await sp.rollback()
    else:
        await sp.commit()
    return stats


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


async def _main() -> None:
    configure_logging()
    parser = argparse.ArgumentParser(description=__doc__)
    add_dsn_args(parser)
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Commit changes. Default is dry run (no changes made).",
    )
    parser.add_argument(
        "-i",
        "--input",
        type=Path,
        default=DEFAULT_CSV_PATH,
        help=f"Path to the analyser CSV (default: {DEFAULT_CSV_PATH}).",
    )
    parser.add_argument(
        "--include-ambiguous",
        action="store_true",
        help="Also commit rows labelled `ambiguous` (the operator has "
        "reviewed them). Default filter is `trivial` only.",
    )
    args = parser.parse_args()
    dry_run = not args.execute
    confidence_filter = {"trivial"}
    if args.include_ambiguous:
        confidence_filter.add("ambiguous")

    if not args.input.exists():
        raise SystemExit(
            f"CSV not found: {args.input}. Run "
            "`uv run python -m scripts.analyse_person_name_parts` first."
        )

    # Resolved after input validation — see prune_outbox.
    dsn = resolve_dsn(args, parser)
    conn = await asyncpg.connect(dsn)
    try:
        result = await run_migration(
            conn,
            csv_path=args.input,
            dry_run=dry_run,
            confidence_filter=confidence_filter,
        )
    finally:
        await conn.close()

    mode = "DRY RUN" if result.dry_run else "EXECUTED"
    print(f"\n[{mode}] person_name_parts migration:")
    print(f"  CSV:         {args.input}")
    print(f"  filter:      {sorted(confidence_filter)}")
    print(f"  applied:     {result.applied} rows")
    if result.skipped_by_confidence:
        print("  skipped:")
        for label, count in sorted(result.skipped_by_confidence.items()):
            print(f"      {label:<10} {count:>5}")
    if result.dry_run:
        print("\nRe-run with --execute to apply changes.")


if __name__ == "__main__":
    asyncio.run(_main())
