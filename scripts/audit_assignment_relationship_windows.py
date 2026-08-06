"""Audit role-assignment relationship edges against their endpoint windows (#301).

Invariant: an active edge's ``[valid_from, valid_until]`` must fall within the
intersection of both endpoint assignment windows, and the edge cannot outlive an
endpoint. The observation path records freely (no enforcement), so this daily
audit reconciles drift — the steady-state counterpart to the endpoint-mutation
cascade trigger (``cascade_assignment_relationships``), sharing its exact clamp
rule so trigger and audit never diverge.

Findings (per active edge; an endpoint is ``from`` or ``to``):

- ``archived_endpoint`` — an endpoint assignment is archived. ``--execute``
  archives the edge (the endpoint is gone; the relationship cannot stand).
- ``clamp`` — a defined ``valid_from`` precedes, or ``valid_until`` (defined or
  NULL/ongoing) outlives, the endpoint intersection. ``--execute`` clamps:
  ``valid_from`` up to the latest endpoint start (defined only — an unknown start
  is never invented, #307), ``valid_until`` down to / materialized at the earliest
  endpoint end (the relationship dies when the employment does).
- ``inverted`` — clamping inverts the window (``valid_from > valid_until``).
  ``--execute`` archives the edge.

Idempotent: a reconciled DB yields no findings and ``--execute`` is a no-op.

Usage:
    uv run python -m scripts.audit_assignment_relationship_windows            # report
    uv run python -m scripts.audit_assignment_relationship_windows --execute  # fix
"""

import argparse
import asyncio
import datetime
import sys

import asyncpg

from scripts._dsn import add_dsn_args, resolve_dsn
from src.core.logging import configure_logging, get_logger

logger = get_logger(__name__)

FINDING_CATEGORIES = ("archived_endpoint", "clamp", "inverted")

# Active edges joined to both endpoints (including archived endpoints), with the
# endpoint-window intersection bounds. GREATEST/LEAST ignore NULLs (open bounds).
_CANDIDATE_SQL = """
SELECT r.id, r.valid_from, r.valid_until,
       (f.archived_at IS NOT NULL OR t.archived_at IS NOT NULL) AS endpoint_archived,
       GREATEST(f.start_date, t.start_date) AS lo,
       LEAST(f.end_date, t.end_date)        AS hi
FROM role_assignment_relationships r
JOIN role_assignments f ON f.id = r.from_assignment_id
JOIN role_assignments t ON t.id = r.to_assignment_id
WHERE r.archived_at IS NULL
ORDER BY r.id
"""

_ARCHIVE_SQL = "UPDATE role_assignment_relationships SET archived_at = NOW() WHERE id = $1"
_CLAMP_SQL = (
    "UPDATE role_assignment_relationships SET valid_from = $2, valid_until = $3"
    " WHERE id = $1 AND archived_at IS NULL"
)


def _clamp(
    valid_from: datetime.date | None,
    valid_until: datetime.date | None,
    lo: datetime.date | None,
    hi: datetime.date | None,
) -> tuple[datetime.date | None, datetime.date | None]:
    """Apply the shared clamp rule (identical to cascade_assignment_relationships)."""
    new_from = valid_from
    new_until = valid_until
    # Clamp a DEFINED start up; never materialize a NULL (unknown) start (#307).
    if lo is not None and new_from is not None and new_from < lo:
        new_from = lo
    # Clamp a defined end down, AND close a NULL (ongoing) edge at a defined end.
    if hi is not None and (new_until is None or new_until > hi):
        new_until = hi
    return new_from, new_until


def classify(row: dict) -> tuple[str, datetime.date | None, datetime.date | None] | None:
    """Return ``(category, new_from, new_until)`` for an edge needing action, else None."""
    if row["endpoint_archived"]:
        return ("archived_endpoint", None, None)
    new_from, new_until = _clamp(row["valid_from"], row["valid_until"], row["lo"], row["hi"])
    if new_from is not None and new_until is not None and new_from > new_until:
        return ("inverted", new_from, new_until)
    if new_from != row["valid_from"] or new_until != row["valid_until"]:
        return ("clamp", new_from, new_until)
    return None


async def audit(conn: asyncpg.Connection) -> dict[str, list[dict]]:
    """Return findings keyed by category (see module docstring)."""
    findings: dict[str, list[dict]] = {c: [] for c in FINDING_CATEGORIES}
    for row in await conn.fetch(_CANDIDATE_SQL):
        verdict = classify(dict(row))
        if verdict is None:
            continue
        category, new_from, new_until = verdict
        findings[category].append(
            {"id": row["id"], "new_from": new_from, "new_until": new_until, **dict(row)}
        )
    return findings


async def run_audit(conn: asyncpg.Connection, *, execute: bool) -> dict[str, list[dict]]:
    """Audit and, with ``execute``, clamp/archive drifted edges."""
    findings = await audit(conn)
    for f in findings["archived_endpoint"] + findings["inverted"]:
        if execute:
            await conn.execute(_ARCHIVE_SQL, f["id"])
    for f in findings["clamp"]:
        if execute:
            await conn.execute(_CLAMP_SQL, f["id"], f["new_from"], f["new_until"])
    return findings


def _log_report(findings: dict[str, list[dict]], *, execute: bool) -> None:
    verb_fix = "Clamped" if execute else "Would clamp"
    verb_arc = "Archived" if execute else "Would archive"
    for f in findings["clamp"]:
        logger.info(
            "%s edge %s: [%s, %s] -> [%s, %s]",
            verb_fix,
            f["id"],
            f["valid_from"],
            f["valid_until"],
            f["new_from"],
            f["new_until"],
        )
    for f in findings["inverted"]:
        logger.warning("%s edge %s: window inverts under endpoint bounds", verb_arc, f["id"])
    for f in findings["archived_endpoint"]:
        logger.warning("%s edge %s: an endpoint assignment is archived", verb_arc, f["id"])
    summary = ", ".join(f"{c}={len(rows)}" for c, rows in findings.items())
    total = sum(len(rows) for rows in findings.values())
    logger.info("%s %d edge(s); %s", "Reconciled" if execute else "Would reconcile", total, summary)
    if not execute and total:
        logger.info("Pass --execute to apply")


def _exit_code(findings: dict[str, list[dict]], *, execute: bool) -> int:
    """Exit 3 when a report-only run found drift, else 0 (#363).

    Mirrors the sibling audits (``audit_ancillary_orphans`` / ``audit_schema_constraint_parity``)
    so the systemd unit shows as failed in ``systemctl --failed`` and can drive
    ``OnFailure=``. ``--execute`` reconciled the drift, so it always exits 0.
    """
    if execute:
        return 0
    total = sum(len(rows) for rows in findings.values())
    return 3 if total else 0


async def run(dsn: str, *, execute: bool) -> int:
    conn = await asyncpg.connect(dsn)
    try:
        if execute:
            async with conn.transaction():
                findings = await run_audit(conn, execute=True)
        else:
            findings = await run_audit(conn, execute=False)
        _log_report(findings, execute=execute)
        return _exit_code(findings, execute=execute)
    finally:
        await conn.close()


def main() -> None:
    configure_logging()
    parser = argparse.ArgumentParser(description=__doc__)
    add_dsn_args(parser)
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Clamp/archive drifted edges (default is report-only)",
    )
    args = parser.parse_args()
    dsn = resolve_dsn(args, parser)
    sys.exit(asyncio.run(run(dsn, execute=args.execute)))


if __name__ == "__main__":
    main()
