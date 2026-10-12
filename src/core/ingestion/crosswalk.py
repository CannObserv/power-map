"""The producer crosswalk: PM's row scope for an applied dataset (#495).

A producer that publishes snapshots rather than observations needs PM to know
which rows are its own. `source_key_id` cannot answer that (#490 addendum gap
A): `people` and `roles` carry no such column, and half of usa-wa's
assignments predate stamping. So the scope is an explicit table, and it doubles
as the per-row handle roles and assignments lose under the identifier collapse
(gap B).

The table was seeded once, at the #490 cutover, from the producer's anchor
export, resolving each PM id through merge history; that seed and its parser
retired in #502. Its writer since is the applier (#499): a create mints a row,
an archive stamps one (#527). What lives here is the shared vocabulary and the
one write a merge owes the table:

* **Re-pointing** after a merge (#514). The table stores where each anchor
  resolves, so a merge that retires a row must write the new answer or every
  anchor naming the row keeps pointing at nothing.
"""

import asyncpg

__all__ = [
    "ANCHOR_KINDS",
    "IN_SCOPE",
    "PRODUCER_SOURCE",
    "TOMBSTONE_TYPE",
    "repoint_anchors",
]

ANCHOR_KINDS = ("person", "organization", "role", "assignment")
# The `source` every usa-wa crosswalk row carries — the applier (#499) scopes by
# it, and a create the applier mints copies it. One definition, because the
# first production dry run matched zero rows on a `usa-wa` / `usa_wa` mismatch
# between the cutover seed and the applier.
PRODUCER_SOURCE = "usa_wa"
# Resolutions that put a crosswalk row in the producer's row scope (docs/SCHEMA.md
# § Producer crosswalk): what the applier diffs and what a curator can pin
# (#498). `archived` is in the table and out of scope.
IN_SCOPE = ("live", "merged")

# usa-wa's kind vocabulary is not PM's tombstone vocabulary: an "assignment" is
# a `role_assignment` on this side.
TOMBSTONE_TYPE = {
    "person": "person",
    "organization": "organization",
    "role": "role",
    "assignment": "role_assignment",
}
_ENTITY_TABLE = {
    "person": "people",
    "organization": "organizations",
    "role": "roles",
    "assignment": "role_assignments",
}


# The walk's answer for an anchor whose row was just merged away: the survivor,
# `merged` when it is live and `archived` when it is not — the rule the retired
# cutover seed resolved anchors by (#495, #502), so a row reads the same either way.
_REPOINT_SQL = """
UPDATE producer_crosswalk
   SET pm_id = $3,
       resolution = CASE WHEN (SELECT archived_at FROM {table} WHERE id = $3) IS NULL
                         THEN 'merged' ELSE 'archived' END
 WHERE kind = $1 AND pm_id = $2
RETURNING id
"""


async def repoint_anchors(db: asyncpg.Connection, kind: str, pairs: list[tuple[str, str]]) -> int:
    """Re-point every anchor naming a merged-away row at its survivor; return how many.

    ``pairs`` is ``[(loser_id, winner_id), ...]`` in the crosswalk's ``kind``
    vocabulary (``assignment``, not ``role_assignment``). Every source moves: the
    row is gone for all of them. ``exported_pm_id`` — the producer's word — is
    left as it was: it records what the producer exported, not where it went.
    Call it after the merge, inside the merge's transaction, with a survivor that
    exists.
    """
    if kind not in ANCHOR_KINDS:
        raise ValueError(f"not a crosswalk kind: {kind!r} (one of {', '.join(ANCHOR_KINDS)})")
    sql = _REPOINT_SQL.format(table=_ENTITY_TABLE[kind])
    moved = 0
    for loser_id, winner_id in pairs:
        moved += len(await db.fetch(sql, kind, loser_id, winner_id))
    return moved
