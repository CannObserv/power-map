"""Walking an id through ``deleted_entities`` merge history (#495, #607).

``deleted_entities.merged_into`` names *that row's* survivor (see
:mod:`src.core.merge_signals`), so where a retired id went is a chain, not a
lookup: A was merged into B, and B later into C. The public detail GETs
(:mod:`src.api.public.gone`) walk it to answer a retired id with ``410`` + where
it went, keyed by the tombstone's own ``entity_type``. The producer crosswalk's
cutover seed walked it too, to resolve anchors; that seed retired in #502.

Merge tombstones never expire (``src/core/maintenance.py``), so a merge stays
walkable for good; a bare-delete tombstone ages out on the TTL.
"""

from dataclasses import dataclass

import asyncpg

#: Tombstone ``entity_type`` → the table whose live row ends a walk. Every type a
#: detail GET can be asked about; ``role_assignment_relationship`` is absent
#: because an edge has no detail route and is announced through its endpoints.
ENTITY_TABLE = {
    "person": "people",
    "organization": "organizations",
    "role": "roles",
    "role_assignment": "role_assignments",
    "jurisdiction": "jurisdictions",
}


@dataclass(frozen=True)
class Resolution:
    """Where an id leads, and whether that is somewhere a reader can use.

    ``status`` is one of ``live``, ``archived``, ``merged``, ``deleted_no_successor``,
    ``missing`` or ``cycle``. ``pm_id`` is the row to use, and is ``None``
    exactly when the id is unresolvable.
    """

    status: str
    pm_id: str | None


async def fetch_tombstone(
    db: asyncpg.Connection, entity_type: str, entity_id: str
) -> asyncpg.Record | None:
    """Return the ``deleted_entities`` row (``deleted_at``, ``merged_into``) for an id."""
    return await db.fetchrow(
        "SELECT deleted_at, merged_into FROM deleted_entities"
        " WHERE entity_type = $1 AND entity_id = $2",
        entity_type,
        entity_id,
    )


async def fetch_tombstones(
    db: asyncpg.Connection, entity_type: str, entity_ids: list[str]
) -> dict[str, asyncpg.Record]:
    """:func:`fetch_tombstone` for many ids in one query, keyed by ``entity_id``."""
    rows = await db.fetch(
        "SELECT entity_id, deleted_at, merged_into FROM deleted_entities"
        " WHERE entity_type = $1 AND entity_id = ANY($2::text[])",
        entity_type,
        entity_ids,
    )
    return {r["entity_id"]: r for r in rows}


async def walk_merge_history(
    db: asyncpg.Connection, entity_type: str, entity_id: str
) -> Resolution:
    """Walk ``entity_id`` through merge history to the row it now lives as."""
    if entity_type not in ENTITY_TABLE:
        raise ValueError(f"unknown entity type: {entity_type!r}")
    table = ENTITY_TABLE[entity_type]

    # `seen` both detects a cycle and bounds the walk: ids are finite and each
    # hop consumes one, so no separate hop limit is needed — and a hop limit
    # could only ever fire by reporting a merely long chain as a cycle.
    seen: set[str] = set()
    current = entity_id
    while True:
        if current in seen:
            return Resolution("cycle", None)
        seen.add(current)

        row = await db.fetchrow(f"SELECT archived_at FROM {table} WHERE id = $1", current)  # noqa: S608
        if row is not None:
            status = "archived" if row["archived_at"] is not None else "live"
            if current != entity_id and status == "live":
                status = "merged"
            return Resolution(status, current)

        tombstone = await fetch_tombstone(db, entity_type, current)
        if tombstone is None:
            return Resolution("missing", None)
        if tombstone["merged_into"] is None:
            return Resolution("deleted_no_successor", None)
        current = tombstone["merged_into"]
