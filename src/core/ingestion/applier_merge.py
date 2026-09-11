"""The merge primitives a `merge` binding may act through (#514).

A producer tombstone is the one diff entry no statement can express: folding
one PM row into another is a whole merge — names deduped, assignments
re-pointed, tombstones and subscriptions written — and PM already has that code,
used by the admin. The manifest names a primitive (`target.primitive`); this
registry maps the name to the core functions: the merge itself, its read-only
preview, and the crosswalk kind of the rows the merge drops as duplicates (each
carries an anchor of its own). A name the manifest may use and this registry
lacks fails `tests/core/ingestion/test_applier_merge_registry.py`.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from src.core.person_merge import merge_person_into, preview_person_merge

__all__ = ["MERGE_PRIMITIVES", "MergePrimitive"]


@dataclass(frozen=True)
class MergePrimitive:
    """One core merge, as the applier calls it."""

    # (db, *, winner_id, loser_id, actor_email) → the (loser, winner) pairs dropped
    merge: Callable[..., Awaitable[list[tuple[str, str]]]]
    # (db, *, winner_id, loser_id) → what the merge would do, JSON-native
    preview: Callable[..., Awaitable[dict]]
    # the crosswalk kind of a row the merge drops as a duplicate of a survivor's
    dropped_kind: str


MERGE_PRIMITIVES: dict[str, MergePrimitive] = {
    "person": MergePrimitive(
        merge=merge_person_into, preview=preview_person_merge, dropped_kind="assignment"
    ),
}
