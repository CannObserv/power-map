"""The person identifiers mart (#710).

usa-wa's person_crosswalk carries each person's WA Legislature keys; PM's
`identifiers` table is what `resolve_entity` reads. The mart maps the two
namespaces PM has types for, presence only, onto the person each key names: its
own entity while that is in scope, else (a usa-wa tombstone) its survivor's.
"""

import pytest

pytest.importorskip("dbt.adapters.duckdb")

from tests.core.ingestion.mapping.conftest import (  # noqa: E402
    P1,
    P3,
    P4,
    P5,
    P6,
    PM1,
    PM4,
    PM6,
    fixture_csv,
)

MART = "desired_person_identifiers"
ROSTER = "person_wa_legislature_roster"
MEMBER = "person_wa_legislature_member_id"
# Unanchored usa-wa tombstones: no PM row ever named them (17 such keys in prod).
P8 = "01P8000000000000000000000A"
P9 = "01P9000000000000000000000A"


def _key(entity: str, namespace: str, value: str, merged_into: str = "") -> str:
    return f"{entity},{namespace}:{value},{namespace},{value},registrar,{merged_into}"


KEYS = [
    _key(P1, "usa_wa_legislature_roster", "peterabbarno:2021"),
    _key(P1, "wa_legislature_member_id", "31526"),
    _key(P3, "usa_wa_legislature_roster", "emilyalvarado:2023"),  # a create
    _key(P5, "usa_wa_legislature_roster", "andrewbarkis:2003"),  # archived anchor
    _key(P8, "usa_wa_legislature_roster", "pabbarno:2015", P1),  # → survivor P1
    _key(P9, "usa_wa_legislature_roster", "abarkis:2001", P5),  # survivor out of scope
]


@pytest.fixture
def built(build):
    return build(datasets={"person_crosswalk": fixture_csv("person_crosswalk", add=KEYS)})


def _rows(b) -> set[tuple]:
    cols = b.columns(MART)
    return {
        tuple(
            dict(zip(cols, r, strict=True))[c]
            for c in ("pm_id", "producer_id", "identifier_type", "value")
        )
        for r in b.rows(MART)
    }


def test_the_mart_exports_identity_type_and_value(built):
    assert built.columns(MART) == ["pm_id", "producer_id", "identifier_type", "value"]


def test_a_live_persons_roster_and_member_keys_map_to_pm_types(built):
    rows = _rows(built)

    assert (PM1, P1, ROSTER, "peterabbarno:2021") in rows
    assert (PM1, P1, MEMBER, "31526") in rows


def test_namespaces_pm_has_no_type_for_are_not_emitted(built):
    """usa_wa_legislature (P1's 31526) and wa_pdc (1688) stay out (#710 decision 2)."""
    rows = _rows(built)

    assert not [r for r in rows if r[3] == "1688"]
    assert [r for r in rows if r[3] == "31526"] == [(PM1, P1, MEMBER, "31526")]


def test_a_create_carries_its_key_with_no_pm_id(built):
    assert (None, P3, ROSTER, "emilyalvarado:2023") in _rows(built)


def test_an_archived_anchors_key_is_out_of_scope(built):
    """Writing onto a soft-deleted row is the #481 hazard."""
    assert not [r for r in _rows(built) if r[1] == P5]


def test_an_unanchored_tombstones_key_lands_on_its_survivor(built):
    """usa-wa's merged_into says the key names the survivor (#710 decision 3)."""
    rows = _rows(built)

    assert (PM1, P1, ROSTER, "pabbarno:2015") in rows
    assert not [r for r in rows if r[1] == P8]


def test_a_tombstone_whose_survivor_is_out_of_scope_is_dropped(built):
    assert not [r for r in _rows(built) if r[3] == "abarkis:2001"]


def test_a_tombstone_with_its_own_live_anchor_keeps_its_key_until_pm_merges(build):
    """P4 and P6 still name live PM rows; the person merge re-homes identifiers."""
    rows = _rows(build())

    assert (PM4, P4, ROSTER, "peterabbarno:2019") in rows
    assert (PM6, P6, ROSTER, "pabbarno:2017") in rows
