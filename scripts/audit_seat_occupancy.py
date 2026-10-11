"""Daily ratchet: two people holding one single-seat role at the same time (#506).

No constraint forbids concurrent occupancy — at-large seats need it (#302) — so
nothing else notices two live holders of a seat that takes one. This audit reads
``role_types.occupancy`` and reports every pair of live assignments that:

- sits on one live role whose type is ``single``,
- belongs to two different people (one person twice is #311's duplicate audit),
- overlaps **strictly**: each starts before the other ends. A span that ends the
  day the next begins is a same-day handoff (mid-term succession), not a finding.

No span is invented: a row with an unknown start, or an unknown end that is not
``is_current``, is out of scope. A current holder runs open-ended.

It is a **ratchet**, not a zero gate. ``BASELINE`` is the count measured on
production on 2026-10-10 — 56 pairs, 55 once #639 landed, listed on usa-wa#489
and broken down in ``docs/AUDITS.md`` — and the run exits 3 only when the count
rises above it.
A lower count passes with a WARNING naming the number to lower ``BASELINE``
to. Every pair is logged each run, so a day-over-day journal diff names the
pair a failure added. A count cannot see a fix and a new defect landing
together; the pair log can. Read-only. Exit 3 (not 2) stays distinct from
argparse usage errors.

Usage:
    uv run python -m scripts.audit_seat_occupancy          # DATABASE_URL
    uv run python -m scripts.audit_seat_occupancy --test   # TEST_DATABASE_URL
    uv run python -m scripts.audit_seat_occupancy --baseline 0   # every pair fails
"""

import asyncio
import sys

import asyncpg

from scripts._dsn import add_dsn_args, build_parser, resolve_dsn
from src.core.logging import configure_logging, get_logger

logger = get_logger(__name__)

#: Strictly overlapping pairs on production: 56 on 2026-10-10 (#506), 55 after #639.
#: Lower it as triage lands; never raise it to clear a failure.
BASELINE = 55

_OVERLAPS_SQL = """
WITH live AS (
    SELECT ra.id, ra.person_id, ra.role_id, ra.start_date, ra.end_date,
           COALESCE(ra.end_date, 'infinity'::date) AS until
    FROM role_assignments ra
    JOIN roles r       ON r.id = ra.role_id AND r.archived_at IS NULL
    JOIN role_types rt ON rt.id = r.role_type_id AND rt.occupancy = 'single'
    WHERE ra.archived_at IS NULL
      AND ra.start_date IS NOT NULL
      AND (ra.end_date IS NOT NULL OR ra.is_current)
)
SELECT a.role_id, r.title AS role_title,
       a.id AS a_id, a.person_id AS a_person_id, pa.display_name AS a_name,
       a.start_date AS a_start, a.end_date AS a_end,
       b.id AS b_id, b.person_id AS b_person_id, pb.display_name AS b_name,
       b.start_date AS b_start, b.end_date AS b_end
FROM live a
JOIN live b  ON b.role_id = a.role_id AND a.id < b.id AND b.person_id <> a.person_id
JOIN roles r ON r.id = a.role_id
LEFT JOIN v_person_display_names pa ON pa.person_id = a.person_id
LEFT JOIN v_person_display_names pb ON pb.person_id = b.person_id
WHERE a.start_date < b.until AND b.start_date < a.until
ORDER BY r.title, a.role_id, a.id, b.id
"""


async def find_overlaps(conn: asyncpg.Connection) -> list[asyncpg.Record]:
    """Every strictly overlapping pair of live holders on a single-seat role."""
    return await conn.fetch(_OVERLAPS_SQL)


def exit_code(count: int, *, baseline: int) -> int:
    """3 when the count rose above the baseline, else 0."""
    return 3 if count > baseline else 0


def _holder(name: str | None, assignment_id: str, start, end) -> str:
    """``Name (assignment_id) start→end``; the id alone when the person has no name."""
    who = f"{name} ({assignment_id})" if name else assignment_id
    return f"{who} {start}→{end or 'open'}"


async def audit(conn: asyncpg.Connection, *, baseline: int = BASELINE) -> int:
    """Log every pair and the ratchet verdict; return the exit code (0 or 3)."""
    pairs = await find_overlaps(conn)
    for p in pairs:
        logger.info(
            "seat overlap: %s (%s) — %s || %s",
            p["role_title"],
            p["role_id"],
            _holder(p["a_name"], p["a_id"], p["a_start"], p["a_end"]),
            _holder(p["b_name"], p["b_id"], p["b_start"], p["b_end"]),
        )
    count = len(pairs)
    code = exit_code(count, baseline=baseline)
    if code:
        logger.warning(
            "seat occupancy: %d pairs, above baseline %d — a new concurrent holder of a"
            " single seat; diff the pair log against the last passing run (docs/AUDITS.md)",
            count,
            baseline,
        )
    elif count < baseline:
        # Still exit 0, but a WARNING: a stale baseline is the ratchet's other signal,
        # and at INFO it would drown in the pair log.
        logger.warning(
            "seat occupancy: %d pairs, below baseline %d — lower BASELINE to %d",
            count,
            baseline,
            count,
        )
    else:
        logger.info("seat occupancy: %d pairs, at baseline %d", count, baseline)
    return code


async def _run(database_url: str, baseline: int) -> int:
    conn = await asyncpg.connect(database_url)
    try:
        return await audit(conn, baseline=baseline)
    finally:
        await conn.close()


def main() -> None:
    parser = build_parser(__doc__)
    add_dsn_args(parser)
    parser.add_argument(
        "--baseline",
        type=int,
        default=BASELINE,
        help=f"fail only above this many pairs (default {BASELINE}; 0 = zero gate)",
    )
    args = parser.parse_args()
    dsn = resolve_dsn(args, parser)

    configure_logging()
    sys.exit(asyncio.run(_run(dsn, args.baseline)))


if __name__ == "__main__":
    main()
