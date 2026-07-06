"""Backfill org_wa_party identifiers onto the existing WA party Organizations.

Issue #270: adds the ``org_wa_party`` entity_identifier_type so a producer can
attach a WA party Org by a stable key instead of by name-match. The two party
Orgs PM already holds (Democratic, Republican) predate the identifier, so a
producer's first observation would create a duplicate unless we backfill. This
script attaches the identifier to each, matched by canonical name.

Value convention (#270): a bare lowercase party slug — ``democratic`` /
``republican``. No ``wa-`` prefix (the identifier *type* already scopes to WA).
"Independent" is deliberately absent: PM does not model an Independent party Org
(an independent legislator = absence of a party Assignment), so there is nothing
to backfill.

Match safety: party Orgs are matched on their canonical ``organization_names``
row (not the ``v_org_display_names`` view, which composes "name (acronym)" and
would miss an acronym'd Org). Always run the dry run first and confirm each
reported Org is the intended party before ``--execute`` — if PM's canonical names
differ from the map below, update ``_PARTY_ORG_NAMES`` (keys are compared
lowercased). A name with no match, or more than one, is reported and skipped,
never created.

Idempotent — an Org that already carries the identifier is left untouched.

Usage:
    uv run python -m scripts.backfill_org_wa_party_identifiers            # dry run
    uv run python -m scripts.backfill_org_wa_party_identifiers --execute  # commit
"""

import argparse
import asyncio
import os
from collections import Counter
from typing import Literal, TypedDict

import asyncpg

from src.core.db import generate_id
from src.core.logging import configure_logging, get_logger

logger = get_logger(__name__)

# Canonical name (lowercased) -> party value. Keys are matched case-insensitively
# against an Org's canonical name. Match the canonical name row directly, not the
# v_org_display_names view: that view composes "name (acronym)", so an acronym'd
# party Org would silently fail an equality match.
_PARTY_ORG_NAMES: dict[str, str] = {
    "washington state democratic party": "democratic",
    "washington state republican party": "republican",
}

_FIND_ORG_SQL = """
SELECT organization_id
FROM organization_names
WHERE is_canonical = TRUE AND lower(name) = $1
"""

PartyBackfillStatus = Literal["applied", "planned", "exists", "conflict", "missing", "ambiguous"]


class PartyBackfillAction(TypedDict):
    """One backfill outcome per mapped party value (see module docstring)."""

    name: str
    value: str
    org_id: str | None
    status: PartyBackfillStatus


_EXISTING_VALUE_SQL = """
SELECT i.value
FROM identifiers i
JOIN entity_identifier_types t ON t.id = i.entity_identifier_type_id
WHERE t.slug = 'org_wa_party' AND i.entity_id = $1
"""

_INSERT_SQL = """
INSERT INTO identifiers (id, entity_id, entity_identifier_type_id, value)
VALUES ($1, $2, $3, $4)
"""


async def backfill_party_identifiers(
    conn: asyncpg.Connection, *, execute: bool
) -> list[PartyBackfillAction]:
    """Attach org_wa_party identifiers to the mapped party Orgs.

    Returns one ``PartyBackfillAction`` per mapped party value, whose ``status``
    is one of ``applied`` (inserted), ``planned`` (dry run, would insert),
    ``exists`` (already present with this value), ``conflict`` (already present
    with a different value — skipped), ``missing`` (no Org matched — skipped),
    or ``ambiguous`` (multiple Orgs matched — skipped). Only ``applied`` mutates.
    """
    type_id = await conn.fetchval(
        "SELECT id FROM entity_identifier_types WHERE slug = 'org_wa_party'"
    )
    if type_id is None:
        raise RuntimeError("org_wa_party identifier type not found — run apply_schema first")

    actions: list[PartyBackfillAction] = []
    for name, value in _PARTY_ORG_NAMES.items():
        org_ids = [r["organization_id"] for r in await conn.fetch(_FIND_ORG_SQL, name)]

        if not org_ids:
            logger.warning("No Org matches canonical name %r — skipping %r", name, value)
            actions.append({"name": name, "value": value, "org_id": None, "status": "missing"})
            continue
        if len(org_ids) > 1:
            logger.warning(
                "Ambiguous: %d Orgs match canonical name %r — skipping %r",
                len(org_ids),
                name,
                value,
            )
            actions.append({"name": name, "value": value, "org_id": None, "status": "ambiguous"})
            continue

        org_id = org_ids[0]
        existing = await conn.fetchval(_EXISTING_VALUE_SQL, org_id)
        if existing is not None:
            if existing == value:
                status = "exists"
            else:
                logger.warning(
                    "Org %s already has org_wa_party=%r, not overwriting with %r",
                    org_id,
                    existing,
                    value,
                )
                status = "conflict"
            actions.append({"name": name, "value": value, "org_id": org_id, "status": status})
            continue

        if execute:
            await conn.execute(_INSERT_SQL, generate_id(), org_id, type_id, value)
            logger.info("Attached org_wa_party=%r to Org %s (%r)", value, org_id, name)
            status = "applied"
        else:
            logger.info("Would attach org_wa_party=%r to Org %s (%r)", value, org_id, name)
            status = "planned"
        actions.append({"name": name, "value": value, "org_id": org_id, "status": status})

    return actions


async def run(*, execute: bool) -> None:
    """Connect to DATABASE_URL and backfill party identifiers."""
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        raise RuntimeError("DATABASE_URL not set")

    conn = await asyncpg.connect(dsn)
    try:
        if execute:
            async with conn.transaction():
                actions = await backfill_party_identifiers(conn, execute=True)
        else:
            actions = await backfill_party_identifiers(conn, execute=False)

        # Surface every outcome, not just applied — a name mismatch shows up as
        # missing/ambiguous here so a 0-applied run is never silently mistaken
        # for "nothing to do".
        counts = Counter(a["status"] for a in actions)
        breakdown = ", ".join(f"{status}={n}" for status, n in sorted(counts.items()))
        if not execute:
            logger.info(
                "Dry run — %d identifier(s) would be attached (%s); pass --execute",
                counts["planned"],
                breakdown or "no party Orgs mapped",
            )
        else:
            logger.info(
                "Backfilled %d org_wa_party identifier(s) (%s)",
                counts["applied"],
                breakdown or "no party Orgs mapped",
            )
    finally:
        await conn.close()


def main() -> None:
    configure_logging()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Commit changes (default is dry run)",
    )
    args = parser.parse_args()
    asyncio.run(run(execute=args.execute))


if __name__ == "__main__":
    main()
