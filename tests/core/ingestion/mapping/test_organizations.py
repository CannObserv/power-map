"""The organizations models (#497 step 5).

Four row surfaces plus gap-E merges. Parents are row-scoped: a row is a claim,
an absent row is silence, so the four PM-curated subcommittee parents that
`agency` cannot express are never clobbered.
"""

import pytest

pytest.importorskip("dbt.adapters.duckdb")

from tests.core.ingestion.mapping.conftest import (  # noqa: E402
    DEFAULT_CROSSWALK,
    EXPECTED_WARNINGS,
    MO1,
    MO2,
    MO3,
    MO4,
    MO5,
    MO6,
    MO7,
    MO8,
    MO9,
    MO10,
    O1,
    O2,
    O3,
    O4,
    O5,
    O6,
    O7,
    O8,
    O9,
    O10,
    crosswalk_row,
    fixture_csv,
)


def test_staging_reads_every_producer_organization_verbatim(build):
    b = build(select="stg_usa_wa__organizations")

    assert len(b.rows("stg_usa_wa__organizations")) == 9


def test_desired_organizations_is_identity_only(build):
    b = build()

    assert b.columns("desired_organizations") == ["pm_id", "producer_id"]
    assert {r[0] for r in b.rows("desired_organizations")} == {
        MO1,
        MO2,
        MO3,
        MO4,
        MO5,
        MO6,
        MO7,
        MO8,
        MO10,
    }
    assert MO9 not in {r[0] for r in b.rows("desired_organizations")}  # tombstone


def test_parents_are_asserted_only_where_the_producer_determines_one(build):
    """House → House chamber, Senate → Senate chamber, Joint and chambers → Legislature."""
    b = build()
    parents = {r[0]: r[1] for r in b.rows("desired_organization_parents")}

    assert b.columns("desired_organization_parents") == ["pm_id", "parent_pm_id", "producer_id"]
    assert parents[MO4] == MO2  # agency House
    assert parents[MO6] == MO1  # agency Joint
    assert parents[MO2] == MO1 and parents[MO3] == MO1  # chambers
    assert MO7 not in parents  # agency 'Other' — PM's curated parent survives
    assert MO1 not in parents and MO8 not in parents  # legislature, party: no claim


def test_an_overlay_parent_wins_over_the_mapped_one(build):
    b = build()
    parents = {r[0]: r[1] for r in b.rows("desired_organization_parents")}

    assert parents[MO5] == MO1  # mapped: Senate chamber MO3; overlay says MO1


def test_names_are_one_legal_row_from_long_name_else_name(build):
    """PM stores long_name as the canonical legal name and never stored `name`."""
    b = build()
    names = {r[0]: r for r in b.rows("desired_organization_names")}

    assert b.columns("desired_organization_names") == ["pm_id", "producer_id", "name", "name_type"]
    assert names[MO4][2] == "House Committee on Capital Budget"
    assert names[MO8][2] == "Washington State Republican Party"
    assert {r[3] for r in names.values()} == {"legal"}
    assert len(names) == 9


def test_acronyms_only_where_the_producer_publishes_one(build):
    b = build()
    rows = b.rows("desired_organization_acronyms", order_by="producer_id")

    assert b.columns("desired_organization_acronyms") == ["pm_id", "producer_id", "acronym"]
    assert rows == [(MO10, O10, "AGEC"), (MO4, O4, "CB"), (MO5, O5, "WM")]


def test_an_org_tombstone_resolves_to_its_survivor(build):
    b = build()
    merges = {r[0]: r for r in b.rows("desired_organization_merges")}

    assert merges[MO9] == (MO9, MO4, O9, O4)


def test_the_projects_own_tests_pass_with_organizations(build):
    b = build()

    assert b.statuses <= {"success", "pass", "warn"}, b.statuses
    assert b.warnings == EXPECTED_WARNINGS


def test_a_parent_the_producer_names_but_pm_cannot_resolve_is_reported(build):
    """CR 2: agency House names the House chamber; if that chamber is unanchored the
    claim cannot be made. No row is right — but silence is not. A warning names it."""
    without_house = [r for r in DEFAULT_CROSSWALK if r[3] != O2]

    b = build(crosswalk=without_house)
    parents = {r[0]: r[1] for r in b.rows("desired_organization_parents")}

    assert MO4 not in parents
    assert "unresolved_org_parents" in b.warnings


def test_an_org_tombstone_whose_survivor_is_archived_is_reported_not_re_pointed(build):
    """CR 14, the organization side: O9 → O4, and O4's PM row is archived."""
    o4_archived = crosswalk_row(O4, MO4, "archived", kind="organization")
    b = build(crosswalk=[o4_archived if r[3] == O4 else r for r in DEFAULT_CROSSWALK])
    merges = {r[0]: r for r in b.rows("desired_organization_merges")}

    assert merges[MO9][1] is None
    assert "not_null_desired_organization_merges_survivor_pm_id" in b.warnings


O11 = "01O11000000000000000000000A"  # a create: no PM anchor, so pm_id is null
O12 = "01O12000000000000000000000A"


def test_an_organization_published_with_a_blank_name_still_lands(build):
    """CR 16: a blank name warns, as it does for persons (CR 1). Identity lands, the
    legal name comes from long_name, and the other 200-odd orgs are not lost with it."""
    row = f"{O11}, ,House Committee on Nothing,,House,committee,2021-22,2025-26"
    b = build(datasets={"organizations": fixture_csv("organizations", add=[row])})

    assert b.result.success
    assert (None, O11) in b.rows("desired_organizations")
    names = {r[1]: r[2] for r in b.rows("desired_organization_names")}
    assert names[O11] == "House Committee on Nothing"
    assert "not_null_stg_usa_wa__organizations_name" in b.warnings


def test_an_unknown_org_type_is_reported_and_claims_no_parent(build):
    """A vocabulary the producer grows is not a defect in PM's build. The org lands;
    the type determines no parent; the accepted_values test warns by name."""
    row = f"{O12},Freedom Caucus,,,,caucus,,"
    b = build(datasets={"organizations": fixture_csv("organizations", add=[row])})

    assert b.result.success
    assert (None, O12) in b.rows("desired_organizations")
    assert O12 not in {r[2] for r in b.rows("desired_organization_parents")}
    prefix = "accepted_values_stg_usa_wa__organizations_org_type"
    assert any(w.startswith(prefix) for w in b.warnings)


def test_a_missing_chamber_anchor_is_reported_not_silently_unparented(build):
    """CR 17: the House chamber is found by its usa_wa_house key in the producer's own
    crosswalk. Without it no House committee can be parented — no claim is right, but
    silence would lose 101 real claims unseen. The singular test names each orphan."""
    b = build(datasets={"org_crosswalk": fixture_csv("org_crosswalk", drop="usa_wa_house")})
    parents = {r[2]: r[1] for r in b.rows("desired_organization_parents")}

    assert b.result.success
    assert O4 not in parents  # agency House, chamber unknown
    assert parents[O5] == MO1  # agency Senate: the Senate key is still there (overlay → MO1)
    assert "unresolved_org_parents" in b.warnings


def test_the_parent_rule_is_stated_once_on_the_identity_model(build):
    """CR 27: `parent_rule` names which anchor an org's agency/org_type determines. The
    anchor CASE and the unresolved_org_parents test both read it, so a new rule cannot
    be added to one and missed by the other."""
    b = build(select="+int_org_identity")
    cols = b.columns("int_org_identity")
    rule = {r[0]: r[cols.index("parent_rule")] for r in b.rows("int_org_identity")}

    assert rule[O4] == "house" and rule[O10] == "house"
    assert rule[O5] == "senate"
    assert rule[O6] == "legislature"  # agency Joint
    assert rule[O2] == "legislature" and rule[O3] == "legislature"  # chambers
    assert rule[O1] is None and rule[O7] is None and rule[O8] is None  # legislature, Other, party


def test_an_out_of_scope_child_with_a_missing_anchor_is_not_reported(build):
    """An archived org claims nothing whatever its parent resolves to, so naming it as
    unresolved is noise that could bury a real one. O5 is the only Senate child."""
    o5_archived = crosswalk_row(O5, MO5, "archived", kind="organization")
    b = build(
        crosswalk=[o5_archived if r[3] == O5 else r for r in DEFAULT_CROSSWALK],
        datasets={"org_crosswalk": fixture_csv("org_crosswalk", drop="usa_wa_senate")},
    )

    assert O5 not in {r[2] for r in b.rows("desired_organization_parents")}
    assert "unresolved_org_parents" not in b.warnings
