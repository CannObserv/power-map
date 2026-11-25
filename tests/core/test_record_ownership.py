"""#882: a record's ownership relative to the calling key — the decision table."""

import pytest

from src.core.record_ownership import Ownership, ownership

_MINE = "01KEYMINE000000000000000000"
_THEIRS = "01KEYTHEIRS00000000000000000"


@pytest.mark.parametrize(
    ("stored", "dataset", "expected"),
    [
        (None, False, Ownership.NONE),
        (_MINE, False, Ownership.SELF),
        (_THEIRS, False, Ownership.OTHER),
        (None, True, Ownership.DATASET),
        (_MINE, True, Ownership.DATASET),
        (_THEIRS, True, Ownership.DATASET),
    ],
)
def test_ownership(stored, dataset, expected):
    assert ownership(stored, _MINE, dataset=dataset) is expected


def test_the_values_on_the_wire():
    assert [o.value for o in Ownership] == ["self", "other", "none", "dataset"]
