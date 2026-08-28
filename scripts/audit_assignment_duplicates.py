"""Audit duplicate role assignments minted by start_date corrections (#311).

Before #311, a producer correcting a tenure's ``start_date`` missed the
``(person, role, start_date)`` match key and minted a *new* assignment,
orphaning the previously anchored row. This audit finds overlapping active
pairs for the same (person, role) — both dated; undated tenures coexist with
dated ones by design (#289) and are never flagged. Non-overlapping tenures
(returning legislators) are legitimate and never flagged.

Pair orientation: the earlier-start row is the **survivor**, the later-start
row the **orphan**. Earlier-start does *not* imply wider — assuming it did is
what produced the #474 archivals — so coverage is proven, never inferred from
orientation. Categories:

**Coverage is the merge gate (#476).** Both auto-merge categories require the
same proof: the orphan's end is dated *and* the survivor's window provably
covers it (dated end ≥ orphan's dated end, or the survivor is open and
``is_current``). Only then does creation order pick between them:

- ``deepened_start`` — covering, and the survivor was created *after* the
  orphan: the #311 producer-correction signature. Auto-mergeable.
- ``subsumed`` — covering, survivor created first. Auto-mergeable.
- ``overlapping_review`` — overlap without provable coverage (unknown end on
  the survivor, an open-ended orphan, or a survivor that ends *before* its
  orphan does); report only.

The merge reconciles no dates — it keeps the survivor's window as stored — so a
pair whose coverage is unproven would discard the orphan's tenure outright.
Before #476 ``deepened_start`` skipped the proof and did exactly that (#474: 21
archivals). The audit never widens a span either: coverage it cannot prove is a
human decision, not a guess.

``--execute`` merges auto-mergeable pairs: links / contact methods / addresses
/ identifiers move to the survivor (rows that would duplicate stay on the
orphan), notes concatenate, and the orphan is **archived** (never deleted) with
a provenance note recording the span the merge discarded. The archive UPDATE
hits the entity_changes outbox, so subscribed producers see the retirement and
can drop stale anchors.

Idempotent: a merged pair leaves the audit's scope (archived rows are ignored).

Usage:
    uv run python -m scripts.audit_assignment_duplicates            # report
    uv run python -m scripts.audit_assignment_duplicates --execute  # merge
"""

import argparse
import asyncio

import asyncpg

from scripts._dsn import add_dsn_args, resolve_dsn
from src.core.logging import configure_logging, get_logger

logger = get_logger(__name__)

FINDING_CATEGORIES = ("deepened_start", "subsumed", "overlapping_review")
AUTO_MERGE_CATEGORIES = ("deepened_start", "subsumed")

_ORPHAN_NOTE = "Archived as duplicate of {survivor} (#311 audit). Span was {start}..{end}."
_OPEN_END = "open"
_UNKNOWN_START = "unknown"

# survivor s = earlier start (strictly — equal active starts are impossible under
# uq_role_assignment_person_role_start); orphan o = later start. Overlap given
# s.start < o.start reduces to o.start <= s's effective end (NULL end = open:
# covers both is_current and unknown-end conservatively, so unprovable pairs
# surface for review instead of hiding).
#
# The outer CASE is the coverage proof, shared by both auto-merge categories
# (#476); the inner one only names which of them it is. Written this way so a
# NULL comparison (s.end_date IS NULL, s.is_current FALSE) falls to the ELSE
# rather than through to a merge — a WHEN on NOT(...) would not.
_FINDINGS_SQL = """
SELECT s.id AS survivor_id, o.id AS orphan_id,
       s.person_id, s.role_id, r.title AS role_title,
       s.start_date AS survivor_start, s.end_date AS survivor_end,
       s.is_current AS survivor_is_current,
       o.start_date AS orphan_start, o.end_date AS orphan_end,
       o.is_current AS orphan_is_current,
       CASE
           WHEN o.end_date IS NOT NULL
                AND (s.end_date >= o.end_date OR (s.end_date IS NULL AND s.is_current))
               THEN CASE WHEN s.created_at > o.created_at
                         THEN 'deepened_start' ELSE 'subsumed' END
           ELSE 'overlapping_review'
       END AS category
FROM role_assignments s
JOIN role_assignments o
  ON o.person_id = s.person_id AND o.role_id = s.role_id AND o.id <> s.id
JOIN roles r ON r.id = s.role_id
WHERE s.archived_at IS NULL AND o.archived_at IS NULL
  AND s.start_date IS NOT NULL AND o.start_date IS NOT NULL
  AND s.start_date < o.start_date
  AND o.start_date <= COALESCE(s.end_date, 'infinity'::date)
ORDER BY s.person_id, s.role_id, o.start_date, o.id
"""

# Side-data moves: rows that would duplicate an existing survivor row stay on
# the (about-to-be-archived) orphan — harmless, and nothing is deleted.
_MOVE_LINKS_SQL = """
UPDATE links l SET entity_id = $2
WHERE l.entity_type = 'role_assignment' AND l.entity_id = $1
  AND NOT EXISTS (
      SELECT 1 FROM links x
      WHERE x.entity_type = 'role_assignment' AND x.entity_id = $2
        AND x.url = l.url AND x.link_type_id = l.link_type_id
  )
"""

_MOVE_CONTACT_METHODS_SQL = """
UPDATE contact_methods c SET entity_id = $2
WHERE c.entity_type = 'role_assignment' AND c.entity_id = $1
  AND NOT EXISTS (
      SELECT 1 FROM contact_methods x
      WHERE x.entity_type = 'role_assignment' AND x.entity_id = $2
        AND x.contact_type = c.contact_type AND x.value = c.value
  )
"""

_MOVE_ADDRESSES_SQL = """
UPDATE entity_addresses ea SET entity_id = $2
WHERE ea.entity_type = 'role_assignment' AND ea.entity_id = $1
  AND NOT EXISTS (
      SELECT 1 FROM entity_addresses x
      WHERE x.entity_type = 'role_assignment' AND x.entity_id = $2
        AND x.address_id = ea.address_id AND x.address_type = ea.address_type
        AND x.valid_from IS NOT DISTINCT FROM ea.valid_from
        AND x.valid_until IS NOT DISTINCT FROM ea.valid_until
  )
"""

_MOVE_IDENTIFIERS_SQL = """
UPDATE identifiers i SET entity_id = $2
WHERE i.entity_id = $1
  AND EXISTS (
      SELECT 1 FROM entity_identifier_types t
      WHERE t.id = i.entity_identifier_type_id AND t.entity_type = 'role_assignment'
  )
  AND NOT EXISTS (
      SELECT 1 FROM identifiers x
      WHERE x.entity_id = $2
        AND x.entity_identifier_type_id = i.entity_identifier_type_id
        AND x.value = i.value
  )
"""

_MERGE_NOTES_SQL = """
UPDATE role_assignments SET notes = CASE
    WHEN notes IS NULL OR notes = '' THEN $2
    ELSE notes || E'\n' || $2
END
WHERE id = $1
"""

_ARCHIVE_ORPHAN_SQL = """
UPDATE role_assignments
SET archived_at = NOW(),
    notes = CASE
        WHEN notes IS NULL OR notes = '' THEN $2
        ELSE notes || E'\n' || $2
    END
WHERE id = $1 AND archived_at IS NULL
"""


async def find_duplicates(conn: asyncpg.Connection) -> dict[str, list[dict]]:
    """Return findings keyed by category (see module docstring)."""
    findings: dict[str, list[dict]] = {category: [] for category in FINDING_CATEGORIES}
    for row in await conn.fetch(_FINDINGS_SQL):
        findings[row["category"]].append(dict(row))
    return findings


async def _merge_pair(conn: asyncpg.Connection, survivor_id: str, orphan_id: str) -> bool:
    """Move the orphan's side data to the survivor and archive the orphan.

    The archive note names the survivor *and* the orphan's span (#476): the
    merge does not reconcile dates, so that window survives nowhere else.

    Returns False (skipping the merge) when either row was archived by an
    earlier pair this run — findings are computed once up front, so 3+-row
    overlap chains can stale-reference a row another merge just retired (CR
    round 1, #311). Data must never move onto an archived row; a skipped pair
    that still duplicates an active survivor resolves on the next run.
    """
    archived = await conn.fetch(
        "SELECT id FROM role_assignments WHERE id = ANY($1::text[]) AND archived_at IS NOT NULL",
        [survivor_id, orphan_id],
    )
    if archived:
        logger.warning(
            "skip merge %s <- %s: row(s) %s already archived by an earlier pair this run",
            survivor_id,
            orphan_id,
            ", ".join(r["id"] for r in archived),
        )
        return False
    await conn.execute(_MOVE_LINKS_SQL, orphan_id, survivor_id)
    await conn.execute(_MOVE_CONTACT_METHODS_SQL, orphan_id, survivor_id)
    await conn.execute(_MOVE_ADDRESSES_SQL, orphan_id, survivor_id)
    await conn.execute(_MOVE_IDENTIFIERS_SQL, orphan_id, survivor_id)
    orphan = await conn.fetchrow(
        "SELECT notes, start_date, end_date FROM role_assignments WHERE id=$1", orphan_id
    )
    orphan_notes = orphan["notes"]
    if orphan_notes:
        survivor_notes = await conn.fetchval(
            "SELECT notes FROM role_assignments WHERE id=$1", survivor_id
        )
        if orphan_notes not in (survivor_notes or ""):
            await conn.execute(_MERGE_NOTES_SQL, survivor_id, orphan_notes)
    # The merge keeps the survivor's window, so the orphan's is otherwise lost.
    note = _ORPHAN_NOTE.format(
        survivor=survivor_id,
        start=orphan["start_date"] or _UNKNOWN_START,
        end=orphan["end_date"] or _OPEN_END,
    )
    await conn.execute(_ARCHIVE_ORPHAN_SQL, orphan_id, note)
    return True


async def run_audit(conn: asyncpg.Connection, *, execute: bool) -> dict[str, list[dict]]:
    """Audit and, with ``execute``, merge the auto-mergeable pairs.

    Runs in the caller's transaction (or autocommit for report-only runs).
    """
    findings = await find_duplicates(conn)
    for category in AUTO_MERGE_CATEGORIES:
        for f in findings[category]:
            if execute:
                merged = await _merge_pair(conn, f["survivor_id"], f["orphan_id"])
                verb = "Merged" if merged else "Skipped"
            else:
                verb = "Would merge"
            logger.info(
                "%s %s: orphan %s (start %s) into survivor %s (start %s, %r)",
                verb,
                category,
                f["orphan_id"],
                f["orphan_start"],
                f["survivor_id"],
                f["survivor_start"],
                f["role_title"],
            )
    return findings


def _log_report(findings: dict[str, list[dict]], *, execute: bool) -> None:
    for f in findings["overlapping_review"]:
        logger.warning(
            "overlapping_review: person %s role %r — %s [%s..%s%s] overlaps %s [%s..%s%s] "
            "— coverage unprovable, resolve by hand",
            f["person_id"],
            f["role_title"],
            f["survivor_id"],
            f["survivor_start"],
            f["survivor_end"] or "",
            " current" if f["survivor_is_current"] else "",
            f["orphan_id"],
            f["orphan_start"],
            f["orphan_end"] or "",
            " current" if f["orphan_is_current"] else "",
        )
    merged = sum(len(findings[c]) for c in AUTO_MERGE_CATEGORIES)
    summary = ", ".join(f"{cat}={len(rows)}" for cat, rows in findings.items())
    logger.info("%s %d pair(s); %s", "Merged" if execute else "Would merge", merged, summary)
    if not execute and merged:
        logger.info("Pass --execute to merge them")


async def run(dsn: str, *, execute: bool) -> None:
    """Connect to DATABASE_URL and run the audit."""
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
    """CLI entry point."""
    configure_logging()
    parser = argparse.ArgumentParser(description=__doc__)
    add_dsn_args(parser)
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Merge auto-mergeable duplicate pairs (default is report-only)",
    )
    args = parser.parse_args()
    dsn = resolve_dsn(args, parser)
    asyncio.run(run(dsn, execute=args.execute))


if __name__ == "__main__":
    main()
