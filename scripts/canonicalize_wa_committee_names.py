"""Issue #254 cleanup: canonicalize the clean name on 21 WA Joint/`Other`
legislative-committee orgs left mis-canonicalized by a producer (usa-wa)
ordering accident.

Background
---------
usa-wa ingests Joint/`Other` committees from WSL's ``CommitteeMeetingService``.
WSL's meeting serializer is deterministic — ``LongName == f"{Agency} {Name}"`` —
so the un-prefixed ``Name`` is the clean form. The first observations (before
usa-wa#61) asserted the agency-prefixed ``LongName``, which PM auto-canonicalized
(first-name-wins). For Joint/`Other` committees ``Agency`` *is* "Joint"/"Other",
so the result is a double-prefixed garbage canonical name like
"Joint **Joint** Transportation Committee" or "**Other** Statute Law Committee".

usa-wa#61 corrected the producer to assert the clean ``short_name``, so each of
the 21 orgs already carries the clean name as an ``is_canonical=false`` `legal`
observation. usa-wa deliberately never asserts ``is_canonical`` (PM is
system-of-record for the canonical name), so this curation can only happen here.

Action (one per org)
---------------------
Within the caller's transaction:

1. **Delete** the prefixed garbage `legal` row (it was never a real name; it
   won't be re-asserted — usa-wa#61 emits only the clean name; and
   ``core.observation.write_names`` skips names that already exist and never
   displaces an existing canonical, so the promotion is durable).
2. **Promote** the clean name to ``is_canonical=TRUE``.

Deleting the prefixed row *before* promoting frees the single-canonical slot
(``uq_org_canonical_name``). The action is idempotent: a re-run finds the
prefixed row already gone and the clean name already canonical, and no-ops.

Both DB writes touch ``organizations`` (via the ``touch_parent_org`` /
search-tsv triggers), so each org emits an ``entity_changes`` ``'updated'`` row
— consumers (usa-wa) re-fetch and see the corrected canonical name.

The ``CANONICALIZE_ACTIONS`` list at module bottom is the reviewable
source of truth — generated from the production DB state on 2026-06-30.

Usage:
    uv run python -m scripts.canonicalize_wa_committee_names            # dry run
    uv run python -m scripts.canonicalize_wa_committee_names --execute
"""

import argparse
import asyncio
from dataclasses import dataclass

import asyncpg

from scripts._dsn import add_dsn_args, resolve_dsn
from src.core.logging import configure_logging, get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Action + result types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CanonicalizeName:
    """Promote ``clean_name`` to canonical and delete the ``prefixed_name`` row
    on a single org. Both names are expected to already exist as `legal` rows."""

    org_id: str
    clean_name: str  # promote -> is_canonical=TRUE
    prefixed_name: str  # delete (retire the agency-double-prefixed garbage row)


@dataclass
class ActionOutcome:
    org_id: str
    promoted: bool  # clean name was promoted to canonical this run
    deleted_prefixed: bool  # prefixed row was deleted this run


@dataclass
class CanonStats:
    applied: int = 0  # actions processed
    promoted: int = 0  # clean names promoted to canonical
    deleted_prefixed: int = 0  # prefixed rows deleted
    dry_run: bool = True


# ---------------------------------------------------------------------------
# Action runner
# ---------------------------------------------------------------------------


async def apply_action(db: asyncpg.Connection, action: CanonicalizeName) -> ActionOutcome:
    """Execute a single canonicalization against ``db``. Caller owns the transaction.

    Idempotent: re-running after success is a no-op. Raises ``ValueError`` when
    the clean name is absent — that means the action list is stale, which we
    surface rather than silently promoting nothing.
    """
    clean = await db.fetchrow(
        "SELECT id, is_canonical FROM organization_names WHERE organization_id=$1 AND name=$2",
        action.org_id,
        action.clean_name,
    )
    if clean is None:
        raise ValueError(
            f"clean name {action.clean_name!r} not found on org {action.org_id} — stale action list"
        )

    prefixed = await db.fetchrow(
        "SELECT id FROM organization_names WHERE organization_id=$1 AND name=$2",
        action.org_id,
        action.prefixed_name,
    )

    # Delete the prefixed (garbage) row first to free the single-canonical slot.
    deleted_prefixed = False
    if prefixed is not None:
        await db.execute("DELETE FROM organization_names WHERE id=$1", prefixed["id"])
        deleted_prefixed = True

    # Promote the clean name (no-op if it is already canonical).
    promoted = False
    if not clean["is_canonical"]:
        await db.execute("UPDATE organization_names SET is_canonical=TRUE WHERE id=$1", clean["id"])
        promoted = True

    logger.info(
        "canonicalize org=%s promote=%r (was_canonical=%s) delete_prefixed=%r (found=%s)",
        action.org_id,
        action.clean_name,
        clean["is_canonical"],
        action.prefixed_name,
        prefixed is not None,
    )
    return ActionOutcome(org_id=action.org_id, promoted=promoted, deleted_prefixed=deleted_prefixed)


async def run_cleanup(
    db: asyncpg.Connection,
    *,
    actions: list[CanonicalizeName],
    dry_run: bool = True,
) -> CanonStats:
    """Run all actions inside a single savepoint. Atomic — any error rolls back
    every action that ran before it. ``dry_run=True`` rolls back even on success.
    """
    stats = CanonStats(dry_run=dry_run)
    sp = db.transaction()
    await sp.start()
    try:
        for action in actions:
            outcome = await apply_action(db, action)
            stats.applied += 1
            stats.promoted += int(outcome.promoted)
            stats.deleted_prefixed += int(outcome.deleted_prefixed)
    except Exception:
        await sp.rollback()
        raise

    if dry_run:
        await sp.rollback()
    else:
        await sp.commit()
    return stats


# ---------------------------------------------------------------------------
# Curated actions for issue #254 (production DB state at 2026-06-30).
# Each org currently has the prefixed name canonical + the clean name
# non-canonical. Generated from the live DB so the strings match exactly.
# ---------------------------------------------------------------------------


CANONICALIZE_ACTIONS: list[CanonicalizeName] = [
    CanonicalizeName(
        org_id="01KWBJ5XAP2RRFKET5WPRAMVS9",
        clean_name="Legislative Evaluation & Accountability Program",
        prefixed_name="Other Legislative Evaluation & Accountability Program",
    ),
    CanonicalizeName(
        org_id="01KWCEM8NVPMGNENYAW3QZ7N4J",
        clean_name="Joint Administrative Rules Review Committee",
        prefixed_name="Joint Joint Administrative Rules Review Committee",
    ),
    CanonicalizeName(
        org_id="01KWBJ5VW52ZCFTZQKFJYHGBS1",
        clean_name="Joint Transportation Committee",
        prefixed_name="Joint Joint Transportation Committee",
    ),
    CanonicalizeName(
        org_id="01KWBJ5V4D8ZTXTNMBRG0SKV44",
        clean_name="Statute Law Committee",
        prefixed_name="Other Statute Law Committee",
    ),
    CanonicalizeName(
        org_id="01KWBJ5Y08SNNWHQ6ZQANT14MX",
        clean_name="Joint Committee on Veterans' & Military Affairs",
        prefixed_name="Joint Joint Committee on Veterans' & Military Affairs",
    ),
    CanonicalizeName(
        org_id="01KWBJ5VG6J4SWDB0YM99AVE39",
        clean_name="Select Committee on Pension Policy",
        prefixed_name="Joint Select Committee on Pension Policy",
    ),
    CanonicalizeName(
        org_id="01KWBJ5T1BA5FNCE16K85AENKG",
        clean_name="JLARC - Joint Legislative Audit & Review Committee",
        prefixed_name="Joint JLARC - Joint Legislative Audit & Review Committee",
    ),
    CanonicalizeName(
        org_id="01KWBJ5YBS8GCJGA3J3WPCT4YR",
        clean_name="Pension Funding Council",
        prefixed_name="Joint Pension Funding Council",
    ),
    CanonicalizeName(
        org_id="01KWCEM84DMNBSWAX6TDCZW522",
        clean_name="Legislative Committee on Economic Development & International Relations",
        prefixed_name=(
            "Joint Legislative Committee on Economic Development & International Relations"
        ),
    ),
    CanonicalizeName(
        org_id="01KWBJ5YQ8V62VWAK8EWJ7MTZQ",
        clean_name="Joint Committee on Energy Supply, Energy Conservation, and Energy Resilience",
        prefixed_name=(
            "Joint Joint Committee on Energy Supply, Energy Conservation, and Energy Resilience"
        ),
    ),
    CanonicalizeName(
        org_id="01KWBJ5WKM0CDQVSH92HK2VVQB",
        clean_name="Joint Legislative Committee on Water Supply During Drought",
        prefixed_name="Joint Joint Legislative Committee on Water Supply During Drought",
    ),
    CanonicalizeName(
        org_id="01KWBJ5Z2W1YQGH9WDC4KGRZVA",
        clean_name="Joint Higher Education Committee",
        prefixed_name="Joint Joint Higher Education Committee",
    ),
    CanonicalizeName(
        org_id="01KWBJ5ZERWWVZ97RMDYDZZ78S",
        clean_name="Joint Select Committee on Health Care and Behavioral Health Oversight",
        prefixed_name=(
            "Joint Joint Select Committee on Health Care and Behavioral Health Oversight"
        ),
    ),
    CanonicalizeName(
        org_id="01KWBJ5W7XZH8G1EZB4FSPPVG9",
        clean_name=(
            "Joint Legislative Executive Committee on Planning for Aging and Disability Issues"
        ),
        prefixed_name=(
            "Joint Joint Legislative Executive Committee on Planning for Aging"
            " and Disability Issues"
        ),
    ),
    CanonicalizeName(
        org_id="01KWBJ5TRS03T0MFRF61CJJ262",
        clean_name="Citizen Commission for Performance Measurement of Tax Preferences",
        prefixed_name="Other Citizen Commission for Performance Measurement of Tax Preferences",
    ),
    CanonicalizeName(
        org_id="01KWBJ5TCSCCQGEG3F736G3FVX",
        clean_name="JLARC I-900 Subcommittee for SAO Performance Audits",
        prefixed_name="Other JLARC I-900 Subcommittee for SAO Performance Audits",
    ),
    CanonicalizeName(
        org_id="01KWBJ5ZT9J31MYT0TQTR4X0XG",
        clean_name="Legislative Oral History Committee",
        prefixed_name="Joint Legislative Oral History Committee",
    ),
    CanonicalizeName(
        org_id="01KWBJ5SFFNQKDFXGENPB84C8F",
        clean_name="Joint Committee on Employment Relations",
        prefixed_name="Joint Joint Committee on Employment Relations",
    ),
    CanonicalizeName(
        org_id="01KWBJ5WZ4Y6Q6RV75PTEJGRBK",
        clean_name="Joint Oregon-Washington Legislative Action Committee",
        prefixed_name="Joint Joint Oregon-Washington Legislative Action Committee",
    ),
    CanonicalizeName(
        org_id="01KWCEM91W71YAZ5YZ9CQ2C3BP",
        clean_name="Joint Select Committee on Governance and Funding for Institutional Education",
        prefixed_name=(
            "Joint Joint Select Committee on Governance and Funding for Institutional Education"
        ),
    ),
    CanonicalizeName(
        org_id="01KWBJ5XPG5GP7HEMSBMPEVJV7",
        clean_name="Joint Select Committee on Civic Health",
        prefixed_name="Joint Joint Select Committee on Civic Health",
    ),
]


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


async def _main() -> None:
    configure_logging()
    parser = argparse.ArgumentParser(description=__doc__)
    add_dsn_args(parser)
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Commit changes. Default is dry run (no changes made).",
    )
    args = parser.parse_args()
    dsn = resolve_dsn(args, parser)
    dry_run = not args.execute

    conn = await asyncpg.connect(dsn)
    try:
        result = await run_cleanup(conn, actions=CANONICALIZE_ACTIONS, dry_run=dry_run)
    finally:
        await conn.close()

    mode = "DRY RUN" if result.dry_run else "EXECUTED"
    print(f"\n[{mode}] WA committee canonical-name cleanup (#254):")
    print(f"  actions processed:   {result.applied}")
    print(f"  names promoted:      {result.promoted}")
    print(f"  prefixed deleted:    {result.deleted_prefixed}")
    if result.dry_run:
        print("\nRe-run with --execute to apply changes.")


if __name__ == "__main__":
    asyncio.run(_main())
