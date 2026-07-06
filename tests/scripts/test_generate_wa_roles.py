"""Unit tests for the WA legislative role generator (#263).

Uses synthetic jurisdiction input — the real jurisdictions seed lives in the
gitignored ``data/cannabis_observer/`` (a local data artifact), so tests must
not depend on it.
"""

import sys

import pytest

from scripts.generate_wa_roles import build_seed, generate_roles
from scripts.generate_wa_roles import main as generate_main


def _synthetic_lds(n: int) -> list[dict]:
    return [
        {"slug": f"usa-wa-ld-{i}", "name": f"LD{i}", "type": "legislative_district"}
        for i in range(1, n + 1)
    ]


def test_three_roles_per_ld_in_ld_number_order():
    jurs = [
        {"slug": "usa-wa-ld-2", "name": "LD2", "type": "legislative_district"},
        {"slug": "usa-wa-ld-1", "name": "LD1", "type": "legislative_district"},
        {"slug": "usa-wa", "name": "WA", "type": "state"},  # non-LD, ignored
    ]
    roles = generate_roles(jurs)
    assert len(roles) == 6
    assert [r["title"] for r in roles[:3]] == [
        "Washington State Senator, LD-1",
        "Washington State Representative, LD-1, Position 1",
        "Washington State Representative, LD-1, Position 2",
    ]
    # LD-2 follows LD-1 (numeric order, not lexical)
    assert roles[3]["title"] == "Washington State Senator, LD-2"


def test_role_field_shapes():
    sen, h1, h2 = generate_roles(
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
        generate_roles([{"slug": "usa-wa-ld-foo", "type": "legislative_district"}])


def test_49_lds_yield_147_roles():
    roles = generate_roles(_synthetic_lds(49))
    assert len(roles) == 147
    assert sum(1 for r in roles if r["chamber"] == "usa_wa_senate") == 49
    assert sum(1 for r in roles if r["chamber"] == "usa_wa_house") == 98
    # every senate role has a NULL qualifier; every house role is Position 1/2
    assert all(r["qualifier"] is None for r in roles if r["chamber"] == "usa_wa_senate")
    assert {r["qualifier"] for r in roles if r["chamber"] == "usa_wa_house"} == {
        "Position 1",
        "Position 2",
    }


def test_build_seed_shape():
    seed = build_seed([{"slug": "usa-wa-ld-1", "type": "legislative_district"}])
    assert "_comment" in seed
    assert isinstance(seed["roles"], list)
    assert len(seed["roles"]) == 3


def test_main_missing_file_exits(monkeypatch):
    """main() reports a friendly SystemExit (not a traceback) for a missing input file."""
    monkeypatch.setattr(sys, "argv", ["generate_wa_roles", "/no/such/jurisdictions.json"])
    with pytest.raises(SystemExit):
        generate_main()
