"""Retype the legacy URL-form org_wa_pdc identifiers to the bare numeric key.

The org-side analog of #293/#295. Every ``org_wa_pdc`` value in PM is a legacy
PDC Lobbyist Reporting (accesshub) node URL — ``https://accesshub.pdc.wa.gov/
node/N`` — rather than the bare node ID ``N`` (which equals the firm's PDC
``filer_id``). This mirrors the person-side cleanup: the identifier type scopes
the vocabulary, so the value should be the bare key, not a URL.

For each ``org_wa_pdc`` value:

- ``.../node/N`` (accesshub or the ``legacy-lobbyist`` redirect host) -> retype:
  insert the bare ``N`` (unless already present), preserve the URL as a
  ``wa_pdc`` org link (provenance — the page where the org was found), and
  delete the URL row.
- already bare-numeric -> ``exists`` (left untouched).
- a campaign-explorer **committee** URL (``?filer_id=...``) -> ``skipped_committee``:
  that is a committee key in a different vocabulary (like #293's candidate URLs);
  it needs its own identifier type, decided separately — never guess-retyped.
- anything else (free-text like ``I-502 Retailer``) -> ``skipped_freetext``:
  not a PDC key at all; reported for manual cleanup, never touched.

Safety (skip-on-anything-unexpected, per #293/#295): the transform is purely the
URL path segment, so no external lookup is needed. If a target ``N`` already
sits on a **different** org, the row is a ``collision`` — reported and left in
place (merge the orgs first). Idempotent: a second run reports ``exists``.

Usage:
    uv run python -m scripts.retype_org_wa_pdc_identifiers            # dry run
    uv run python -m scripts.retype_org_wa_pdc_identifiers --execute  # commit
"""

import argparse
import asyncio
import os
import re
from collections import Counter
from typing import Literal, TypedDict
from urllib.parse import parse_qs, urlsplit

import asyncpg

from src.core.db import generate_id
from src.core.logging import configure_logging, get_logger

logger = get_logger(__name__)

# The node ID is the final path segment of an accesshub / legacy-lobbyist URL.
_NODE_RE = re.compile(r"/node/(\d+)/?$")


def extract_node_id(value: str) -> str | None:
    """Return the bare node ID from a ``.../node/N`` URL, else None.

    Accepts the accesshub host and PDC's ``legacy-lobbyist`` redirect host (both
    carry the same ``/node/N`` path). A bare numeric value, a committee URL, or
    free text yields None.
    """
    parts = urlsplit(value.strip())
    if parts.scheme not in ("http", "https"):
        return None
    m = _NODE_RE.search(parts.path)
    return m.group(1) if m else None


def _is_committee_url(value: str) -> bool:
    """True for a campaign-explorer URL carrying a ``filer_id`` query param."""
    parts = urlsplit(value.strip())
    return parts.scheme in ("http", "https") and "filer_id" in parse_qs(parts.query)


OrgKeyStatus = Literal[
    "applied",
    "planned",
    "exists",
    "collision",
    "skipped_committee",
    "skipped_freetext",
]


class OrgKeyAction(TypedDict):
    """One outcome per org_wa_pdc identifier row."""

    org_id: str
    value: str
    node_id: str | None
    status: OrgKeyStatus


_TYPE_ID_SQL = "SELECT id FROM entity_identifier_types WHERE slug = 'org_wa_pdc'"
_LINK_TYPE_ID_SQL = "SELECT id FROM link_types WHERE slug = 'wa_pdc'"

_ALL_ROWS_SQL = """
SELECT i.id, i.entity_id, i.value
FROM identifiers i
WHERE i.entity_identifier_type_id = $1
ORDER BY i.entity_id, i.created_at
"""

_NUMERIC_VALUES_SQL = """
SELECT i.value
FROM identifiers i
WHERE i.entity_identifier_type_id = $1 AND i.entity_id = $2
"""

_VALUE_ON_OTHER_ORG_SQL = """
SELECT i.entity_id
FROM identifiers i
WHERE i.entity_identifier_type_id = $1 AND i.value = $2 AND i.entity_id <> $3
"""

_INSERT_IDENTIFIER_SQL = """
INSERT INTO identifiers (id, entity_id, entity_identifier_type_id, value)
VALUES ($1, $2, $3, $4)
"""

_INSERT_LINK_SQL = """
INSERT INTO links (id, entity_type, entity_id, url, link_type_id)
VALUES ($1, 'organization', $2, $3, $4)
ON CONFLICT (entity_type, entity_id, url, link_type_id) DO NOTHING
"""

_DELETE_IDENTIFIER_SQL = "DELETE FROM identifiers WHERE id = $1"


async def retype_org_wa_pdc(conn: asyncpg.Connection, *, execute: bool) -> list[OrgKeyAction]:
    """Retype every URL-form ``org_wa_pdc`` value to its bare numeric node ID.

    Returns one ``OrgKeyAction`` per ``org_wa_pdc`` row. ``status`` is
    ``applied`` (retyped) / ``planned`` (dry run) / ``exists`` (already numeric)
    / ``collision`` (target node ID already on another org — skipped) /
    ``skipped_committee`` (campaign-explorer committee URL — different
    vocabulary) / ``skipped_freetext`` (not a PDC key). Only ``applied`` mutates:
    the bare node ID is inserted (unless present), the URL is preserved as a
    ``wa_pdc`` org link, and the URL row is deleted.
    """
    type_id = await conn.fetchval(_TYPE_ID_SQL)
    link_type_id = await conn.fetchval(_LINK_TYPE_ID_SQL)
    if type_id is None or link_type_id is None:
        raise RuntimeError(
            "org_wa_pdc identifier type or wa_pdc link type not found — run apply_schema first"
        )

    actions: list[OrgKeyAction] = []
    for row in await conn.fetch(_ALL_ROWS_SQL, type_id):
        org_id, value = row["entity_id"], row["value"]
        node_id = extract_node_id(value)
        action: OrgKeyAction = {
            "org_id": org_id,
            "value": value,
            "node_id": node_id,
            "status": "skipped_freetext",
        }
        actions.append(action)

        if value.isdigit():
            action["status"] = "exists"
            continue
        if node_id is None:
            if _is_committee_url(value):
                logger.warning(
                    "org %s: committee URL %r — needs its own vocabulary, skipping",
                    org_id,
                    value,
                )
                action["status"] = "skipped_committee"
            else:
                logger.warning("org %s: non-key value %r — skipping", org_id, value)
                action["status"] = "skipped_freetext"
            continue

        holders = await conn.fetch(_VALUE_ON_OTHER_ORG_SQL, type_id, node_id, org_id)
        if holders:
            logger.warning(
                "org %s: node_id=%s already on org %s — collision, skipping (merge first)",
                org_id,
                node_id,
                holders[0]["entity_id"],
            )
            action["status"] = "collision"
            continue

        if not execute:
            logger.info("Would retype org %s: %r -> %s (+ wa_pdc link)", org_id, value, node_id)
            action["status"] = "planned"
            continue

        existing = {r["value"] for r in await conn.fetch(_NUMERIC_VALUES_SQL, type_id, org_id)}
        if node_id not in existing:
            await conn.execute(_INSERT_IDENTIFIER_SQL, generate_id(), org_id, type_id, node_id)
        await conn.execute(_INSERT_LINK_SQL, generate_id(), org_id, value, link_type_id)
        await conn.execute(_DELETE_IDENTIFIER_SQL, row["id"])
        logger.info("Retyped org %s: %r -> %s (preserved as wa_pdc link)", org_id, value, node_id)
        action["status"] = "applied"

    return actions


async def run(*, execute: bool) -> None:
    """Connect to DATABASE_URL and retype the legacy org identifiers."""
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        raise RuntimeError("DATABASE_URL not set")

    conn = await asyncpg.connect(dsn)
    try:
        if execute:
            async with conn.transaction():
                actions = await retype_org_wa_pdc(conn, execute=True)
        else:
            actions = await retype_org_wa_pdc(conn, execute=False)

        counts = Counter(a["status"] for a in actions)
        breakdown = ", ".join(f"{status}={n}" for status, n in sorted(counts.items()))
        if not execute:
            logger.info(
                "Dry run — %d org_wa_pdc value(s) would be retyped (%s); pass --execute",
                counts["planned"],
                breakdown,
            )
        else:
            logger.info("Retyped %d org_wa_pdc value(s) (%s)", counts["applied"], breakdown)
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
