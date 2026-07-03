"""Unit tests for the WA legislative seat generator (#263).

Uses synthetic jurisdiction input — the real jurisdictions seed lives in the
gitignored ``data/cannabis_observer/`` (a local data artifact), so tests must
not depend on it.
"""

import pytest

from scripts.generate_wa_seats import build_seed, generate_seats


def _synthetic_lds(n: int) -> list[dict]:
    return [
        {"slug": f"usa-wa-ld-{i}", "name": f"LD{i}", "type": "legislative_district"}
        for i in range(1, n + 1)
    ]


def test_three_seats_per_ld_in_ld_number_order():
    jurs = [
        {"slug": "usa-wa-ld-2", "name": "LD2", "type": "legislative_district"},
        {"slug": "usa-wa-ld-1", "name": "LD1", "type": "legislative_district"},
        {"slug": "usa-wa", "name": "WA", "type": "state"},  # non-LD, ignored
    ]
    seats = generate_seats(jurs)
    assert len(seats) == 6
    assert [s["title"] for s in seats[:3]] == [
        "Washington State Senator, LD-1",
        "Washington State Representative, LD-1, Position 1",
        "Washington State Representative, LD-1, Position 2",
    ]
    # LD-2 follows LD-1 (numeric order, not lexical)
    assert seats[3]["title"] == "Washington State Senator, LD-2"


def test_seat_field_shapes():
    sen, h1, h2 = generate_seats(
        [{"slug": "usa-wa-ld-5", "name": "LD5", "type": "legislative_district"}]
    )
    assert sen == {
        "chamber": "usa_wa_senate",
        "role_type": "state_senator",
        "jurisdiction_slug": "usa-wa-ld-5",
        "qualifier": None,
        "title": "Washington State Senator, LD-5",
    }
    assert h1["chamber"] == "usa_wa_house"
    assert h1["role_type"] == "state_representative"
    assert h1["qualifier"] == "Position 1"
    assert h1["title"] == "Washington State Representative, LD-5, Position 1"
    assert h2["qualifier"] == "Position 2"


def test_malformed_ld_slug_raises():
    with pytest.raises(ValueError, match="usa-wa-ld"):
        generate_seats([{"slug": "usa-wa-ld-foo", "type": "legislative_district"}])


def test_49_lds_yield_147_seats():
    seats = generate_seats(_synthetic_lds(49))
    assert len(seats) == 147
    assert sum(1 for s in seats if s["chamber"] == "usa_wa_senate") == 49
    assert sum(1 for s in seats if s["chamber"] == "usa_wa_house") == 98
    # every senate seat has a NULL qualifier; every house seat is Position 1/2
    assert all(s["qualifier"] is None for s in seats if s["chamber"] == "usa_wa_senate")
    assert {s["qualifier"] for s in seats if s["chamber"] == "usa_wa_house"} == {
        "Position 1",
        "Position 2",
    }


def test_build_seed_shape():
    seed = build_seed([{"slug": "usa-wa-ld-1", "type": "legislative_district"}])
    assert "_comment" in seed
    assert isinstance(seed["seats"], list)
    assert len(seed["seats"]) == 3
