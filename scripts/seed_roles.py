"""Seed legislative roles from a role seed JSON file (#263).

Reads a file of the shape produced by ``scripts.generate_wa_roles`` (a ``roles``
array; see ``data/cannabis_observer/*-usa_wa-legislative-roles.json``). Each role
references its chamber by the ``org_wa_legislature_chamber`` identifier value,
its district by jurisdiction slug, and its office by role_type slug. Creation
goes through ``resolve_role`` (#261), so the run is idempotent — re-running
attaches to existing roles rather than duplicating them.

This is a **seeder, not an updater**: ``resolve_role`` matches on role identity
(org + role_type + jurisdiction + qualifier) and ignores ``title``, so re-running
after a title-convention change does NOT revise existing roles' titles or other
attributes — it only create-or-attaches.

Usage:
    uv run python -m scripts.seed_roles <path-to-roles.json>            # dry run
    uv run python -m scripts.seed_roles <path-to-roles.json> --execute  # commit
"""

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any

import asyncpg

from scripts._dsn import add_dsn_args, resolve_dsn
from src.core.logging import configure_logging, get_logger
from src.core.observation import Disposition, resolve_role

logger = get_logger(__name__)


def load_seed_file(path: Path) -> dict[str, Any]:
    """Load and return the role seed JSON (a ``roles`` array)."""
    with path.open() as f:
        return json.load(f)


async def _resolve_refs(
    conn: asyncpg.Connection, role: dict[str, Any]
) -> tuple[str | None, str | None]:
    """Resolve a role's chamber → org_id and jurisdiction slug → id (either may be None)."""
    org_id = await conn.fetchval(
        "SELECT i.entity_id FROM identifiers i"
        " JOIN entity_identifier_types t ON t.id = i.entity_identifier_type_id"
        " WHERE t.slug = 'org_wa_legislature_chamber' AND i.value = $1",
        role["chamber"],
    )
    if org_id is None:
        return None, None
    jurisdiction_id = await conn.fetchval(
        "SELECT id FROM jurisdictions WHERE slug = $1", role["jurisdiction_slug"]
    )
    return org_id, jurisdiction_id


async def seed_roles(conn: asyncpg.Connection, roles: list[dict[str, Any]]) -> dict[str, int]:
    """Create-or-attach each role via resolve_role. Returns {new, attached, rejected}."""
    counts = {"new": 0, "attached": 0, "rejected": 0}
    for role in roles:
        org_id, jurisdiction_id = await _resolve_refs(conn, role)
        if org_id is None:
            logger.warning("role rejected: unknown chamber=%r (%s)", role["chamber"], role["title"])
            counts["rejected"] += 1
            continue
        if jurisdiction_id is None:
            logger.warning(
                "role rejected: unknown jurisdiction_slug=%r (%s)",
                role["jurisdiction_slug"],
                role["title"],
            )
            counts["rejected"] += 1
            continue

        _role_id, disposition, reason = await resolve_role(
            conn,
            org_id,
            role["title"],
            role_type=role["role_type"],
            jurisdiction_id=jurisdiction_id,
            qualifier=role["qualifier"],
        )
        if disposition is Disposition.NEW:
            counts["new"] += 1
        elif disposition is Disposition.AUTO_ATTACHED:
            counts["attached"] += 1
        else:
            logger.warning("role rejected: %s (%s)", role["title"], reason)
            counts["rejected"] += 1
    return counts


async def preview_roles(conn: asyncpg.Connection, roles: list[dict[str, Any]]) -> dict[str, int]:
    """Read-only classification for dry runs. Returns {would_create, exists, unresolved}.

    ``unresolved`` counts roles whose chamber or jurisdiction does not resolve
    (these would be rejected on execute). No rows are written.
    """
    counts = {"would_create": 0, "exists": 0, "unresolved": 0}
    for role in roles:
        org_id, jurisdiction_id = await _resolve_refs(conn, role)
        role_type_id = await conn.fetchval(
            "SELECT id FROM role_types WHERE slug = $1", role["role_type"]
        )
        # unresolved mirrors what --execute would reject (missing chamber /
        # jurisdiction / office).
        if org_id is None or jurisdiction_id is None or role_type_id is None:
            counts["unresolved"] += 1
            continue
        existing = await conn.fetchval(
            "SELECT 1 FROM roles"
            " WHERE organization_id = $1"
            "   AND role_type_id IS NOT DISTINCT FROM $2"
            "   AND jurisdiction_id = $3"
            "   AND qualifier IS NOT DISTINCT FROM $4"
            "   AND archived_at IS NULL",
            org_id,
            role_type_id,
            jurisdiction_id,
            role["qualifier"],
        )
        counts["exists" if existing else "would_create"] += 1
    return counts


async def run(dsn: str, seed_path: Path, *, execute: bool) -> None:
    """Load the seed file and seed roles. Dry run (read-only preview) unless ``execute``."""
    roles = load_seed_file(seed_path).get("roles", [])
    conn = await asyncpg.connect(dsn)
    try:
        if not execute:
            preview = await preview_roles(conn, roles)
            logger.info(
                "Dry run (%d roles): %d would create, %d already exist, %d unresolved."
                " Pass --execute to commit.",
                len(roles),
                preview["would_create"],
                preview["exists"],
                preview["unresolved"],
            )
            return

        async with conn.transaction():
            counts = await seed_roles(conn, roles)
        logger.info(
            "Seeded roles: %d new, %d attached, %d rejected",
            counts["new"],
            counts["attached"],
            counts["rejected"],
        )
    finally:
        await conn.close()


def main() -> None:
    configure_logging()
    parser = argparse.ArgumentParser(description=__doc__)
    add_dsn_args(parser)
    parser.add_argument("seed_file", type=Path, help="Path to the role seed JSON file")
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Commit changes (default is dry run)",
    )
    args = parser.parse_args()
    if not args.seed_file.exists():
        raise SystemExit(f"seed file not found: {args.seed_file}")
    # Resolved after validation so the echo means "about to connect". See
    # docs/RUNBOOKS.md §"Operational scripts — dry run by default & target echo".
    dsn = resolve_dsn(args, parser)
    asyncio.run(run(dsn, args.seed_file, execute=args.execute))


if __name__ == "__main__":
    main()
