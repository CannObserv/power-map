"""Data-only sweep of non-role observation artifacts + typo'd role titles (#304).

Follow-on to #266. Two mechanical fixes over **plain free-text roles** — those
with ``role_type_id IS NULL AND jurisdiction_id IS NULL``, whose match key is
``(organization_id, lower(title))`` (``uq_role_org_title``). Typed / jurisdictional
roles match structurally and are never touched.

1. **Archive non-role artifacts** — ``Guest`` / ``Visitor or Guest``: attendance
   noise, not offices, that leaks into "membership" queries. Their active
   assignments are archived, then the role is archived (never hard-deleted).

2. **Normalize typo'd titles** — ``Principle`` → ``Principal``: a misspelling is
   an orphan ``(org, lower(title))`` match key, so a fresh observation of the
   correct spelling mints a *second* role instead of matching. The fix normally
   renames in place; when the same org **already** carries the canonical role, it
   would collide on ``uq_role_org_title``, so the typo role is instead **merged**
   into the canonical one — assignments re-pointed (same ``(person, start_date)``
   deduped), role-level ancillary re-homed (#324/#326), loser hard-deleted.

Scope intentionally excludes the judgment calls surfaced in #304 — ``Participant``
disposition, bare ``Chairman`` normalization, and the ``Ranking Democratic Member``
typed-fold — which are vocabulary decisions for #266, not a mechanical sweep.

Idempotent: canonical titles are not typo keys and artifact roles archive once, so
re-runs are no-ops. Coverage is unbounded — every matching active role is handled
each run (e.g. after upstream backfills add more).

Usage:
    uv run python -m scripts.sweep_role_data_quality            # dry run
    uv run python -m scripts.sweep_role_data_quality --execute  # commit
"""

import argparse
import asyncio
import os
from collections import Counter
from datetime import UTC, datetime
from typing import Literal, TypedDict

import asyncpg

from src.core.ancillary_migrate import (
    rehome_conflicting_assignment_ancillary,
    rehome_role_ancillary,
)
from src.core.logging import configure_logging, get_logger

logger = get_logger(__name__)

# Non-role observation artifacts to archive (lowercased match keys, #304).
ARCHIVE_TITLES: frozenset[str] = frozenset({"guest", "visitor or guest"})

# Typo'd title (lowercased) → canonical display spelling (#304). Keys must be
# lowercase; values must not themselves be typo keys (no rename chains).
RENAME_MAP: dict[str, str] = {"principle": "Principal"}


def canonical_rename(title: str) -> str | None:
    """Return the canonical spelling for a typo'd ``title``, or None if not a typo.

    Case-insensitive and whitespace-tolerant. A title already at its canonical
    form is not a key, so returns None — keeps re-runs idempotent.
    """
    return RENAME_MAP.get(title.strip().lower())


ArchiveStatus = Literal["archived", "planned"]
# Dry run distinguishes the two rename outcomes — an in-place UPDATE vs a
# destructive merge into an existing canonical role — so the risky path is
# visible before --execute. Execute reports the terminal "renamed" / "merged".
RenameStatus = Literal["renamed", "merged", "would_rename", "would_merge"]


class ArchiveAction(TypedDict):
    """Outcome for one archived artifact role."""

    role_id: str
    organization_id: str
    title: str
    active_assignments: int
    status: ArchiveStatus


class RenameAction(TypedDict):
    """Outcome for one normalized title: renamed in place, or merged on collision."""

    role_id: str
    organization_id: str
    from_title: str
    to_title: str
    target_role_id: str | None  # set only when merged into an existing canonical role
    status: RenameStatus


class Report(TypedDict):
    """Full sweep outcome."""

    archived: list[ArchiveAction]
    renamed: list[RenameAction]


# Plain free-text roles only: (org, lower(title)) is the match key exactly when
# both structural fields are NULL, so uq_role_org_title governs collisions here.
_ARTIFACT_ROLES_SQL = """
SELECT r.id, r.organization_id, r.title,
       count(ra.id) FILTER (WHERE ra.archived_at IS NULL) AS active_assignments
FROM roles r
LEFT JOIN role_assignments ra ON ra.role_id = r.id
WHERE r.archived_at IS NULL
  AND r.role_type_id IS NULL AND r.jurisdiction_id IS NULL
  AND lower(r.title) = ANY($1)
GROUP BY r.id, r.organization_id, r.title
ORDER BY r.title, r.id
"""

_TYPO_ROLES_SQL = """
SELECT r.id, r.organization_id, r.title
FROM roles r
WHERE r.archived_at IS NULL
  AND r.role_type_id IS NULL AND r.jurisdiction_id IS NULL
  AND lower(r.title) = ANY($1)
ORDER BY r.title, r.id
"""

# A same-org active role already at the canonical title (excluding self). Scoped
# to exactly uq_role_org_title's predicate (jurisdiction_id IS NULL AND
# archived_at IS NULL) — deliberately NOT also role_type_id IS NULL: the index
# covers non-jurisdictional roles regardless of role_type, so a *typed* peer at
# the canonical title would still collide on a bare in-place UPDATE. Detecting it
# routes the typo through a merge instead (untyped loser folds into the typed
# winner — the better outcome), never tripping the constraint.
_CANONICAL_PEER_SQL = """
SELECT r.id
FROM roles r
WHERE r.archived_at IS NULL AND r.jurisdiction_id IS NULL
  AND r.organization_id = $1 AND lower(r.title) = $2 AND r.id <> $3
ORDER BY r.id
LIMIT 1
"""

_ARCHIVE_ASSIGNMENTS_SQL = (
    "UPDATE role_assignments SET archived_at = NOW() WHERE role_id = $1 AND archived_at IS NULL"
)
_ARCHIVE_ROLE_SQL = "UPDATE roles SET archived_at = NOW() WHERE id = $1"
_RENAME_ROLE_SQL = "UPDATE roles SET title = $2 WHERE id = $1"

# Merge (mirrors src/api/admin/orgs_roles.py::role_merge): dedup conflicting
# assignments by (person, start_date), re-home their ancillary onto the survivor
# before the delete (#324), re-point the rest, re-home role-level ancillary (#326),
# then hard-delete the loser role.
_CONFLICT_PAIRS_SQL = """
SELECT l.id AS loser_ra, w.id AS winner_ra
FROM role_assignments l
JOIN role_assignments w
  ON w.role_id = $2 AND w.archived_at IS NULL
 AND w.person_id = l.person_id
 AND w.start_date IS NOT DISTINCT FROM l.start_date
WHERE l.role_id = $1 AND l.archived_at IS NULL
"""

_DELETE_CONFLICT_ASSIGNMENTS_SQL = """
DELETE FROM role_assignments ra
WHERE ra.role_id = $1 AND ra.archived_at IS NULL
  AND EXISTS (
      SELECT 1 FROM role_assignments w
      WHERE w.role_id = $2 AND w.archived_at IS NULL
        AND w.person_id = ra.person_id
        AND w.start_date IS NOT DISTINCT FROM ra.start_date
  )
"""

_REPOINT_ASSIGNMENTS_SQL = "UPDATE role_assignments SET role_id = $1 WHERE role_id = $2"
_DELETE_ROLE_SQL = "DELETE FROM roles WHERE id = $1"

_LOSER_ROLE_SQL = "SELECT title, notes FROM roles WHERE id = $1"
_WINNER_NOTES_SQL = "SELECT notes FROM roles WHERE id = $1"
_APPEND_WINNER_NOTES_SQL = "UPDATE roles SET notes = $2 WHERE id = $1"


async def _archive_artifacts(conn: asyncpg.Connection, *, execute: bool) -> list[ArchiveAction]:
    """Archive artifact roles (Guest / Visitor or Guest) and their active assignments."""
    actions: list[ArchiveAction] = []
    for row in await conn.fetch(_ARTIFACT_ROLES_SQL, list(ARCHIVE_TITLES)):
        action: ArchiveAction = {
            "role_id": row["id"],
            "organization_id": row["organization_id"],
            "title": row["title"],
            "active_assignments": row["active_assignments"],
            "status": "planned",
        }
        actions.append(action)
        if not execute:
            continue
        await conn.execute(_ARCHIVE_ASSIGNMENTS_SQL, row["id"])
        await conn.execute(_ARCHIVE_ROLE_SQL, row["id"])
        action["status"] = "archived"
        logger.info(
            "Archived artifact role %r (%s), %d assignment(s)",
            row["title"],
            row["id"],
            row["active_assignments"],
        )
    return actions


async def _merge_into_canonical(conn: asyncpg.Connection, loser_id: str, winner_id: str) -> None:
    """Fold a typo role into the same-org canonical role (mirrors admin role_merge)."""
    # Preserve the loser role's notes on the survivor before the hard-delete
    # (mirrors role_merge; the script is the actor in place of a curator email).
    loser = await conn.fetchrow(_LOSER_ROLE_SQL, loser_id)
    if loser is not None and loser["notes"]:
        winner_notes = await conn.fetchval(_WINNER_NOTES_SQL, winner_id)
        merge_date = datetime.now(UTC).strftime("%Y-%m-%d")
        prefix = (
            f"Merged from {loser['title']} on {merge_date}"
            " by scripts.sweep_role_data_quality (#304)"
        )
        appended = f"{prefix}\n{loser['notes']}"
        new_notes = f"{winner_notes}\n\n{appended}" if winner_notes else appended
        await conn.execute(_APPEND_WINNER_NOTES_SQL, winner_id, new_notes)

    conflict_pairs = await conn.fetch(_CONFLICT_PAIRS_SQL, loser_id, winner_id)
    await rehome_conflicting_assignment_ancillary(
        conn, [(r["loser_ra"], r["winner_ra"]) for r in conflict_pairs]
    )
    await conn.execute(_DELETE_CONFLICT_ASSIGNMENTS_SQL, loser_id, winner_id)
    await conn.execute(_REPOINT_ASSIGNMENTS_SQL, winner_id, loser_id)
    await rehome_role_ancillary(conn, loser_id, winner_id)
    await conn.execute(_DELETE_ROLE_SQL, loser_id)


async def _normalize_typos(conn: asyncpg.Connection, *, execute: bool) -> list[RenameAction]:
    """Rename typo'd titles in place, or merge onto an existing same-org canonical role."""
    actions: list[RenameAction] = []
    for row in await conn.fetch(_TYPO_ROLES_SQL, list(RENAME_MAP)):
        canonical = canonical_rename(row["title"])
        if canonical is None:  # defensive; SQL already filters to RENAME_MAP keys
            continue
        peer = await conn.fetchval(
            _CANONICAL_PEER_SQL, row["organization_id"], canonical.lower(), row["id"]
        )
        action: RenameAction = {
            "role_id": row["id"],
            "organization_id": row["organization_id"],
            "from_title": row["title"],
            "to_title": canonical,
            "target_role_id": peer,
            "status": "would_merge" if peer is not None else "would_rename",
        }
        actions.append(action)
        if not execute:
            continue
        if peer is None:
            await conn.execute(_RENAME_ROLE_SQL, row["id"], canonical)
            action["status"] = "renamed"
            logger.info("Renamed role %s %r → %r", row["id"], row["title"], canonical)
        else:
            await _merge_into_canonical(conn, row["id"], peer)
            action["status"] = "merged"
            logger.info(
                "Merged typo role %s %r into canonical %s (%r)",
                row["id"],
                row["title"],
                peer,
                canonical,
            )
    return actions


async def sweep_role_data_quality(conn: asyncpg.Connection, *, execute: bool) -> Report:
    """Archive artifact roles and normalize typo'd titles. Only mutates when execute."""
    archived = await _archive_artifacts(conn, execute=execute)
    renamed = await _normalize_typos(conn, execute=execute)
    return Report(archived=archived, renamed=renamed)


async def run(*, execute: bool) -> None:
    """Connect to DATABASE_URL and run the sweep."""
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        raise RuntimeError("DATABASE_URL not set")

    conn = await asyncpg.connect(dsn)
    try:
        if execute:
            async with conn.transaction():
                report = await sweep_role_data_quality(conn, execute=True)
        else:
            report = await sweep_role_data_quality(conn, execute=False)

        archive_counts = Counter(a["status"] for a in report["archived"])
        rename_counts = Counter(a["status"] for a in report["renamed"])
        verb = "Swept" if execute else "Dry run — would sweep"
        logger.info(
            "%s: %d artifact role(s) archived (%s), %d title(s) normalized (%s)",
            verb,
            len(report["archived"]),
            ", ".join(f"{k}={v}" for k, v in sorted(archive_counts.items())) or "none",
            len(report["renamed"]),
            ", ".join(f"{k}={v}" for k, v in sorted(rename_counts.items())) or "none",
        )
        if not execute:
            logger.info("Pass --execute to commit")
    finally:
        await conn.close()


def main() -> None:
    configure_logging()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--execute", action="store_true", help="Commit changes (default is dry run)"
    )
    args = parser.parse_args()
    asyncio.run(run(execute=args.execute))


if __name__ == "__main__":
    main()
