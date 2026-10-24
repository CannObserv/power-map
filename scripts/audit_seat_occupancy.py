"""Daily ratchet: more principals holding a seat than it can seat (#506, #671).

No constraint forbids concurrent occupancy — at-large seats need it (#302) — so
nothing else notices a seat holding more people than it lawfully can. This audit
applies usa-wa's occupancy predicate (its spec D7, may tier — PM stores outer
bounds only), clause for clause with its ``seat_overlap_pairs('may')``:

- **Principals only.** Live assignments on live roles whose ``membership_type``
  is ``principal``; a substitute serving under one is not a second holder. One
  person twice is one holder (#311's duplicate audit owns that shape).
- **Half-open tenures.** ``[start_date, end_date)``: a span ending the day the
  next begins is a same-day handoff, not an overlap. A current holder runs
  open-ended. No span is invented: an unknown start, or an unknown end that is
  not ``is_current``, is out of scope.
- **Checked where the count can peak:** at each principal's start, and at each
  capacity period's start — capacity can drop with no one starting.
- **Capacity at an instant** is the covering ``role_capacity_periods`` row's
  ``max_holders`` (``valid_to`` inclusive; null = no limit); with no covering
  period, the role type's ``occupancy`` (``single`` → 1; ``shared`` or no type →
  no limit). The fallback is PM's own divergence from usa-wa, which reads a
  missing period as one: it covers PM's legacy and admin rows (usa-wa D7).

A finding is every pair of principals holding the role at an instant where more
distinct people hold it than its capacity.

It is a **ratchet**, not a zero gate. ``BASELINE`` is the count measured on
production — 56 on 2026-10-10 (#506), 55 after #639, 48 after #671's re-pin
(2026-10-23), listed on usa-wa#489 and broken down in ``docs/AUDITS.md`` — and
the run exits 3 only when the count rises above it.
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

#: Pairs over capacity on production: 56 on 2026-10-10 (#506), 55 after #639, 48
#: after #671's re-pin (2026-10-23). Lower it as triage lands; never raise it to
#: clear a failure.
BASELINE = 48

_OVERLAPS_SQL = """
WITH principals AS (
    SELECT ra.id, ra.person_id, ra.role_id, ra.start_date, ra.end_date
    FROM role_assignments ra
    JOIN roles r ON r.id = ra.role_id AND r.archived_at IS NULL
    WHERE ra.archived_at IS NULL
      AND ra.membership_type = 'principal'
      AND ra.start_date IS NOT NULL
      AND (ra.end_date IS NOT NULL OR ra.is_current)
),
periods AS (
    SELECT role_id, valid_from, valid_to, max_holders
    FROM role_capacity_periods
    WHERE archived_at IS NULL
),
instants AS (
    SELECT role_id, start_date AS t FROM principals
    UNION
    SELECT p.role_id, p.valid_from FROM periods p
    WHERE p.role_id IN (SELECT role_id FROM principals)
),
-- An open span covers every later instant: spelled as its own branch, because
-- `t < NULL` is NULL and would drop every sitting member from the count.
holding AS (
    SELECT i.role_id, i.t, p.id, p.person_id
    FROM instants i
    JOIN principals p
      ON p.role_id = i.role_id
     AND p.start_date <= i.t
     AND (p.end_date IS NULL OR i.t < p.end_date)
),
capacity AS (
    SELECT h.role_id, h.t,
           CASE WHEN cp.covered THEN cp.max_holders
                WHEN rt.occupancy = 'single' THEN 1
           END AS max_holders
    FROM (SELECT DISTINCT role_id, t FROM holding) h
    JOIN roles r ON r.id = h.role_id
    LEFT JOIN role_types rt ON rt.id = r.role_type_id
    LEFT JOIN LATERAL (
        SELECT TRUE AS covered, p.max_holders
        FROM periods p
        WHERE p.role_id = h.role_id
          AND p.valid_from <= h.t
          AND (p.valid_to IS NULL OR h.t <= p.valid_to)
        ORDER BY p.valid_from DESC
        LIMIT 1
    ) cp ON TRUE
),
excess AS (
    SELECT h.role_id, h.t
    FROM holding h
    JOIN capacity c ON c.role_id = h.role_id AND c.t = h.t
    WHERE c.max_holders IS NOT NULL
    GROUP BY h.role_id, h.t, c.max_holders
    HAVING count(DISTINCT h.person_id) > c.max_holders
),
pairs AS (
    SELECT DISTINCT e.role_id, a.id AS a_id, b.id AS b_id
    FROM excess e
    JOIN holding a ON a.role_id = e.role_id AND a.t = e.t
    JOIN holding b ON b.role_id = e.role_id AND b.t = e.t
     AND a.id < b.id AND a.person_id <> b.person_id
)
SELECT p.role_id, r.title AS role_title,
       a.id AS a_id, a.person_id AS a_person_id, pa.display_name AS a_name,
       a.start_date AS a_start, a.end_date AS a_end,
       b.id AS b_id, b.person_id AS b_person_id, pb.display_name AS b_name,
       b.start_date AS b_start, b.end_date AS b_end
FROM pairs p
JOIN roles r ON r.id = p.role_id
JOIN role_assignments a ON a.id = p.a_id
JOIN role_assignments b ON b.id = p.b_id
LEFT JOIN v_person_display_names pa ON pa.person_id = a.person_id
LEFT JOIN v_person_display_names pb ON pb.person_id = b.person_id
ORDER BY r.title, p.role_id, a.id, b.id
"""


async def find_overlaps(conn: asyncpg.Connection) -> list[asyncpg.Record]:
    """Every pair of principals holding a role at an instant it is over capacity (D7)."""
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
            " seat over its capacity; diff the pair log against the last passing run"
            " (docs/AUDITS.md)",
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
