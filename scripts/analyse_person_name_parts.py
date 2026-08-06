"""Phase-2 analyser: emit a triage CSV for `person_name_parts` decomposition.

Issue #135. Read-only — writes a CSV the operator hand-triages before any
parts are persisted. Pairs with `scripts/migrate_person_name_parts.py`
(forthcoming) which consumes the triaged CSV.

For every `person_names` row the analyser:
  1. Calls `src.core.normalizers.person_name.suggest_parts(...)`.
  2. Writes one CSV row with the row's identifying columns + the
     suggestion's confidence, reasons, and pre-decomposed fields.

Non-decomposable name_types (`initials`, `mrz`, `reading`, `romanization`)
emit a row with ``confidence='skip'`` so they can be filtered out trivially.
Non-public-visibility rows (`legal_only`, `hidden`, deadnames) are
**included** with the visibility column populated — per #135 the operator
must review them separately for legal-context implications, but the
suggestion itself is allowed.

Usage:
    uv run python -m scripts.analyse_person_name_parts \\
        --output ./tmp/parts-analysis.csv

Pre-conditions:
    * `DATABASE_URL` set
    * `apply_schema` run on the target DB (the analyser reads existing
      `person_names` rows)
"""

import argparse
import asyncio
import csv
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import asyncpg

from scripts._dsn import add_dsn_args, resolve_dsn
from src.core.logging import configure_logging, get_logger
from src.core.normalizers.person_name import PartsSuggestion, suggest_parts

logger = get_logger(__name__)


CSV_COLUMNS: tuple[str, ...] = (
    # Identity / context
    "id",
    "person_id",
    "name",
    "name_type",
    "locale",
    "script",
    "visibility",
    # Suggestion
    "confidence",
    "reasons",
    # Parts (in canonical print order)
    "honorific_prefix",
    "given_names",
    "additional_names",
    "family_names",
    "honorific_suffix",
    "primary_identifier",
)


@dataclass
class AnalysisStats:
    """Summary returned by `run_analysis`."""

    rows_analysed: int = 0
    bucket_counts: Counter = field(default_factory=Counter)


def _join(values: list[str]) -> str:
    """Join an array column for CSV. ``|`` separator avoids CSV quoting."""
    return "|".join(values)


def _format_csv_row(row: dict | asyncpg.Record, suggestion: PartsSuggestion) -> dict:
    """Turn a DB row + a PartsSuggestion into a CSV-ready dict."""
    return {
        "id": row["id"],
        "person_id": row["person_id"],
        "name": row["name"],
        "name_type": row["name_type"],
        "locale": row["locale"] or "",
        "script": row["script"] or "",
        "visibility": row["visibility"],
        "confidence": suggestion.confidence,
        "reasons": _join(suggestion.reasons),
        "honorific_prefix": suggestion.honorific_prefix or "",
        "given_names": _join(suggestion.given_names),
        "additional_names": _join(suggestion.additional_names),
        "family_names": _join(suggestion.family_names),
        "honorific_suffix": suggestion.honorific_suffix or "",
        "primary_identifier": suggestion.primary_identifier or "",
    }


async def run_analysis(
    conn: asyncpg.Connection,
    *,
    output_path: Path,
) -> AnalysisStats:
    """Walk every `person_names` row, suggest parts, write CSV.

    The CSV is the input to a follow-up migration; this function only
    reads the DB. Includes legal_only / hidden rows so the operator can
    triage them. The caller owns the connection lifecycle.
    """
    rows = await conn.fetch(
        "SELECT id, person_id, name, name_type, locale, script, visibility FROM person_names"
    )
    stats = AnalysisStats()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(CSV_COLUMNS))
        writer.writeheader()
        for r in rows:
            # Locale/script may be NULL for rows that haven't been backfilled
            # yet; suggest_parts only branches on script for the skip set,
            # so NULL → no Latn → 'skip' with reason 'unsupported-script:'.
            # In practice Phase 1 should run first.
            sug = suggest_parts(
                r["name"],
                locale=r["locale"] or "",
                script=r["script"] or "",
                name_type=r["name_type"],
            )
            writer.writerow(_format_csv_row(r, sug))
            stats.rows_analysed += 1
            stats.bucket_counts[sug.confidence] += 1
    logger.info(
        "analysis: wrote %d rows to %s — buckets=%s",
        stats.rows_analysed,
        output_path,
        dict(stats.bucket_counts),
    )
    return stats


async def _main() -> None:
    configure_logging()
    parser = argparse.ArgumentParser(description=__doc__)
    add_dsn_args(parser)
    parser.add_argument(
        "--output",
        "-o",
        type=Path,
        default=Path("tmp/person_name_parts_analysis.csv"),
        help="Output CSV path (default: tmp/person_name_parts_analysis.csv)",
    )
    args = parser.parse_args()
    dsn = resolve_dsn(args, parser)

    conn = await asyncpg.connect(dsn)
    try:
        result = await run_analysis(conn, output_path=args.output)
    finally:
        await conn.close()

    print(f"\nWrote {result.rows_analysed} rows to {args.output}")
    print("\nConfidence buckets:")
    for bucket in ("trivial", "ambiguous", "skip"):
        print(f"  {bucket:<10} {result.bucket_counts[bucket]:>5}")


if __name__ == "__main__":
    asyncio.run(_main())
