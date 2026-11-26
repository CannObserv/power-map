"""Who may change a record through the observation API, relative to the caller (#882).

A producer reads this before writing, so it can tell an update that will hold
from one that will be refused or reverted:

- ``dataset``: a subscribed dataset owns the row — its crosswalk anchor is in
  scope — so the nightly apply reverts any observation write. It comes first,
  whoever holds ``source_key_id``: the applier writes without consulting that
  column (#499), so a key can hold the stamp on a row it cannot keep.
- ``self`` / ``other``: the row's ``source_key_id`` is the caller's, or another
  key's — the same-or-NULL gate's two answers.
- ``none``: no one holds it; the first id-addressed write claims it.

The read (``AssignmentDetail.ownership``, ``OrgDetail.parent_ownership``) and the
write refusal (``owned_by_dataset``, in ``src/core/observation.py``) share
:func:`dataset_owned`, so the two cannot disagree. People and roles carry no
ownership: no gate exists there (``docs/OBSERVATIONS.md`` § Record ownership).
"""

from enum import StrEnum

import asyncpg

from src.core.dataset_ownership import anchored_assignments, anchored_orgs

__all__ = [
    "Ownership",
    "dataset_owned",
    "ownership",
]

# The crosswalk kinds whose rows the observation API can overwrite, and the
# anchor lookup for each.
_ANCHORED = {"assignment": anchored_assignments, "organization": anchored_orgs}


class Ownership(StrEnum):
    """A record's ownership relative to the calling key."""

    SELF = "self"
    OTHER = "other"
    NONE = "none"
    DATASET = "dataset"


def ownership(source_key_id: str | None, caller_key_id: str, *, dataset: bool) -> Ownership:
    """The decision table: ``dataset`` first, then the ``source_key_id`` gate's answer."""
    if dataset:
        return Ownership.DATASET
    if source_key_id is None:
        return Ownership.NONE
    return Ownership.SELF if source_key_id == caller_key_id else Ownership.OTHER


async def dataset_owned(conn: asyncpg.Connection, kind: str, ids: list[str]) -> set[str]:
    """The rows among ``ids`` of crosswalk ``kind`` a subscribed dataset owns."""
    return await _ANCHORED[kind](conn, ids)
