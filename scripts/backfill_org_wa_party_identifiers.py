"""Backfill org_wa_party identifiers onto the existing WA party Organizations.

Issue #270: adds the ``org_wa_party`` entity_identifier_type so a producer can
attach a WA party Org by a stable key instead of by name-match. The two party
Orgs PM already holds (Democratic, Republican) predate the identifier, so a
producer's first observation would create a duplicate unless we backfill. This
script attaches the identifier to each, matched by canonical display name.

Value convention (#270): a bare lowercase party slug — ``democratic`` /
``republican``. No ``wa-`` prefix (the identifier *type* already scopes to WA).
"Independent" is deliberately absent: PM does not model an Independent party Org
(an independent legislator = absence of a party Assignment), so there is nothing
to backfill.

Match safety: party Orgs are matched by canonical name via ``v_org_display_names``.
Always run the dry run first and confirm each reported Org is the intended party
before ``--execute`` — if PM's canonical names differ from the map below, update
``_PARTY_ORG_NAMES`` (keys are compared lowercased). A name with no match, or
more than one, is reported and skipped, never created.

Idempotent — an Org that already carries the identifier is left untouched.

Usage:
    uv run python -m scripts.backfill_org_wa_party_identifiers            # dry run
    uv run python -m scripts.backfill_org_wa_party_identifiers --execute  # commit
"""

import argparse
import asyncio
import os

import asyncpg

from src.core.db import generate_id
from src.core.logging import configure_logging, get_logger

logger = get_logger(__name__)

# Canonical display name (lowercased) -> party value. Keys are matched
# case-insensitively against v_org_display_names.display_name.
_PARTY_ORG_NAMES: dict[str, str] = {
    "washington state democratic party": "democratic",
    "washington state republican party": "republican",
}

_FIND_ORG_SQL = """
SELECT organization_id
FROM v_org_display_names
WHERE lower(display_name) = $1
"""

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


async def backfill_party_identifiers(conn: asyncpg.Connection, *, execute: bool) -> list[dict]:
    """Attach org_wa_party identifiers to the mapped party Orgs.

    Returns one action record per mapped party value:
    ``{"name", "value", "org_id", "status"}`` where status is one of
    ``applied`` (inserted), ``planned`` (dry run, would insert),
    ``exists`` (already present with this value), ``conflict`` (already present
    with a different value — skipped), ``missing`` (no Org matched — skipped),
    or ``ambiguous`` (multiple Orgs matched — skipped). Only ``applied`` mutates.
    """
    type_id = await conn.fetchval(
        "SELECT id FROM entity_identifier_types WHERE slug = 'org_wa_party'"
    )
    if type_id is None:
        raise RuntimeError("org_wa_party identifier type not found — run apply_schema first")

    actions: list[dict] = []
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

        applied = sum(1 for a in actions if a["status"] == "applied")
        planned = sum(1 for a in actions if a["status"] == "planned")
        if not execute:
            logger.info("Dry run — %d identifier(s) would be attached; pass --execute", planned)
        else:
            logger.info("Backfilled %d org_wa_party identifier(s)", applied)
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
