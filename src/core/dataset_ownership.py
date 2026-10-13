"""What a subscribed dataset owns outright: links PM curates nowhere (#592).

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

from src.core.ingestion.crosswalk import IN_SCOPE

__all__ = ["DATASET_OWNED_LINK_TYPES", "dataset_owned_link"]

# The org→org link types a dataset owns on anchored pairs.
DATASET_OWNED_LINK_TYPES: frozenset[str] = frozenset({"succeeded_by", "merged_with", "split_from"})

# Both orgs carry a producer anchor in scope — the applier's own row scope, so
# the lock reaches exactly the links absence can archive.
_PAIR_SQL = (
    "SELECT count(DISTINCT pm_id) = 2 FROM producer_crosswalk"
    " WHERE kind = 'organization' AND pm_id = ANY($1::text[])"
    " AND resolution = ANY($2::text[])"
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
    if (
        entity_type != "organization"
        or slug not in DATASET_OWNED_LINK_TYPES
        or linked_type != "organization"
        or not linked_id
        or linked_id == entity_id
    ):
        return False
    return bool(await conn.fetchval(_PAIR_SQL, [entity_id, linked_id], list(IN_SCOPE)))
