"""Issue #308c backfill: promote the sole public name of every canonical-less
person, so they stop rendering blank in admin and on the public API.

Background
----------
``core.observation.write_names`` used to drive person-name canonicality *only*
from the client's ``is_canonical`` hint — unlike the org branch, which has always
auto-promoted the first name it sees. A client that omits the hint (usa-wa does,
deliberately: PM is system-of-record for the canonical name) therefore produced
people with names but no canonical name, and ``v_person_display_names`` only
surfaces ``is_canonical = TRUE`` rows, so those people render as nothing.

#308b closed the source of the drift by giving the person branch the same
first-wins auto-promotion. This script repairs the people already in that state
(567 at 2026-07-18, all from the usa-wa legislator/person enrichment cohorts —
ULID prefixes ``01KWWW``, ``01KXNX``, ``01KXNY``, ``01KXP0``).

Selection rule
--------------
A person is a candidate when they have **no** public canonical name and
**exactly one** eligible public name — eligible meaning a name_type that is a
plausible display name (``NO_AUTO_CANONICAL_NAME_TYPES`` from
``core.observation`` is excluded: deadnames and machine-readable renderings).

The "exactly one" bound is deliberate. Where several eligible names compete, the
right pick is a curation decision, not a script's — those people are counted and
reported as ``ambiguous`` for human follow-up rather than guessed at. At
2026-07-18 there are none: all 567 carry a single public ``legal`` name.

Idempotent — a second run finds nothing to do. Promotion writes ``person_names``,
which fires ``trg_touch_person_on_name_change`` → ``people.updated_at`` →
an ``entity_changes`` ``'updated'`` row, so subscribers re-fetch and pick up the
newly-visible name.

Usage:
    uv run python -m scripts.backfill_person_canonical_names            # dry run
    uv run python -m scripts.backfill_person_canonical_names --execute
"""

import argparse
import asyncio
import os
from dataclasses import dataclass

import asyncpg

from src.core.logging import configure_logging, get_logger
from src.core.observation import NO_AUTO_CANONICAL_NAME_TYPES

logger = get_logger(__name__)


@dataclass(frozen=True)
class PromoteCandidate:
    """A person whose sole eligible public name should become canonical."""

    person_id: str
    name_id: str
    name: str
    name_type: str


@dataclass
class BackfillStats:
    promoted: int = 0  # names promoted to canonical
    ambiguous: int = 0  # canonical-less people with >1 eligible name — skipped
    dry_run: bool = True


def _eligible(alias: str) -> str:
    """Predicate: this person_names row could serve as a display name.

    Alias-parameterised because the same test is applied to the outer row and to
    a correlated count subquery over a second alias.
    """
    return f"{alias}.visibility = 'public' AND {alias}.name_type <> ALL($1::text[])"


def _no_public_canonical(alias: str) -> str:
    """Predicate: this row's person has no public canonical name."""
    return f"""NOT EXISTS (
              SELECT 1 FROM person_names c
              WHERE c.person_id = {alias}.person_id
                AND c.is_canonical = TRUE
                AND c.visibility = 'public'
          )"""


async def find_candidates(db: asyncpg.Connection) -> list[PromoteCandidate]:
    """People with no public canonical name and exactly one eligible public name."""
    excluded = list(NO_AUTO_CANONICAL_NAME_TYPES)
    rows = await db.fetch(
        f"""
        SELECT n.person_id, n.id AS name_id, n.name, n.name_type
        FROM person_names n
        WHERE {_eligible("n")}
          AND {_no_public_canonical("n")}
          AND (
              SELECT count(*) FROM person_names s
              WHERE s.person_id = n.person_id AND {_eligible("s")}
          ) = 1
        ORDER BY n.person_id
        """,
        excluded,
    )
    return [
        PromoteCandidate(
            person_id=r["person_id"],
            name_id=r["name_id"],
            name=r["name"],
            name_type=r["name_type"],
        )
        for r in rows
    ]


async def count_ambiguous(db: asyncpg.Connection) -> int:
    """Canonical-less people carrying >1 eligible public name — left for a human."""
    excluded = list(NO_AUTO_CANONICAL_NAME_TYPES)
    return await db.fetchval(
        f"""
        SELECT count(*) FROM (
            SELECT n.person_id
            FROM person_names n
            WHERE {_eligible("n")}
              AND {_no_public_canonical("n")}
            GROUP BY n.person_id
            HAVING count(*) > 1
        ) s
        """,
        excluded,
    )


async def run_backfill(
    db: asyncpg.Connection,
    *,
    dry_run: bool = True,
) -> BackfillStats:
    """Promote every candidate inside a single savepoint.

    Atomic — any error rolls back the whole run. ``dry_run=True`` rolls back even
    on success, so the printed counts reflect exactly what ``--execute`` would do.
    """
    stats = BackfillStats(dry_run=dry_run)
    stats.ambiguous = await count_ambiguous(db)

    sp = db.transaction()
    await sp.start()
    try:
        for cand in await find_candidates(db):
            await db.execute(
                "UPDATE person_names SET is_canonical = TRUE WHERE id = $1", cand.name_id
            )
            stats.promoted += 1
            logger.info(
                "promote person=%s name=%r name_type=%s",
                cand.person_id,
                cand.name,
                cand.name_type,
            )
    except Exception:
        await sp.rollback()
        raise

    if dry_run:
        await sp.rollback()
    else:
        await sp.commit()
    return stats


async def _main() -> None:
    configure_logging()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Commit changes. Default is dry run (no changes made).",
    )
    args = parser.parse_args()
    dry_run = not args.execute

    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        raise SystemExit("DATABASE_URL environment variable is required")

    conn = await asyncpg.connect(dsn)
    try:
        result = await run_backfill(conn, dry_run=dry_run)
    finally:
        await conn.close()

    mode = "DRY RUN" if result.dry_run else "EXECUTED"
    print(f"\n[{mode}] person canonical-name backfill (#308c):")
    print(f"  names promoted:      {result.promoted}")
    print(f"  ambiguous (skipped): {result.ambiguous}")
    if result.ambiguous:
        print("  → >1 eligible public name; promote by hand in admin.")
    if result.dry_run:
        print("\nRe-run with --execute to apply changes.")


if __name__ == "__main__":
    asyncio.run(_main())
