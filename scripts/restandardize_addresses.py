"""Re-standardize addresses saved while address-validator was unavailable (#595).

Every addresses row records why it has, or lacks, a standardized form
(addresses.standardization_status). This sweep retries the transient ones and,
on success, writes the standardized form with Accept semantics, folding the row
into a twin the entity already holds (src/core/address_repair.py). It never
selects 'declined' — a curator's "Keep my input" — nor 'rejected' or
'no_result', which would fail the same way again.

The dry run calls address-validator too (it is read-only) and reports what each
candidate would become, plus the merges; only --execute writes, one transaction
per address. It doubles as the on-demand audit: it prints counts per status and
the oldest 'unavailable' row's age, and exits 3 when that age passes 3 days.

Usage:
    uv run python -m scripts.restandardize_addresses                       # dry run
    uv run python -m scripts.restandardize_addresses --execute             # write
    uv run python -m scripts.restandardize_addresses --status local --limit 20

Exit codes:
    0  done; no 'unavailable' row older than 3 days remains
    2  usage error (argparse), or ADDRESS_VALIDATOR_API_KEY is unset
    3  an 'unavailable' row older than 3 days remains (after the writes, with --execute)
"""

import argparse
import asyncio
import sys
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import asyncpg

from scripts._dsn import add_dsn_args, build_parser, resolve_dsn
from src.core.address_repair import CANDIDATE_COLUMNS, apply_result, compose_input, find_twins
from src.core.normalizers.address import (
    STANDARDIZATION_STATUSES,
    FallbackAddressNormalizer,
    get_address_normalizer,
    standardization_status,
)

# Statuses a sweep may select. 'unavailable' is the default; 'local' (a
# local_only import, #402, or a missing key) and 'unsupported' (once the validator
# covers a country) are opt-in. 'declined' is a curator's decision: never.
SWEEPABLE = ("unavailable", "local", "unsupported")
DEFAULT_LIMIT = 100
STALE_AFTER = timedelta(days=3)
EXIT_STALE = 3

# A bulk run waits out address-validator's Retry-After rather than fall back,
# like the CSV import (#597).
_SWEEP_RETRY_AFTER_CAP = 60.0


def positive_int(value: str) -> int:
    """argparse type for ``--limit``: an int of at least 1."""
    try:
        n = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not an integer: {value!r}") from None
    if n < 1:
        raise argparse.ArgumentTypeError(f"must be at least 1, got {n}")
    return n


async def status_counts(conn: asyncpg.Connection) -> dict[str | None, int]:
    """Rows per ``standardization_status`` (None = unknown, pre-#595)."""
    rows = await conn.fetch(
        "SELECT standardization_status AS s, count(*) AS n FROM addresses GROUP BY 1"
    )
    return {r["s"]: r["n"] for r in rows}


async def oldest_unavailable(conn: asyncpg.Connection) -> datetime | None:
    """``checked_at`` of the longest-waiting 'unavailable' row, if any."""
    return await conn.fetchval(
        "SELECT min(COALESCE(standardization_checked_at, created_at)) FROM addresses"
        " WHERE standardization_status = 'unavailable'"
    )


async def fetch_candidates(
    conn: asyncpg.Connection, statuses: tuple[str, ...], limit: int
) -> list[asyncpg.Record]:
    """Rows to retry, longest-waiting first. Refuses any status outside SWEEPABLE."""
    unknown = set(statuses) - set(SWEEPABLE)
    if unknown:
        raise ValueError(f"not sweepable: {sorted(unknown)}")
    return await conn.fetch(
        f"SELECT {CANDIDATE_COLUMNS} FROM addresses"
        " WHERE standardization_status = ANY($1::text[])"
        " ORDER BY standardization_checked_at NULLS FIRST, id"
        " LIMIT $2",
        list(statuses),
        limit,
    )


async def sweep(
    conn: asyncpg.Connection,
    normalizer: FallbackAddressNormalizer,
    candidates: list[asyncpg.Record],
    *,
    execute: bool,
) -> dict[str, int]:
    """Retry each candidate; tally outcomes. Writes only when ``execute``."""
    tally: dict[str, int] = {}

    def bump(key: str, n: int = 1) -> None:
        tally[key] = tally.get(key, 0) + n

    for row in candidates:
        text = compose_input(row)
        if not text:
            bump("skipped_empty")
            continue
        result = await normalizer.normalize(text, country=row["country"] or "US")
        if result.skipped or result.value is None:
            bump("skipped_empty")
            continue
        if execute:
            async with conn.transaction():
                outcome = await apply_result(conn, row["id"], result)
            bump(f"{row['standardization_status']} -> {outcome.status}")
            bump("links_moved", outcome.links_moved)
            bump("links_dropped", outcome.links_dropped)
            bump("rows_deleted", int(outcome.deleted))
            continue
        status = standardization_status(result)
        bump(f"{row['standardization_status']} -> {status}")
        if status == "standardized":
            twins = await find_twins(conn, row["id"], result.value["standardized"])
            bump("links_to_move", sum(not t["duplicate"] for t in twins))
            bump("links_to_drop", sum(bool(t["duplicate"]) for t in twins))
    return tally


def build_normalizer() -> FallbackAddressNormalizer | None:
    """The shared normalizer's config with the bulk Retry-After cap; None without a key."""
    shared = get_address_normalizer()
    if shared.config is None:
        return None
    return FallbackAddressNormalizer(
        config=replace(shared.config, retry_after_cap=_SWEEP_RETRY_AFTER_CAP)
    )


def print_report(counts: dict[str | None, int], oldest: datetime | None, now: datetime) -> bool:
    """Print counts per status and the oldest 'unavailable' age; return True when stale."""
    print("\nAddresses by standardization_status")
    for status in (*STANDARDIZATION_STATUSES, None):
        print(f"  {status or '(unknown)':<14} {counts.get(status, 0):>6}")
    if oldest is None:
        print("\nNo 'unavailable' rows.")
        return False
    age = now - oldest
    stale = age > STALE_AFTER
    print(f"\nOldest 'unavailable' row: {age.days} d {age.seconds // 3600} h old.")
    if stale:
        print(f"STALE: older than {STALE_AFTER.days} days. Run with --execute.")
    return stale


def print_tally(tally: dict[str, int], *, execute: bool) -> None:
    """Print the sweep's per-transition counts."""
    print(f"\nSweep ({'execute' if execute else 'dry run — nothing written'})")
    if not tally:
        print("  no candidates")
    for key in sorted(tally):
        print(f"  {key:<32} {tally[key]:>6}")


async def run(argv: list[str] | None = None) -> int:
    """Parse args, sweep, report; return the exit code."""
    parser = build_parser(__doc__)
    add_dsn_args(parser)
    parser.add_argument(
        "--execute", action="store_true", help="Write the results. Default: dry run."
    )
    parser.add_argument(
        "--status",
        action="append",
        choices=SWEEPABLE,
        help="Status to retry; repeatable. Default: unavailable.",
    )
    parser.add_argument(
        "--limit", type=positive_int, default=DEFAULT_LIMIT, help="Max rows (default 100)."
    )
    args = parser.parse_args(argv)
    dsn = resolve_dsn(args, parser)
    normalizer = build_normalizer()
    if normalizer is None:
        parser.error("ADDRESS_VALIDATOR_API_KEY is not set: a sweep needs the validator")
    statuses = tuple(args.status or ("unavailable",))

    conn = await asyncpg.connect(dsn)
    try:
        candidates = await fetch_candidates(conn, statuses, args.limit)
        print(f"Candidates ({', '.join(statuses)}): {len(candidates)} (limit {args.limit})")
        tally = await sweep(conn, normalizer, candidates, execute=args.execute)
        print_tally(tally, execute=args.execute)
        stale = print_report(
            await status_counts(conn), await oldest_unavailable(conn), datetime.now(UTC)
        )
    finally:
        await conn.close()
    return EXIT_STALE if stale else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(run()))
