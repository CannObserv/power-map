"""Backfill org end events + close open assignments on defunct orgs (#313).

Follow-up to #307 (scope item 5). Nine orgs surfaced as ``missing_end_event``
in ``audit_org_lifecycle_assignments``; human research (issue #313) resolved
them to:

- Five defunct orgs get a ``dissolved`` event at a researched date, and every
  open assignment is closed at that ``ended_on``. Unlike the audit's
  ``--execute`` (which closes only ``is_current=TRUE`` rows and leaves
  ``unknown_end_on_ended`` alone), this backfill closes **all** open rows at
  ``ended_on`` — a deliberate, human-authorized close, not an invented end.
- Kalytera renamed to Claritas Pharmaceuticals (2021-04-02) and is treated as
  the same continuing org: flip ``active=TRUE`` so it drops out of
  ``missing_end_event``; its assignment stays open. The name swap is done by
  hand in admin (org → Names), not here.
- WA Senate LCTA descoped (resolved via other means); Grown Folks 502 and
  McGregor Company were already ``active=TRUE`` and need nothing.

Idempotent: skips an org that already carries a lifespan-bounding
(``dissolved``/``merged_with``) event; re-closing already-closed assignments
is a no-op (only ``end_date IS NULL`` rows match).

Usage (via ``uv run --env-file /etc/power-map/.env``):
    python -m scripts.backfill_313_org_end_events            # report (read-only)
    python -m scripts.backfill_313_org_end_events --execute  # apply
"""

import argparse
import asyncio
import datetime
import os

import asyncpg

from src.core.db import generate_id
from src.core.logging import configure_logging, get_logger

logger = get_logger(__name__)

CLOSE_NOTE = "Closed at org lifespan end (#313 backfill)."
EVENT_NOTE = "End event recorded via #313 backfill (see issue for sourcing)."

# org_id -> (event slug, researched end date). Each date is day-precision.
END_EVENTS: dict[str, tuple[str, datetime.date]] = {
    # WA SECTF (Social Equity in Marijuana) — statutory sunset, RCW 69.50.336
    "01KV6PQJZZ86FAHT3GAQ2S74RM": ("dissolved", datetime.date(2023, 6, 30)),
    # WA SECTF – Licensing Work Group — ends with parent task force
    "01KV6PQGGN5ZQ30BYEWX15K6RG": ("dissolved", datetime.date(2023, 6, 30)),
    # WA SECTF – TA & Mentorship Work Group — ends with parent task force
    "01KV6PQGH5RYK4PV1W55QFAGB6": ("dissolved", datetime.date(2023, 6, 30)),
    # Craft Cannabis Coalition
    "01KV6PNWCH596352STHNZA96N5": ("dissolved", datetime.date(2023, 6, 30)),
    # Landrace Brands
    "01KV6PPCFQXNB64MMEK9JK07P9": ("dissolved", datetime.date(2025, 6, 30)),
}

# Renamed → Claritas Pharmaceuticals (2021-04-02); treated as continuing, so it
# is reactivated rather than dissolved. Assignment stays open.
KALYTERA_ID = "01KV6PPBKXRHYY1FR7MF97E6PQ"


async def _lifespan_event_exists(conn: asyncpg.Connection, org_id: str) -> bool:
    """True if the org already carries a non-archived dissolved/merged_with event."""
    return bool(
        await conn.fetchval(
            """SELECT 1 FROM entity_events ev
               JOIN entity_event_types t ON t.id = ev.event_type_id
               WHERE ev.entity_type = 'organization' AND ev.entity_id = $1
                 AND t.slug IN ('dissolved', 'merged_with')
                 AND ev.archived_at IS NULL AND ev.event_year IS NOT NULL
               LIMIT 1""",
            org_id,
        )
    )


async def _ended_on(conn: asyncpg.Connection, org_id: str) -> datetime.date | None:
    """The org's derived lifespan end, or None if it has no lifespan event yet."""
    return await conn.fetchval(
        "SELECT ended_on FROM v_org_lifespan WHERE organization_id = $1", org_id
    )


async def _org_exists(conn: asyncpg.Connection, org_id: str) -> bool:
    """True if the org id resolves. ``entity_events.entity_id`` is polymorphic
    (no FK), so a mistyped id would otherwise create an orphan event."""
    return bool(await conn.fetchval("SELECT 1 FROM organizations WHERE id = $1", org_id))


# Open = end_date NULL on a non-archived assignment of a non-archived role. Split
# by whether closing at ``ended_on`` would invert the window: a row whose
# start_date > ended_on is ``start_after_ended`` and must be left open (mirrors
# the audit) — there is no DB CHECK enforcing end_date >= start_date.
_CLOSABLE_OPEN_SQL = """
SELECT ra.id FROM role_assignments ra
JOIN roles r ON r.id = ra.role_id
WHERE r.organization_id = $1 AND r.archived_at IS NULL
  AND ra.archived_at IS NULL AND ra.end_date IS NULL
  AND (ra.start_date IS NULL OR ra.start_date <= $2)
ORDER BY ra.id
"""

_START_AFTER_ENDED_SQL = """
SELECT ra.id FROM role_assignments ra
JOIN roles r ON r.id = ra.role_id
WHERE r.organization_id = $1 AND r.archived_at IS NULL
  AND ra.archived_at IS NULL AND ra.end_date IS NULL
  AND ra.start_date IS NOT NULL AND ra.start_date > $2
ORDER BY ra.id
"""


async def _closable_open_ids(
    conn: asyncpg.Connection, org_id: str, ended_on: datetime.date
) -> list[str]:
    """Open assignment ids that can be closed at ``ended_on`` without inverting."""
    return [r["id"] for r in await conn.fetch(_CLOSABLE_OPEN_SQL, org_id, ended_on)]


async def _start_after_ended_ids(
    conn: asyncpg.Connection, org_id: str, ended_on: datetime.date
) -> list[str]:
    """Open assignment ids whose start_date > ``ended_on`` — left open, not closed."""
    return [r["id"] for r in await conn.fetch(_START_AFTER_ENDED_SQL, org_id, ended_on)]


async def _create_end_event(
    conn: asyncpg.Connection, org_id: str, slug: str, on: datetime.date
) -> None:
    """Insert a day-precision lifespan event, mirroring the admin events path."""
    event_type_id = await conn.fetchval("SELECT id FROM entity_event_types WHERE slug = $1", slug)
    if event_type_id is None:
        raise RuntimeError(f"Unknown entity_event_type slug {slug!r}")
    await conn.execute(
        """INSERT INTO entity_events
               (id, entity_type, entity_id, event_type_id,
                event_year, event_month, event_day, notes, visibility)
           VALUES ($1, 'organization', $2, $3, $4, $5, $6, $7, 'public')""",
        generate_id(),
        org_id,
        event_type_id,
        on.year,
        on.month,
        on.day,
        EVENT_NOTE,
    )


async def _close_open_assignments(
    conn: asyncpg.Connection, org_id: str, ended_on: datetime.date
) -> list[str]:
    """Close open assignments at ``ended_on``; return closed ids.

    Skips ``start_after_ended`` rows (start_date > ended_on) — closing them
    would set end_date < start_date, and no DB CHECK guards against it.
    """
    rows = await conn.fetch(
        """UPDATE role_assignments ra
           SET is_current = FALSE,
               end_date = $2,
               notes = CASE
                   WHEN ra.notes IS NULL OR ra.notes = '' THEN $3
                   ELSE ra.notes || E'\n' || $3
               END
           FROM roles r
           WHERE ra.role_id = r.id AND r.organization_id = $1
             AND r.archived_at IS NULL AND ra.archived_at IS NULL
             AND ra.end_date IS NULL
             AND (ra.start_date IS NULL OR ra.start_date <= $2)
           RETURNING ra.id""",
        org_id,
        ended_on,
        CLOSE_NOTE,
    )
    return [r["id"] for r in rows]


async def run_backfill(conn: asyncpg.Connection, *, execute: bool) -> dict[str, list[str]]:
    """Record end events, close open assignments, reactivate Kalytera.

    Runs in the caller's transaction (report-only runs stay read-only).
    Returns a summary keyed by ``events``, ``closed``, ``skipped``,
    ``reactivated`` — each a list of the org ids (or assignment ids, for
    ``closed``/``skipped``) that were or would be touched.
    """
    summary: dict[str, list[str]] = {
        "events": [],
        "closed": [],
        "skipped": [],
        "reactivated": [],
    }

    for org_id, (slug, on) in END_EVENTS.items():
        if not await _org_exists(conn, org_id):
            logger.warning("org %s not found — skipping (check the END_EVENTS ids)", org_id)
            continue

        if await _lifespan_event_exists(conn, org_id):
            logger.info("event exists: %s already has a lifespan event — skipping", org_id)
        else:
            if execute:
                await _create_end_event(conn, org_id, slug, on)
            summary["events"].append(org_id)
            logger.info(
                "%s %s event on %s at %s",
                "recorded" if execute else "would record",
                slug,
                org_id,
                on,
            )

        ended_on = await _ended_on(conn, org_id) or on
        skipped = await _start_after_ended_ids(conn, org_id, ended_on)
        if execute:
            closed = await _close_open_assignments(conn, org_id, ended_on)
        else:
            closed = await _closable_open_ids(conn, org_id, ended_on)
        summary["closed"].extend(closed)
        summary["skipped"].extend(skipped)
        logger.info(
            "%s %d assignment(s) on %s at %s",
            "closed" if execute else "would close",
            len(closed),
            org_id,
            ended_on,
        )
        if skipped:
            logger.warning(
                "left %d start_after_ended assignment(s) open on %s (start_date > %s) — "
                "closing would invert the window; resolve by hand",
                len(skipped),
                org_id,
                ended_on,
            )

    active = await conn.fetchval("SELECT active FROM organizations WHERE id = $1", KALYTERA_ID)
    if active is None:
        logger.warning("Kalytera %s not found — skipping reactivation", KALYTERA_ID)
    elif active:
        logger.info("Kalytera %s already active — skipping", KALYTERA_ID)
    else:
        if execute:
            await conn.execute("UPDATE organizations SET active = TRUE WHERE id = $1", KALYTERA_ID)
        summary["reactivated"].append(KALYTERA_ID)
        logger.info("%s Kalytera %s active=TRUE", "set" if execute else "would set", KALYTERA_ID)

    verb = "Recorded" if execute else "Would record"
    logger.info(
        "%s %d event(s), closed %d assignment(s), skipped %d, reactivated %d org(s)",
        verb,
        len(summary["events"]),
        len(summary["closed"]),
        len(summary["skipped"]),
        len(summary["reactivated"]),
    )
    if not execute:
        logger.info("Pass --execute to apply")
    return summary


async def run(*, execute: bool) -> None:
    """Connect to DATABASE_URL and run the backfill."""
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        raise RuntimeError("DATABASE_URL not set")
    conn = await asyncpg.connect(dsn)
    try:
        if execute:
            async with conn.transaction():
                await run_backfill(conn, execute=True)
        else:
            await run_backfill(conn, execute=False)
    finally:
        await conn.close()


def main() -> None:
    configure_logging()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true", help="apply changes (default: report)")
    args = parser.parse_args()
    asyncio.run(run(execute=args.execute))


if __name__ == "__main__":
    main()
