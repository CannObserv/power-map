"""The manifest's merge-primitive vocabulary and the applier's registry agree (#514).

The manifest loader accepts a `target.primitive` from `MERGE_PRIMITIVES`; the
applier calls it through `applier_merge.MERGE_PRIMITIVES`. A name in the first
and not the second would load cleanly and fail at 09:30 with a KeyError.
"""

from src.core.ingestion import applier_merge
from src.core.ingestion.mapping import load_manifest
from src.core.ingestion.mapping.manifest import MERGE_PRIMITIVES
from src.core.person_merge import merge_person_into, preview_person_merge


def test_every_primitive_the_manifest_may_name_is_registered():
    assert set(MERGE_PRIMITIVES) == set(applier_merge.MERGE_PRIMITIVES)


def test_every_bound_merge_table_names_a_registered_primitive():
    bound = {
        name: spec.target.primitive
        for name, spec in load_manifest().tables.items()
        if spec.target.shape == "merge" and spec.target.primitive
    }

    assert bound == {"desired_person_merges": "person"}
    assert set(bound.values()) <= set(applier_merge.MERGE_PRIMITIVES)


def test_the_person_primitive_is_the_admins_own_merge():
    """#514's premise: the applier folds a person exactly as a curator's merge does."""
    person = applier_merge.MERGE_PRIMITIVES["person"]

    assert person.merge is merge_person_into
    assert person.preview is preview_person_merge
    assert person.dropped_kind == "assignment"
