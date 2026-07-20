"""Audit role assignments against their org's lifespan (#307).

Invariant: an assignment's window must fall within its org's lifespan.
``v_org_lifespan.ended_on`` (earliest non-archived ``dissolved`` /
``merged_with`` event, latest date within its precision) is the bound.

Findings:

- ``current_on_ended`` — ``is_current=TRUE`` on an ended org and no dated
  contradiction. The only auto-fixable class: ``--execute`` closes it at
  ``ended_on`` (``end_date=ended_on, is_current=FALSE``, provenance note).
- ``end_after_ended`` — ``end_date > ended_on``; contradiction, report only.
- ``start_after_ended`` — ``start_date > ended_on``; contradiction, report
  only (closing at ``ended_on`` would end before it starts).
- ``unknown_end_on_ended`` — ``end_date NULL, is_current=FALSE`` on an ended
  org; unknown end is never invented, report only.
- ``missing_end_event`` — org ``active=FALSE`` or archived, has open
  assignments, but no end event to bound them. Needs a human-supplied
  dissolved/merged_with entity event; re-run afterwards.

Idempotent: a compliant DB yields no findings and ``--execute`` is a no-op.

Usage:
    uv run python -m scripts.audit_org_lifecycle_assignments            # report
    uv run python -m scripts.audit_org_lifecycle_assignments --execute  # close
"""

import argparse
import asyncio
import os
from collections import Counter

import asyncpg

from src.core.logging import configure_logging, get_logger

logger = get_logger(__name__)

CLOSE_NOTE = "Closed at org lifespan end (#307 audit)."

FINDING_CATEGORIES = (
    "current_on_ended",
    "end_after_ended",
    "start_after_ended",
    "unknown_end_on_ended",
    "missing_end_event",
)

_ASSIGNMENT_FINDINGS_SQL = """
SELECT ra.id AS assignment_id, ra.person_id, ra.role_id, ra.is_current,
       ra.start_date, ra.end_date,
       r.organization_id, dn.display_name, ls.ended_on,
       CASE
           WHEN ra.start_date IS NOT NULL AND ra.start_date > ls.ended_on
               THEN 'start_after_ended'
           WHEN ra.end_date IS NOT NULL AND ra.end_date > ls.ended_on
               THEN 'end_after_ended'
           WHEN ra.is_current
               THEN 'current_on_ended'
           WHEN ra.end_date IS NULL
               THEN 'unknown_end_on_ended'
       END AS category
FROM role_assignments ra
JOIN roles r ON r.id = ra.role_id
JOIN v_org_lifespan ls ON ls.organization_id = r.organization_id
LEFT JOIN v_org_display_names dn ON dn.organization_id = r.organization_id
WHERE ra.archived_at IS NULL AND r.archived_at IS NULL
  AND (
      ra.is_current
      OR ra.end_date IS NULL
      OR ra.end_date > ls.ended_on
      OR (ra.start_date IS NOT NULL AND ra.start_date > ls.ended_on)
  )
ORDER BY dn.display_name NULLS LAST, ra.id
"""

_MISSING_END_EVENT_SQL = """
SELECT o.id AS organization_id, dn.display_name,
       o.active, o.archived_at IS NOT NULL AS archived,
       count(*) AS open_assignments
FROM organizations o
LEFT JOIN v_org_display_names dn ON dn.organization_id = o.id
JOIN roles r ON r.organization_id = o.id AND r.archived_at IS NULL
JOIN role_assignments ra ON ra.role_id = r.id
    AND ra.archived_at IS NULL AND ra.end_date IS NULL
WHERE (o.active = FALSE OR o.archived_at IS NOT NULL)
  AND NOT EXISTS (SELECT 1 FROM v_org_lifespan ls WHERE ls.organization_id = o.id)
GROUP BY o.id, dn.display_name, o.active, o.archived_at
ORDER BY open_assignments DESC, o.id
"""

_CLOSE_SQL = """
UPDATE role_assignments
SET is_current = FALSE,
    end_date = $2,
    notes = CASE
        WHEN notes IS NULL OR notes = '' THEN $3
        ELSE notes || E'\n' || $3
    END
WHERE id = $1
"""


async def audit_org_lifecycle(conn: asyncpg.Connection) -> dict[str, list[dict]]:
    """Return findings keyed by category (see module docstring)."""
    findings: dict[str, list[dict]] = {category: [] for category in FINDING_CATEGORIES}
    for row in await conn.fetch(_ASSIGNMENT_FINDINGS_SQL):
        findings[row["category"]].append(dict(row))
    for row in await conn.fetch(_MISSING_END_EVENT_SQL):
        findings["missing_end_event"].append(dict(row))
    return findings


async def run_audit(conn: asyncpg.Connection, *, execute: bool) -> dict[str, list[dict]]:
    """Audit and, with ``execute``, close ``current_on_ended`` at ``ended_on``.

    Runs in the caller's transaction (or autocommit for report-only runs).
    """
    findings = await audit_org_lifecycle(conn)
    for f in findings["current_on_ended"]:
        if execute:
            await conn.execute(_CLOSE_SQL, f["assignment_id"], f["ended_on"], CLOSE_NOTE)
        logger.info(
            "%s assignment %s on %r (ended %s)",
            "Closed" if execute else "Would close",
            f["assignment_id"],
            f["display_name"],
            f["ended_on"],
        )
    return findings


def _log_report(findings: dict[str, list[dict]], *, execute: bool) -> None:
    for f in findings["end_after_ended"]:
        logger.warning(
            "end_after_ended: assignment %s on %r ends %s > org end %s",
            f["assignment_id"],
            f["display_name"],
            f["end_date"],
            f["ended_on"],
        )
    for f in findings["start_after_ended"]:
        logger.warning(
            "start_after_ended: assignment %s on %r starts %s > org end %s",
            f["assignment_id"],
            f["display_name"],
            f["start_date"],
            f["ended_on"],
        )
    # Per-org counts, not per-row lines — this category can run to hundreds of
    # rows and is informational (unknown ends are left open by design).
    unknown_by_org = Counter(
        (f["display_name"], f["ended_on"]) for f in findings["unknown_end_on_ended"]
    )
    for (display_name, ended_on), n in unknown_by_org.most_common():
        logger.info(
            "unknown_end_on_ended: %d assignment(s) on %r (org ended %s) — end unknown, left open",
            n,
            display_name,
            ended_on,
        )
    for f in findings["missing_end_event"]:
        logger.warning(
            "missing_end_event: %r (%s) is %s with %d open assignment(s) and no "
            "dissolved/merged_with event — record one, then re-run",
            f["display_name"],
            f["organization_id"],
            "archived" if f["archived"] else "inactive",
            f["open_assignments"],
        )
    summary = ", ".join(f"{cat}={len(rows)}" for cat, rows in findings.items())
    verb = "Closed" if execute else "Would close"
    logger.info("%s %d assignment(s); %s", verb, len(findings["current_on_ended"]), summary)
    if not execute and findings["current_on_ended"]:
        logger.info("Pass --execute to close them")


async def run(*, execute: bool) -> None:
    """Connect to DATABASE_URL and run the audit."""
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        raise RuntimeError("DATABASE_URL not set")

    conn = await asyncpg.connect(dsn)
    try:
        if execute:
            async with conn.transaction():
                findings = await run_audit(conn, execute=True)
        else:
            findings = await run_audit(conn, execute=False)
        _log_report(findings, execute=execute)
    finally:
        await conn.close()


def main() -> None:
    configure_logging()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Close current_on_ended assignments at ended_on (default is report-only)",
    )
    args = parser.parse_args()
    asyncio.run(run(execute=args.execute))


if __name__ == "__main__":
    main()
