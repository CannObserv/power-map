"""Un-collapse the #467 Transportation-committee merge into the succession shape (#469).

The pre-#467 org merge folded WSL committee ``3532`` ("House Committee on
Transportation") into ``31651`` ("Washington State House Transportation
Committee"): the loser org and its Member role were hard-deleted, and the 136
pre-2021 assignments were migrated onto the winner's Member role under
**reminted** ULIDs — the history survived, every producer-held anchor broke.

This restores the two-source-record shape the #469 design canonizes:

1. Resurrect the predecessor org and its Member role under their **original
   ULIDs** (the PKs are free after a hard delete) and clear their
   ``deleted_entities`` tombstones, so the ids stop resolving as both dead and
   alive. This heals usa-wa's org + role anchors with no producer-side action.
2. Move identifier ``3532`` back to the predecessor — one WSL id, one org.
3. Re-point the pre-2021 assignments (start_date < the cutoff) onto the
   resurrected role. Their reminted ULIDs are kept: PM never recorded the
   old→new mapping, so the producer re-resolves those anchors by natural key
   (see CannObserv/usa-wa#283).
4. Record a year-2020 ``succeeded_by`` event (predecessor → winner), which
   also derives the predecessor's lifespan end (2020-12-31).
5. Re-subscribe the winner's current watchers to the resurrected ids (#479).
   Steps 1–4 all emit outbox rows, but ``GET /changes`` joins on **current**
   subscriptions: a consumer that processed the merge tombstone and correctly
   unsubscribed from the retired id receives none of them, including the ones
   saying it came back. PM does not record the delete-time audience, so the
   winner's watchers stand in for it — an approximation, and exactly right in
   the case this script exists for.

Idempotent: every step no-ops when its outcome is already in place. All writes
ride ordinary triggers, so the change feed announces each touched row.

Usage (via ``uv run --env-file /etc/power-map/.env``):
    python -m scripts.restore_467_committee_succession            # report (read-only)
    python -m scripts.restore_467_committee_succession --execute  # apply
"""

import argparse
import asyncio
import datetime
from dataclasses import dataclass

import asyncpg

from scripts._dsn import add_dsn_args, resolve_dsn
from src.core.db import generate_id
from src.core.logging import configure_logging, get_logger
from src.core.merge_signals import copy_subscriptions

logger = get_logger(__name__)


@dataclass(frozen=True)
class RestorePlan:
    """Everything the restore needs — parameterized so tests run synthetic ids."""

    winner_org_id: str
    predecessor_org_id: str
    predecessor_org_name: str
    predecessor_role_id: str
    winner_member_role_id: str
    moved_identifier_value: str
    identifier_type_slug: str
    cutoff: datetime.date
    succession_year: int


# The #467 production facts (issue CannObserv/power-map#467).
PROD_PLAN = RestorePlan(
    winner_org_id="01KVGJM6Y7X5H0AAMMBVPH8NDJ",
    predecessor_org_id="01KWJA2SFGTT84H4EW13QT1F2B",
    predecessor_org_name="House Committee on Transportation",
    predecessor_role_id="01KXPB892XS90RX227MG5SJYRB",
    winner_member_role_id="01KWWWSRA7YWDM8K6P44CFDSAP",
    moved_identifier_value="3532",
    identifier_type_slug="org_wa_legislature_committee_id",
    # Winner's own cohort starts with the 2021-22 biennium; everything earlier
    # is the migrated loser history.
    cutoff=datetime.date(2021, 1, 1),
    succession_year=2020,
)


async def _resurrect_org(conn: asyncpg.Connection, plan: RestorePlan, *, execute: bool) -> bool:
    """Re-insert the predecessor org (original ULID) with its canonical name.

    Parent and jurisdiction affiliation are copied from the winner — same
    chamber, same jurisdiction. ``active`` = FALSE: the manifestation ended.
    """
    if await conn.fetchval("SELECT 1 FROM organizations WHERE id=$1", plan.predecessor_org_id):
        logger.info("org %s already exists — skipping resurrection", plan.predecessor_org_id)
        return False
    logger.info(
        "%s org %s (%r)",
        "resurrecting" if execute else "would resurrect",
        plan.predecessor_org_id,
        plan.predecessor_org_name,
    )
    if not execute:
        return True
    await conn.execute(
        """INSERT INTO organizations (id, parent_id, active)
           SELECT $1, o.parent_id, FALSE FROM organizations o WHERE o.id = $2""",
        plan.predecessor_org_id,
        plan.winner_org_id,
    )
    await conn.execute(
        "INSERT INTO organization_names (id, organization_id, name, name_type, is_canonical)"
        " VALUES ($1, $2, $3, 'legal', TRUE)",
        generate_id(),
        plan.predecessor_org_id,
        plan.predecessor_org_name,
    )
    for aff in await conn.fetch(
        "SELECT jurisdiction_id, affiliation_type_id"
        " FROM organization_jurisdiction_affiliations WHERE organization_id=$1",
        plan.winner_org_id,
    ):
        await conn.execute(
            "INSERT INTO organization_jurisdiction_affiliations"
            " (id, organization_id, jurisdiction_id, affiliation_type_id)"
            " VALUES ($1, $2, $3, $4)",
            generate_id(),
            plan.predecessor_org_id,
            aff["jurisdiction_id"],
            aff["affiliation_type_id"],
        )
    return True


async def _resurrect_role(conn: asyncpg.Connection, plan: RestorePlan, *, execute: bool) -> bool:
    """Re-insert the Member role (original ULID) under the predecessor org."""
    if await conn.fetchval("SELECT 1 FROM roles WHERE id=$1", plan.predecessor_role_id):
        logger.info("role %s already exists — skipping resurrection", plan.predecessor_role_id)
        return False
    logger.info(
        "%s role %s under org %s",
        "resurrecting" if execute else "would resurrect",
        plan.predecessor_role_id,
        plan.predecessor_org_id,
    )
    if not execute:
        return True
    await conn.execute(
        """INSERT INTO roles (id, organization_id, title, role_type_id)
           SELECT $1, $2, r.title, r.role_type_id FROM roles r WHERE r.id = $3""",
        plan.predecessor_role_id,
        plan.predecessor_org_id,
        plan.winner_member_role_id,
    )
    return True


async def _clear_tombstones(conn: asyncpg.Connection, plan: RestorePlan, *, execute: bool) -> int:
    """Drop the deleted_entities rows for the resurrected ids."""
    rows = await conn.fetch(
        """SELECT entity_type, entity_id FROM deleted_entities
           WHERE (entity_type='organization' AND entity_id=$1)
              OR (entity_type='role' AND entity_id=$2)""",
        plan.predecessor_org_id,
        plan.predecessor_role_id,
    )
    for r in rows:
        logger.info(
            "%s tombstone %s/%s",
            "clearing" if execute else "would clear",
            r["entity_type"],
            r["entity_id"],
        )
    if execute and rows:
        await conn.execute(
            """DELETE FROM deleted_entities
               WHERE (entity_type='organization' AND entity_id=$1)
                  OR (entity_type='role' AND entity_id=$2)""",
            plan.predecessor_org_id,
            plan.predecessor_role_id,
        )
    return len(rows)


async def _move_identifier(conn: asyncpg.Connection, plan: RestorePlan, *, execute: bool) -> bool:
    """Point the predecessor's WSL id back at the predecessor."""
    row = await conn.fetchrow(
        """SELECT i.id, i.entity_id FROM identifiers i
           JOIN entity_identifier_types t ON t.id = i.entity_identifier_type_id
           WHERE t.slug = $1 AND i.value = $2""",
        plan.identifier_type_slug,
        plan.moved_identifier_value,
    )
    if row is None:
        logger.warning(
            "identifier %s=%s not found — nothing to move",
            plan.identifier_type_slug,
            plan.moved_identifier_value,
        )
        return False
    if row["entity_id"] == plan.predecessor_org_id:
        logger.info("identifier %s already on the predecessor — skipping", row["id"])
        return False
    if row["entity_id"] != plan.winner_org_id:
        logger.warning(
            "identifier %s is on unexpected org %s (not the winner) — leaving it alone",
            row["id"],
            row["entity_id"],
        )
        return False
    logger.info(
        "%s identifier %s=%s → org %s",
        "moving" if execute else "would move",
        plan.identifier_type_slug,
        plan.moved_identifier_value,
        plan.predecessor_org_id,
    )
    if execute:
        await conn.execute(
            "UPDATE identifiers SET entity_id=$1 WHERE id=$2",
            plan.predecessor_org_id,
            row["id"],
        )
    return True


async def _repoint_assignments(
    conn: asyncpg.Connection, plan: RestorePlan, *, execute: bool
) -> tuple[int, int]:
    """Move pre-cutoff assignments from the winner's Member role to the resurrected role.

    Returns ``(moved, current_moved)`` — the second is the #307 warning count.

    NULL-start rows are reported but never moved — the cutoff cannot classify
    them; resolve those by hand if any appear.
    """
    null_start = await conn.fetchval(
        "SELECT count(*) FROM role_assignments WHERE role_id=$1 AND start_date IS NULL",
        plan.winner_member_role_id,
    )
    if null_start:
        logger.warning(
            "%d assignment(s) on the winner role have NULL start_date — "
            "the cutoff cannot classify them; left untouched, resolve by hand",
            null_start,
        )
    # #469 CR: a current row landing on the ended predecessor violates the hard
    # #307 invariant (org ended → no is_current assignment). Count before the
    # move so both modes report it; prod pre-check showed 0, so a non-zero here
    # means the data changed since — stop and look before trusting --execute.
    current_moved = await conn.fetchval(
        "SELECT count(*) FROM role_assignments WHERE role_id=$1 AND start_date < $2 AND is_current",
        plan.winner_member_role_id,
        plan.cutoff,
    )
    if current_moved:
        logger.warning(
            "%d is_current assignment(s) in the movable cohort — moving them "
            "onto the ended predecessor breaks the #307 invariant; investigate "
            "before relying on this restore",
            current_moved,
        )
    if execute:
        rows = await conn.fetch(
            """UPDATE role_assignments SET role_id=$1
               WHERE role_id=$2 AND start_date < $3
               RETURNING id""",
            plan.predecessor_role_id,
            plan.winner_member_role_id,
            plan.cutoff,
        )
        moved = len(rows)
    else:
        moved = await conn.fetchval(
            "SELECT count(*) FROM role_assignments WHERE role_id=$1 AND start_date < $2",
            plan.winner_member_role_id,
            plan.cutoff,
        )
    logger.info(
        "%s %d assignment(s) starting before %s onto role %s",
        "re-pointed" if execute else "would re-point",
        moved,
        plan.cutoff,
        plan.predecessor_role_id,
    )
    return moved, current_moved


async def _record_succession(conn: asyncpg.Connection, plan: RestorePlan, *, execute: bool) -> bool:
    """Dated succeeded_by (predecessor → winner); derives the lifespan end."""
    exists = await conn.fetchval(
        """SELECT 1 FROM entity_events ev
           JOIN entity_event_types t ON t.id = ev.event_type_id
           WHERE t.slug='succeeded_by' AND ev.entity_type='organization'
             AND ev.entity_id=$1 AND ev.linked_entity_id=$2
             AND ev.archived_at IS NULL""",
        plan.predecessor_org_id,
        plan.winner_org_id,
    )
    if exists:
        logger.info("succession event already recorded — skipping")
        return False
    logger.info(
        "%s succeeded_by (%d) %s → %s",
        "recording" if execute else "would record",
        plan.succession_year,
        plan.predecessor_org_id,
        plan.winner_org_id,
    )
    if execute:
        await conn.execute(
            """INSERT INTO entity_events
                   (id, entity_type, entity_id, event_type_id, event_year,
                    linked_entity_type, linked_entity_id, notes)
               SELECT $1, 'organization', $2, t.id, $4, 'organization', $3,
                      'WSL re-keyed the committee 3532 → 31651 (#467/#469 restore).'
               FROM entity_event_types t WHERE t.slug='succeeded_by'""",
            generate_id(),
            plan.predecessor_org_id,
            plan.winner_org_id,
            plan.succession_year,
        )
    return True


async def _resubscribe_watchers(
    conn: asyncpg.Connection, plan: RestorePlan, *, execute: bool
) -> int:
    """Re-subscribe the winner's current watchers to the resurrected org and role.

    The restore's compensating ``updated`` events cannot reach the one consumer
    that needs them: ``GET /changes`` joins on **current** subscriptions, so a
    key that processed the merge tombstone, re-anchored to the winner and
    unsubscribed from the retired id is permanently deaf to that id. Nothing
    records who held a subscription at delete time, so the winner's watchers are
    the stand-in — the same key set in every case where the consumer re-anchored,
    which is the documented response to a ``merged_into`` tombstone.

    Returns the number of (key, id) subscriptions that are missing, which is what
    a re-run reports as zero.
    """
    added = 0
    for source, target in (
        (plan.winner_org_id, plan.predecessor_org_id),
        (plan.winner_member_role_id, plan.predecessor_role_id),
    ):
        keys = await conn.fetch(
            """SELECT s.api_key_id FROM api_key_entity_subscriptions s
               WHERE s.entity_id = $1
                 AND NOT EXISTS (
                     SELECT 1 FROM api_key_entity_subscriptions t
                     WHERE t.api_key_id = s.api_key_id AND t.entity_id = $2)""",
            source,
            target,
        )
        for k in keys:
            logger.info(
                "%s key %s to restored entity %s (watches winner %s)",
                "subscribing" if execute else "would subscribe",
                k["api_key_id"],
                target,
                source,
            )
        added += len(keys)
        if execute and keys:
            await copy_subscriptions(conn, [(source, target)])
    if not added:
        logger.info("no winner subscriptions to copy — the restored ids have no audience")
    return added


async def run_restore(
    conn: asyncpg.Connection, plan: RestorePlan, *, execute: bool
) -> dict[str, int | bool]:
    """Run all steps; return a summary. Caller owns the transaction."""
    if not await conn.fetchval("SELECT 1 FROM organizations WHERE id=$1", plan.winner_org_id):
        raise RuntimeError(f"winner org {plan.winner_org_id} not found — wrong database?")
    if not await conn.fetchval("SELECT 1 FROM roles WHERE id=$1", plan.winner_member_role_id):
        raise RuntimeError(f"winner Member role {plan.winner_member_role_id} not found")

    summary: dict[str, int | bool] = {}
    summary["org_resurrected"] = await _resurrect_org(conn, plan, execute=execute)
    summary["role_resurrected"] = await _resurrect_role(conn, plan, execute=execute)
    summary["tombstones_cleared"] = await _clear_tombstones(conn, plan, execute=execute)
    summary["identifier_moved"] = await _move_identifier(conn, plan, execute=execute)
    summary["moved_assignments"], summary["current_moved"] = await _repoint_assignments(
        conn, plan, execute=execute
    )
    summary["succession_recorded"] = await _record_succession(conn, plan, execute=execute)
    # Last: the ids exist again by now, so the audience is being pointed at
    # something live rather than at a tombstone the earlier steps still held.
    summary["subscriptions_restored"] = await _resubscribe_watchers(conn, plan, execute=execute)
    logger.info("summary: %s", summary)
    if not execute:
        logger.info("Pass --execute to apply")
    return summary


async def run(dsn: str, *, execute: bool) -> None:
    """Connect and run; execute mode is one transaction — all or nothing."""
    conn = await asyncpg.connect(dsn)
    try:
        if execute:
            async with conn.transaction():
                await run_restore(conn, PROD_PLAN, execute=True)
        else:
            await run_restore(conn, PROD_PLAN, execute=False)
    finally:
        await conn.close()


def main() -> None:
    configure_logging()
    parser = argparse.ArgumentParser(description=__doc__)
    add_dsn_args(parser)
    parser.add_argument("--execute", action="store_true", help="apply changes (default: report)")
    args = parser.parse_args()
    dsn = resolve_dsn(args, parser)
    asyncio.run(run(dsn, execute=args.execute))


if __name__ == "__main__":
    main()
