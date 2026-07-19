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
from dataclasses import dataclass, field

import asyncpg

from src.core.logging import configure_logging, get_logger
from src.core.observation import _NAME_TYPE_PRIORITY_SQL, NO_AUTO_CANONICAL_NAME_TYPES

logger = get_logger(__name__)


@dataclass(frozen=True)
class PromoteCandidate:
    """The one name PM would display for this person, ready to be promoted."""

    person_id: str
    name_id: str
    name: str
    name_type: str


@dataclass
class BackfillStats:
    promoted: int = 0  # names promoted to canonical
    blocked: int = 0  # every candidate's slot held by a non-public canonical
    multi_name: int = 0  # promoted people who had >1 eligible name (priority decided)
    dry_run: bool = True
    promoted_ids: list[str] = field(default_factory=list)
    blocked_ids: list[str] = field(default_factory=list)


# One SQL ladder, shared with core.observation, which is in turn asserted against
# v_person_display_names (#308, CR3 #26). Selection here must agree with the heal
# pass — otherwise the backfill defers a choice the next observation makes anyway,
# and the two paths can disagree about which name a person displays.
_PROMOTABLE_SQL = f"""
SELECT n.person_id,
       n.id   AS name_id,
       n.name,
       n.name_type,
       {_NAME_TYPE_PRIORITY_SQL.replace("name_type", "n.name_type")} AS rank,
       NOT EXISTS (
           SELECT 1 FROM person_names x
           WHERE x.person_id = n.person_id
             AND x.is_canonical = TRUE
             AND x.name_type = n.name_type
             AND COALESCE(x.locale, '') = COALESCE(n.locale, '')
             AND COALESCE(x.script, '') = COALESCE(n.script, '')
       ) AS slot_free
FROM person_names n
WHERE n.visibility = 'public'
  AND n.is_canonical = FALSE
  AND n.name_type <> ALL($1::text[])
  AND NOT EXISTS (
      SELECT 1 FROM person_names c
      WHERE c.person_id = n.person_id
        AND c.is_canonical = TRUE
        AND c.visibility = 'public'
  )
"""


async def find_candidates(db: asyncpg.Connection) -> list[PromoteCandidate]:
    """One promotable name per canonical-less person, chosen by display priority.

    Ambiguity is resolved, not deferred: `_heal_person_canonical` promotes the
    top-priority name on that person's next observation regardless, so leaving
    them for a human only delayed the same decision while hiding it from the
    operator. Ties break on `id`, matching the heal and the display view.

    Excludes people whose every candidate sits in an occupied unique-index slot —
    promoting those raises UniqueViolationError, and since the run is one
    savepoint that would roll back every other promotion.
    """
    rows = await db.fetch(
        f"""
        SELECT DISTINCT ON (person_id) person_id, name_id, name, name_type
        FROM ({_PROMOTABLE_SQL}) p
        WHERE slot_free
        ORDER BY person_id, rank, name_id
        """,
        list(NO_AUTO_CANONICAL_NAME_TYPES),
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


async def find_blocked(db: asyncpg.Connection) -> list[str]:
    """Canonical-less people with eligible names but no free slot among them.

    Disjoint from `find_candidates` by construction (#308, CR3 #18): a person with
    even one free-slot candidate is promotable and is not reported here.
    Unrepairable by promotion — freeing the slot means demoting a curated
    non-public canonical, which is a human decision, not a script's.
    """
    rows = await db.fetch(
        f"""
        SELECT person_id
        FROM ({_PROMOTABLE_SQL}) p
        GROUP BY person_id
        HAVING bool_and(NOT slot_free)
        ORDER BY person_id
        """,
        list(NO_AUTO_CANONICAL_NAME_TYPES),
    )
    return [r["person_id"] for r in rows]


async def count_multi_name(db: asyncpg.Connection) -> int:
    """Promotable people carrying >1 eligible name — reported, not skipped."""
    return await db.fetchval(
        f"""
        SELECT count(*) FROM (
            SELECT person_id FROM ({_PROMOTABLE_SQL}) p
            GROUP BY person_id
            HAVING count(*) FILTER (WHERE slot_free) > 1
        ) s
        """,
        list(NO_AUTO_CANONICAL_NAME_TYPES),
    )


async def run_backfill(
    db: asyncpg.Connection,
    *,
    dry_run: bool = True,
) -> BackfillStats:
    """Promote every candidate inside a single savepoint.

    Atomic — any error rolls back the whole run. ``dry_run=True`` rolls back even
    on success, so the printed counts reflect exactly what ``--execute`` would do.

    Because the run is all-or-nothing, candidate selection must exclude anything
    that could raise: `find_candidates` filters out occupied slots (counted as
    ``blocked``) so one unrepairable person cannot abort every other promotion.
    """
    stats = BackfillStats(dry_run=dry_run)
    stats.blocked_ids = await find_blocked(db)
    stats.blocked = len(stats.blocked_ids)
    stats.multi_name = await count_multi_name(db)

    sp = db.transaction()
    await sp.start()
    try:
        for cand in await find_candidates(db):
            await db.execute(
                "UPDATE person_names SET is_canonical = TRUE WHERE id = $1", cand.name_id
            )
            stats.promoted += 1
            stats.promoted_ids.append(cand.person_id)
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
    print(f"    of which >1 name:  {result.multi_name} (display priority decided)")
    print(f"  blocked (skipped):   {result.blocked}")
    if result.blocked:
        print("  → canonical slot held by a non-public name; demote it in admin first.")
    if result.dry_run:
        print("\nRe-run with --execute to apply changes.")


if __name__ == "__main__":
    asyncio.run(_main())
