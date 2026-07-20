"""Split the coarse `member` role_type into committee_member + party_member (#266).

The pre-#266 `member` classifier (#269) was overloaded — it tagged both committee
membership and party membership, so "all members" mixed the two. #266's
domain-prefix convention gives each its own slug. This script reassigns every
`member` role by the **structural identifier** on its org (no display-name
heuristics):

- org carries an ``org_wa_legislature_committee_id`` identifier → ``committee_member``
- org carries an ``org_wa_party`` identifier → ``party_member``
- neither (or, defensively, both) → **skipped**, left untouched, reported for triage

Only ``role_type_id`` changes; the ``roles`` UPDATE auto-emits a change-feed event
(``trg_entity_changes_roles``). Idempotent — a reassigned role no longer matches
``member`` and is a no-op on re-run. Reassigns archived rows too, so no row
references ``member`` afterward (its catalog row can then be dropped, #266 step 6).

Usage:
    uv run python -m scripts.migrate_member_role_type            # dry run
    uv run python -m scripts.migrate_member_role_type --execute  # commit
"""

import argparse
import asyncio
import os
from collections import Counter
from typing import Literal, TypedDict

import asyncpg

from src.core.logging import configure_logging, get_logger

logger = get_logger(__name__)

Target = Literal["committee_member", "party_member", "skipped"]


class RoleAction(TypedDict):
    """Reassignment outcome for one `member` role."""

    role_id: str
    organization_id: str
    title: str
    target: Target


class Report(TypedDict):
    """Full run outcome: one action per `member` role."""

    actions: list[RoleAction]


_MEMBER_ROLES_SQL = """
SELECT
    r.id AS role_id,
    r.organization_id,
    r.title,
    EXISTS (
        SELECT 1 FROM identifiers i
        JOIN entity_identifier_types t ON t.id = i.entity_identifier_type_id
        WHERE i.entity_id = r.organization_id AND t.slug = 'org_wa_legislature_committee_id'
    ) AS is_committee,
    EXISTS (
        SELECT 1 FROM identifiers i
        JOIN entity_identifier_types t ON t.id = i.entity_identifier_type_id
        WHERE i.entity_id = r.organization_id AND t.slug = 'org_wa_party'
    ) AS is_party
FROM roles r
JOIN role_types rt ON rt.id = r.role_type_id
WHERE rt.slug = 'member'
ORDER BY r.title, r.id
"""

_REASSIGN_SQL = "UPDATE roles SET role_type_id = $2 WHERE id = $1"


def _classify(*, is_committee: bool, is_party: bool) -> Target:
    """Pick the target slug from the org's structural identifiers (skip if ambiguous)."""
    if is_committee and not is_party:
        return "committee_member"
    if is_party and not is_committee:
        return "party_member"
    return "skipped"


async def migrate_member_role_type(conn: asyncpg.Connection, *, execute: bool) -> Report:
    """Reassign every `member` role to committee_member / party_member by org kind.

    Returns one ``RoleAction`` per `member` role. Only non-``skipped`` rows mutate,
    and only when ``execute`` is True.
    """
    type_ids = {
        row["slug"]: row["id"]
        for row in await conn.fetch(
            "SELECT slug, id FROM role_types"
            " WHERE slug IN ('member', 'committee_member', 'party_member')"
        )
    }
    if "member" not in type_ids:
        # The migration has already completed everywhere: `member` is dropped from
        # the catalog once it holds no rows (#266). Nothing left to split, so this
        # is a clean no-op rather than an error — keeps the script re-runnable on
        # an already-migrated DB.
        logger.info("role_type `member` is absent — already migrated, nothing to do")
        return Report(actions=[])
    missing = {"committee_member", "party_member"} - type_ids.keys()
    if missing:
        raise RuntimeError(f"missing role_types {missing} — run apply_schema first")

    actions: list[RoleAction] = []
    for row in await conn.fetch(_MEMBER_ROLES_SQL):
        target = _classify(is_committee=row["is_committee"], is_party=row["is_party"])
        actions.append(
            {
                "role_id": row["role_id"],
                "organization_id": row["organization_id"],
                "title": row["title"],
                "target": target,
            }
        )
        if target == "skipped":
            logger.warning(
                "skipped member role %s (%r) — org %s has no committee/party identifier",
                row["role_id"],
                row["title"],
                row["organization_id"],
            )
            continue
        if execute:
            await conn.execute(_REASSIGN_SQL, row["role_id"], type_ids[target])
            logger.info("Reassigned member role %s (%r) → %s", row["role_id"], row["title"], target)

    return Report(actions=actions)


async def run(*, execute: bool) -> None:
    """Connect to DATABASE_URL and split the `member` role_type."""
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        raise RuntimeError("DATABASE_URL not set")

    conn = await asyncpg.connect(dsn)
    try:
        if execute:
            async with conn.transaction():
                report = await migrate_member_role_type(conn, execute=True)
        else:
            report = await migrate_member_role_type(conn, execute=False)

        counts = Counter(a["target"] for a in report["actions"])
        breakdown = ", ".join(f"{t}={n}" for t, n in sorted(counts.items())) or "nothing to do"
        verb = "Reassigned" if execute else "Dry run — would reassign"
        logger.info("%s %d member role(s): %s", verb, len(report["actions"]), breakdown)
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
