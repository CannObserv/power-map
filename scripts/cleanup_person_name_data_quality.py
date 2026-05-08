"""Issue #135 cleanup: surgical fixes to upstream data-quality issues that the
phase-2 analyser surfaced as edge-case rows in `person_names`.

Action types:

- ``StripSuffix``  — UPDATE name in place (drop ``(2)`` dedupe markers, etc.)
- ``SplitName``    — UPDATE the existing legal-name row + INSERT a sibling
                     (``variant`` for alt-spellings / nicknames; ``maiden``
                     for parenthesized maiden surnames)
- ``MergePerson``  — consolidate two `people` records via
                     ``merge_person_into`` (the same primitive the admin
                     route uses)

The actions list at module top is the source of truth for what runs.
Each entry has a comment naming the original DB row + the rationale —
this is the reviewable diff the user signed off on.

Usage:
    uv run python -m scripts.cleanup_person_name_data_quality           # dry run
    uv run python -m scripts.cleanup_person_name_data_quality --execute

Pre-conditions:
    * `DATABASE_URL` set
    * Schema migration that introduced ``name_type='variant'`` applied
      (`apply_schema` will have run that block).
"""

import argparse
import asyncio
import os
from collections import Counter
from dataclasses import dataclass, field
from typing import Literal

import asyncpg

from src.api.admin.people_merge import merge_person_into
from src.core.db import generate_id
from src.core.logging import configure_logging, get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Action types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StripSuffix:
    """In-place UPDATE removing a literal suffix from a name."""

    name_id: str
    new_name: str
    strip: str  # documentation: what's being removed (e.g. " (2)")


@dataclass(frozen=True)
class SplitName:
    """Modify the legal row + INSERT a `variant` or `maiden` sibling.

    Sibling inherits ``locale`` and ``script`` from the legal row, gets
    ``visibility='public'``, ``is_canonical=FALSE``.
    """

    name_id: str            # existing person_names.id whose name is being cleaned
    new_legal_name: str     # what the existing row's name becomes
    sibling_name: str       # full name string for the new sibling row
    sibling_type: Literal["variant", "maiden"]


@dataclass(frozen=True)
class MergePerson:
    """Merge two persons via the canonical merge primitive."""

    winner_id: str
    loser_id: str
    rationale: str


CleanupAction = StripSuffix | SplitName | MergePerson


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------


@dataclass
class CleanupStats:
    applied: int = 0
    dry_run: bool = True
    kind_counts: Counter[str] = field(default_factory=Counter)


# ---------------------------------------------------------------------------
# Action runner
# ---------------------------------------------------------------------------


_ACTOR = "data-quality-cleanup-#135"


async def apply_action(db: asyncpg.Connection, action: CleanupAction) -> None:
    """Execute a single action against `db`. Caller owns the transaction.

    Raises:
        ValueError: when a name_id / person_id is missing — surfaces stale
            cleanup-list entries rather than silently no-op'ing.
    """
    if isinstance(action, StripSuffix):
        result = await db.execute(
            "UPDATE person_names SET name=$1 WHERE id=$2",
            action.new_name, action.name_id,
        )
        # asyncpg returns "UPDATE 0" on no-match.
        if result.endswith(" 0"):
            raise ValueError(
                f"StripSuffix: name_id={action.name_id!r} not found"
            )
        logger.info(
            "strip-suffix: %s -> %r (stripped %r)",
            action.name_id, action.new_name, action.strip,
        )

    elif isinstance(action, SplitName):
        row = await db.fetchrow(
            "SELECT person_id, locale, script, visibility "
            "FROM person_names WHERE id=$1",
            action.name_id,
        )
        if row is None:
            raise ValueError(
                f"SplitName: name_id={action.name_id!r} not found"
            )
        await db.execute(
            "UPDATE person_names SET name=$1 WHERE id=$2",
            action.new_legal_name, action.name_id,
        )
        sibling_id = generate_id()
        await db.execute(
            "INSERT INTO person_names "
            "(id, person_id, name, name_type, is_canonical, "
            " locale, script, visibility) "
            "VALUES ($1, $2, $3, $4, FALSE, $5, $6, $7)",
            sibling_id, row["person_id"], action.sibling_name,
            action.sibling_type, row["locale"], row["script"],
            row["visibility"],
        )
        logger.info(
            "split-name: %s -> legal=%r + %s=%r (sibling_id=%s)",
            action.name_id, action.new_legal_name, action.sibling_type,
            action.sibling_name, sibling_id,
        )

    elif isinstance(action, MergePerson):
        await merge_person_into(
            db,
            winner_id=action.winner_id,
            loser_id=action.loser_id,
            actor_email=_ACTOR,
        )
        logger.info(
            "merge-person: loser=%s -> winner=%s (%s)",
            action.loser_id, action.winner_id, action.rationale,
        )

    else:  # pragma: no cover — exhaustive type union
        raise TypeError(f"unknown action: {type(action).__name__}")


async def run_cleanup(
    db: asyncpg.Connection,
    *,
    actions: list[CleanupAction],
    dry_run: bool = True,
) -> CleanupStats:
    """Run all actions inside a single savepoint. Atomic — any error rolls
    back every action that ran before it.

    `dry_run=True` rolls the savepoint back even on success.
    """
    stats = CleanupStats(dry_run=dry_run)
    sp = db.transaction()
    await sp.start()
    try:
        for action in actions:
            await apply_action(db, action)
            stats.applied += 1
            stats.kind_counts[type(action).__name__] += 1
    except Exception:
        await sp.rollback()
        raise

    if dry_run:
        await sp.rollback()
    else:
        await sp.commit()
    return stats


# ---------------------------------------------------------------------------
# Curated cleanup actions for issue #135 (production data state at 2026-05-08).
# Each entry comments the source row name so the diff is reviewable.
# ---------------------------------------------------------------------------


CLEANUP_ACTIONS: list[CleanupAction] = [
    # --- Bucket B: alt-spelling / uncertain-spelling rows ----------------------
    # Legal becomes the FIRST given name (canonical); second becomes variant.
    SplitName(
        name_id="01KM1CTMRNZ07T5ERS5DN165SE",  # 'Rene or Renee'
        new_legal_name="Rene", sibling_name="Renee", sibling_type="variant",
    ),
    SplitName(
        name_id="01KM1CTKNHT6BKNEC24W2S96JA",  # 'Libby/Lynn Rindal'
        new_legal_name="Libby Rindal", sibling_name="Lynn Rindal",
        sibling_type="variant",
    ),
    SplitName(
        name_id="01KM1CTM6B9M3CF8CH78KSMQBM",  # 'Micael (Michael?) Tsegai'
        new_legal_name="Micael Tsegai", sibling_name="Michael Tsegai",
        sibling_type="variant",
    ),

    # --- Bucket C: parenthesized markers ---------------------------------------
    # Strip the (2) dedupe-disambiguation markers — those should never have
    # ended up in `name`. Leaves the row otherwise untouched.
    StripSuffix(
        name_id="01KM1CTKPE5FP8D8PMFERVYEM9",  # 'Linda Thompson (2)'
        new_name="Linda Thompson", strip=" (2)",
    ),
    StripSuffix(
        name_id="01KM1CTM08M02DQJSKGBJKWXDT",  # 'Mary Brown (2)'
        new_name="Mary Brown", strip=" (2)",
    ),
    StripSuffix(
        name_id="01KM1CTNFS4K611707B60QPDGS",  # 'Shannon Angell (2)'
        new_name="Shannon Angell", strip=" (2)",
    ),
    # Inline alt-spelling / nickname / maiden parens.
    SplitName(
        name_id="01KM1CTMCFMK4N558AXCZ8HD18",  # 'Myra (Mayra) Hernandez'
        new_legal_name="Myra Hernandez", sibling_name="Mayra Hernandez",
        sibling_type="variant",
    ),
    SplitName(
        name_id="01KM1CTNZSRRYWG444M5GVAYZ1",  # 'Victor (Vic) Colman'
        new_legal_name="Victor Colman", sibling_name="Vic Colman",
        sibling_type="variant",
    ),
    SplitName(
        name_id="01KM1CTP05ZKAJ8WNDFHY3VGQX",  # 'Virginia (Webber) Hoyer'
        new_legal_name="Virginia Hoyer", sibling_name="Virginia Webber",
        sibling_type="maiden",
    ),

    # --- Bucket D: quoted nickname surfaces as a separate variant row ----------
    SplitName(
        name_id="01KM1CTKFMSJXTYHNE6SYFQR7W",  # 'Kristopher "Kip" Hill'
        new_legal_name="Kristopher Hill", sibling_name="Kip Hill",
        sibling_type="variant",
    ),

    # --- Bucket B (continued): split BEFORE merge so both records carry both
    # spellings; the merge's exact-name dedup collapses them cleanly.
    SplitName(
        name_id="01KM1CTK12XJ3MHQPFFZ6WR5SP",  # 'Jodi/Jody' (winner — older + has notes)
        new_legal_name="Jodi", sibling_name="Jody", sibling_type="variant",
    ),
    SplitName(
        name_id="01KM234TX6NV6W2M8ZQFYR5VX7",  # 'Jody or Jodi' (loser)
        new_legal_name="Jody", sibling_name="Jodi", sibling_type="variant",
    ),
    MergePerson(
        winner_id="01KM1CTK11KGSM3P7AF84NKPFB",  # Jodi/Jody person
        loser_id="01KM234TX521Q0P7D05ZBWZS8D",   # Jody or Jodi person
        rationale="Same person — uncertainty about Jodi vs Jody spelling.",
    ),
]


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


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
        result = await run_cleanup(conn, actions=CLEANUP_ACTIONS, dry_run=dry_run)
    finally:
        await conn.close()

    mode = "DRY RUN" if result.dry_run else "EXECUTED"
    print(f"\n[{mode}] person_names data-quality cleanup:")
    print(f"  total actions:  {result.applied}")
    for kind, count in sorted(result.kind_counts.items()):
        print(f"      {kind:<14} {count:>3}")
    if result.dry_run:
        print("\nRe-run with --execute to apply changes.")


if __name__ == "__main__":
    asyncio.run(_main())
