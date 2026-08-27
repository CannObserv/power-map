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
- :func:`mirror_subscriptions` — everyone watching the loser also watches the
  winner from here on, so a subscriber does not stop seeing the data merely
  because it moved. It **adds** rather than moves: the feed joins subscriptions at
  *read* time (``changes.py``: ``s.entity_id = ec.entity_id``), so deleting the
  loser's row would hide the tombstone written moments earlier from the one key
  that needed it. Keeping a subscription on a retired id is a supported state —
  ``_BATCH_RESOLVE_ENTITY_TYPE`` resolves ids through ``deleted_entities``
  precisely so it is — and the consumer drops it once the rebind is processed.

Both are idempotent and safe on an empty pair list, so a caller can hand over
whatever its conflict query returned without a length check.
"""

import asyncpg

#: Entity types a merge may **tombstone** — not the set it hard-deletes, which is
#: wider. Deliberately narrower than the `deleted_entities` CHECK constraint on two
#: counts: 'jurisdiction', because no merge folds one jurisdiction into another; and
#: 'role_assignment_relationship', because an edge is announced through its
#: endpoints rather than on its own. An edge is reachable only via the two
#: assignments it joins, both of which get their own tombstone, so it has no
#: independent anchor for a subscriber to repair — see the CASCADE note in
#: :mod:`src.core.ancillary_migrate`. Anything not listed here would only widen what
#: a typo can reach. `mirror_subscriptions` needs no such list — it copies the type
#: from the row it mirrors.
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
    ``merged_into`` is the one that describes the hop the subscriber missed.
    """
    _validate(entity_type)
    if not pairs:
        return
    await db.executemany(
        "INSERT INTO deleted_entities (entity_type, entity_id, merged_into)"
        " VALUES ($1, $2, $3) ON CONFLICT DO NOTHING",
        [(entity_type, loser_id, winner_id) for loser_id, winner_id in pairs],
    )


async def mirror_subscriptions(db: asyncpg.Connection, pairs: list[tuple[str, str]]) -> None:
    """Subscribe every watcher of ``loser`` to ``winner`` too, keeping the loser row.

    ``pairs`` is ``[(loser_id, winner_id), ...]``.

    **Add, never move.** The change feed resolves subscriptions when the consumer
    polls, not when the merge runs (``changes.py``: ``JOIN ... ON s.entity_id =
    ec.entity_id``). Re-pointing the loser's subscription onto the winner would
    therefore erase the audience for the loser's own tombstone — the subscriber
    holding the retired anchor is the only party that needs it, and it would be the
    one party guaranteed not to receive it. The loser row stays until the consumer
    retires it; the ``deleted_entities`` TTL prunes the tombstone on the usual
    90-day clock either way.

    ``entity_type`` is copied from the loser's own subscription row rather than
    passed in: the two ends of a merge are always the same type, and reading it
    from the row makes a caller's mismatched argument impossible instead of
    silently retyping a subscription.
    """
    if not pairs:
        return
    await db.executemany(
        """INSERT INTO api_key_entity_subscriptions (api_key_id, entity_id, entity_type)
           SELECT l.api_key_id, $2, l.entity_type
           FROM api_key_entity_subscriptions l
           WHERE l.entity_id = $1
           ON CONFLICT DO NOTHING""",
        pairs,
    )
