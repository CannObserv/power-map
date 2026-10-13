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

__all__ = ["DATASET_OWNED_LINK_TYPES"]

# The org→org link types a dataset owns on anchored pairs.
DATASET_OWNED_LINK_TYPES: frozenset[str] = frozenset({"succeeded_by", "merged_with", "split_from"})
