"""What a subscribed dataset owns outright: links PM curates nowhere (#592), and
tables PM holds only as the dataset's copy (#671).

The applier owns the org link types below wherever both orgs are in scope, an
*anchored pair*. A curator's disagreement with one of those links goes upstream:
there is no overlay pin for a link, so every PM write door refuses a change to
it rather than let the next run revert it.

The type list is the manifest's `owned_event_types` for the lineage binding.
The admin service cannot read the manifest (the mapping package imports dbt), so
the list lives here as well, and `tests/core/ingestion/mapping/test_manifest.py`
holds the two together.
"""

import asyncpg

from src.core.ingestion.crosswalk import IN_SCOPE, PRODUCER_SOURCE

__all__ = [
    "DATASET_OWNED_LINK_TYPES",
    "DATASET_OWNED_TABLES",
    "anchored_orgs",
    "dataset_owned_link",
    "owned_link",
]

# The org→org link types a dataset owns on anchored pairs.
DATASET_OWNED_LINK_TYPES: frozenset[str] = frozenset({"succeeded_by", "merged_with", "split_from"})

# #671: tables a dataset owns outright under each in-scope parent (the manifest's
# `owns_rows: all`). No PM write door touches them, so nothing here needs a guard
# yet; a door added later refuses rows under an anchored parent, as the links do.
DATASET_OWNED_TABLES: frozenset[str] = frozenset({"role_capacity_periods"})

# The orgs among $1 carrying an in-scope anchor of the producer whose dataset
# owns the links — the applier's own row scope (it reads one source's crosswalk),
# so the lock reaches exactly the links absence can archive (CR 2).
_ANCHORED_SQL = (
    "SELECT DISTINCT pm_id FROM producer_crosswalk"
    " WHERE source = $3 AND kind = 'organization' AND pm_id = ANY($1::text[])"
    " AND resolution = ANY($2::text[])"
)


async def anchored_orgs(conn: asyncpg.Connection, ids: list[str]) -> set[str]:
    """The orgs among ``ids`` the producer's anchors hold in scope — one query for a list."""
    if not ids:
        return set()
    rows = await conn.fetch(_ANCHORED_SQL, ids, list(IN_SCOPE), PRODUCER_SOURCE)
    return {r["pm_id"] for r in rows}


def owned_link(
    entity_type: str,
    entity_id: str,
    slug: str | None,
    linked_type: str | None,
    linked_id: str | None,
    anchored: set[str],
) -> bool:
    """`dataset_owned_link` given the anchored orgs already read (a list renders many)."""
    return (
        entity_type == "organization"
        and slug in DATASET_OWNED_LINK_TYPES
        and linked_type == "organization"
        and bool(linked_id)
        and linked_id != entity_id
        and entity_id in anchored
        and linked_id in anchored
    )


async def dataset_owned_link(
    conn: asyncpg.Connection,
    entity_type: str,
    entity_id: str,
    slug: str,
    linked_type: str | None,
    linked_id: str | None,
) -> bool:
    """Whether this event is a link a dataset owns — and so no PM door may write it.

    True for an org→org link of a `DATASET_OWNED_LINK_TYPES` type whose two orgs
    are both in a producer's scope. A door checks the row as it is and as the
    write would leave it, so an edit can neither move a link into the lock nor
    free one from it.
    """
    if linked_id is None or not owned_link(
        entity_type, entity_id, slug, linked_type, linked_id, {entity_id, linked_id}
    ):
        return False  # decided without the anchors: not an owned org→org link
    anchored = await anchored_orgs(conn, [entity_id, linked_id])
    return owned_link(entity_type, entity_id, slug, linked_type, linked_id, anchored)
