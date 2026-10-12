"""What a merge owes its readers: one tombstone per hard-deleted row (#467, #607).

A merge does two things a plain edit does not: it **hard-deletes** rows, and it
**re-identifies** the data those rows held. The single record of "this id
retired, and here is where it went" is a ``deleted_entities`` row. The detail
GETs read it to answer a retired id with ``410`` + ``merged_into`` (#607), and
the applier reads it to follow a crosswalk anchor through a merge.

Until #467 only the merged **organization** / **person** got one. The roles and
role_assignments a merge hard-deletes underneath them got nothing, on the
assumption that the parent's tombstone covered the subtree. It does not: a
consumer anchored on an assignment id reads that id, not its parent's.

:func:`record_merge_tombstones` closes that: one tombstone per hard-deleted row,
carrying ``merged_into`` = the **survivor of that row**, not of the parent merge.
That makes the signal a *rebind* ("this assignment is now that assignment")
rather than a bare drop. It is idempotent and safe on an empty pair list, so a
caller can hand over whatever its conflict query returned without a length check.

The subscription copy that sat beside it (``mirror_subscriptions`` /
``copy_subscriptions``) retired with the change feed in #502.
"""

import asyncpg

#: Entity types a merge may **tombstone** — not the set it hard-deletes, which is
#: wider. Deliberately narrower than the `deleted_entities` CHECK constraint on two
#: counts: 'jurisdiction', because no merge folds one jurisdiction into another; and
#: 'role_assignment_relationship', because an edge is announced through its
#: endpoints rather than on its own. An edge is reachable only via the two
#: assignments it joins, both of which get their own tombstone, so it has no
#: independent anchor for a consumer to repair — see the CASCADE note in
#: :mod:`src.core.ancillary_migrate`. Anything not listed here would only widen what
#: a typo can reach.
MERGEABLE_ENTITY_TYPES = frozenset({"person", "organization", "role", "role_assignment"})


def _validate(entity_type: str) -> None:
    """Reject a mistyped entity type here rather than at the CHECK constraint.

    The DB would catch it either way; failing in Python names the offending value
    and the caller, which a 23514 from inside `executemany` does not.
    """
    if entity_type not in MERGEABLE_ENTITY_TYPES:
        raise ValueError(f"not a mergeable entity type: {entity_type!r}")


async def record_merge_tombstones(
    db: asyncpg.Connection, entity_type: str, pairs: list[tuple[str, str]]
) -> None:
    """Tombstone each hard-deleted ``loser``, pointing at the ``winner`` that absorbed it.

    ``pairs`` is ``[(loser_id, winner_id), ...]`` — the same shape the merge paths
    already build for :func:`~src.core.ancillary_migrate.rehome_conflicting_assignment_ancillary`,
    so a caller passes the list it has rather than reshaping it.

    Call this **after** the DELETE: ``deleted_entities`` has no FK to the entity,
    but ordering it after keeps "the row is gone" and "the row was announced"
    adjacent in every caller. ``ON CONFLICT DO NOTHING`` makes a re-merge of an
    already-tombstoned id a no-op instead of a 23505 — the first tombstone's
    ``merged_into`` is the one that describes the hop a reader missed.
    """
    _validate(entity_type)
    if not pairs:
        return
    await db.executemany(
        "INSERT INTO deleted_entities (entity_type, entity_id, merged_into)"
        " VALUES ($1, $2, $3) ON CONFLICT DO NOTHING",
        [(entity_type, loser_id, winner_id) for loser_id, winner_id in pairs],
    )
