"""What a merge owes its consumers: tombstones and subscription re-homing (#467).

A merge does two things a plain edit does not: it **hard-deletes** rows, and it
**re-identifies** the data those rows held. Both are invisible to a subscriber
unless the merge says so, because the outbox triggers
(``fn_record_entity_change``) fire on INSERT and UPDATE only — a DELETE emits
nothing. The single channel for "this id retired" is a ``deleted_entities`` row,
whose own INSERT trigger writes the ``'deleted'`` outbox entry.

Until #467 only the merged **organization** / **person** got one. The roles and
role_assignments a merge hard-deletes underneath them got nothing, on the
assumption (stated in :mod:`src.core.ancillary_migrate`) that the parent's
tombstone covered the subtree. It does not: a producer anchored on
``pm_assignment_id`` polls ``/api/v1/changes`` filtered by its **own**
subscriptions, so a parent-org tombstone it may not even hold tells it nothing
about the 136 assignment ids that just stopped resolving.

Two helpers close that:

- :func:`record_merge_tombstones` — one tombstone per hard-deleted row, carrying
  ``merged_into`` = the **survivor of that row**, not of the parent merge. That
  makes the signal a *rebind* ("this assignment is now that assignment") rather
  than a bare drop, which is what a producer needs to repair its anchor map
  without re-deriving it.
- :func:`rehome_subscriptions` — an allowlist entry follows its entity. Without
  it a subscriber's list decays into ids that resolve to nothing, and it stops
  seeing the survivor precisely because it was never subscribed to the survivor.

Both are idempotent and safe on an empty pair list, so a caller can hand over
whatever its conflict query returned without a length check.
"""

import asyncpg

#: Entity types both `deleted_entities` and `api_key_entity_subscriptions` accept.
#: Narrower than either CHECK constraint on purpose — these are the only types a
#: merge hard-deletes or re-identifies.
MERGEABLE_ENTITY_TYPES = frozenset(
    {"person", "organization", "role", "role_assignment", "role_assignment_relationship"}
)


def _validate(entity_type: str) -> None:
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
    ``merged_into`` is the one that describes the hop the subscriber missed.
    """
    _validate(entity_type)
    if not pairs:
        return
    await db.executemany(
        "INSERT INTO deleted_entities (entity_type, entity_id, merged_into)"
        f" VALUES ('{entity_type}', $1, $2) ON CONFLICT DO NOTHING",
        pairs,
    )


async def rehome_subscriptions(
    db: asyncpg.Connection, entity_type: str, pairs: list[tuple[str, str]]
) -> None:
    """Move each key's subscription from ``loser`` onto ``winner``.

    ``api_key_entity_subscriptions`` is keyed ``(api_key_id, entity_id)`` — note
    the PK excludes ``entity_type``, so a key already watching the winner must
    have its loser row **deleted** before the UPDATE rather than merged into it;
    otherwise the re-point collides. Dropping the loser row loses nothing: the
    key is left subscribed to the winner, which is where the data now lives.
    """
    _validate(entity_type)
    for loser_id, winner_id in pairs:
        await db.execute(
            """DELETE FROM api_key_entity_subscriptions l
               WHERE l.entity_id = $1
                 AND EXISTS (SELECT 1 FROM api_key_entity_subscriptions w
                             WHERE w.api_key_id = l.api_key_id AND w.entity_id = $2)""",
            loser_id,
            winner_id,
        )
        await db.execute(
            "UPDATE api_key_entity_subscriptions"
            f" SET entity_id = $2, entity_type = '{entity_type}' WHERE entity_id = $1",
            loser_id,
            winner_id,
        )
