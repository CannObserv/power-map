"""The persons models (#497 step 4).

What the desired state asserts for a person is settled in the design doc:
identity plus one legal name. What these tests pin is the row scope — who is
in, who is out, and where a usa-wa tombstone points (addendum gap E).
"""

import pytest

pytest.importorskip("dbt.adapters.duckdb")

from tests.core.ingestion.mapping.conftest import (  # noqa: E402
    DEFAULT_CROSSWALK,
    EXPECTED_WARNINGS,
    P1,
    P2,
    P3,
    P4,
    P5,
    P6,
    P7,
    PM1,
    PM2,
    PM4,
    PM6,
    PM7,
    crosswalk_row,
)


def test_staging_reads_every_producer_person_verbatim(build):
    b = build(select="stg_usa_wa__persons")

    assert [r[0] for r in b.rows("stg_usa_wa__persons")] == [P1, P2, P3, P5, P7]


def test_identity_resolves_each_producer_person_through_the_crosswalk(build):
    b = build(select="+int_person_identity")
    by_producer = {r[0]: r for r in b.rows("int_person_identity")}
    cols = b.columns("int_person_identity")
    col = {c: i for i, c in enumerate(cols)}

    assert by_producer[P1][col["pm_id"]] == PM1
    assert by_producer[P1][col["resolution"]] == "live"
    assert by_producer[P2][col["pm_id"]] == PM2  # PM-side merge: already the survivor
    assert by_producer[P3][col["pm_id"]] is None  # unanchored → a create


def test_an_archived_anchor_is_out_of_scope_entirely(build):
    """Writing onto a soft-deleted row is the #481 hazard; minting a twin is worse."""
    b = build()

    assert P5 not in {r[1] for r in b.rows("desired_people")}


def test_desired_people_is_identity_only(build):
    b = build()

    assert b.columns("desired_people") == ["pm_id", "producer_id"]
    assert b.rows("desired_people", order_by="producer_id") == [
        (PM1, P1),
        (PM2, P2),
        (None, P3),
        (PM7, P7),
    ]


def test_desired_person_names_is_one_legal_name_per_person_with_the_overlay_winning(build):
    b = build()
    rows = {r[1]: r for r in b.rows("desired_person_names")}

    assert b.columns("desired_person_names") == ["pm_id", "producer_id", "name", "name_type"]
    assert rows[P1][2] == "Curated One"  # overlay row for PM1.name
    assert rows[P2][2] == "Hunter Abell"  # mapped
    assert rows[P3] == (None, P3, "Emily Alvarado", "legal")  # a create still carries its name
    assert {r[3] for r in rows.values()} == {"legal"}


def test_a_usa_wa_tombstone_resolves_to_its_survivors_pm_row(build):
    """Gap E: the tombstone is the only signal that re-points a merged-away person.

    P4 is absent from persons — retraction-as-absence — and present in
    person_crosswalk with merged_into = P1. PM still holds PM4. The model must
    say PM4's survivor is PM1, and must not emit PM4 as a live person.
    """
    b = build()
    merges = {r[0]: r for r in b.rows("desired_person_merges")}

    assert b.columns("desired_person_merges") == [
        "loser_pm_id",
        "survivor_pm_id",
        "loser_producer_id",
        "survivor_producer_id",
    ]
    assert merges[PM4] == (PM4, PM1, P4, P1)
    assert PM4 not in {r[0] for r in b.rows("desired_people")}


def test_a_two_hop_tombstone_chain_reaches_the_final_survivor(build):
    """P6 → P4 → P1. Stopping one hop short would re-point at another tombstone."""
    b = build()
    merges = {r[0]: r for r in b.rows("desired_person_merges")}

    assert merges[PM6] == (PM6, PM1, P6, P1)


def test_a_model_that_ignored_the_tombstone_would_fail_here(build):
    """The seam-crossing assertion the design promised: no tombstone, no merge row."""
    b = build()

    losers = {r[2] for r in b.rows("desired_person_merges")}
    assert losers == {P4, P6}


def test_the_projects_own_tests_pass_on_the_fixture(build):
    """dbt's unique / not_null / relationships tests run as part of `build`."""
    b = build()

    assert b.statuses <= {"success", "pass", "warn"}, b.statuses
    assert b.warnings == EXPECTED_WARNINGS


def test_a_person_published_with_a_blank_name_keeps_identity_but_asserts_no_name(build):
    """Real data: five anchored legislators arrive with name_full of ' ' or ''.

    A space is not NULL. Without trimming at staging the model would assert a
    legal name of ' ' for four of them. Identity stands; the name row does not,
    so PM's own legal name is left exactly as it is (names are assert-only).
    """
    b = build()

    assert (PM7, P7) in b.rows("desired_people")
    assert P7 not in {r[1] for r in b.rows("desired_person_names")}


def test_a_tombstone_whose_survivor_is_unanchored_is_reported_not_fatal(build):
    """CR 1: PM cannot re-point PM4 when P1 has no PM row. That is a finding for
    the applier to report — not a reason for the nightly build to produce nothing."""
    without_p1 = [r for r in DEFAULT_CROSSWALK if r[3] != P1]

    b = build(crosswalk=without_p1)
    merges = {r[0]: r for r in b.rows("desired_person_merges")}

    assert merges[PM4][1] is None
    assert "not_null_desired_person_merges_survivor_pm_id" in b.warnings


def _with(crosswalk, producer_id, row):
    """DEFAULT_CROSSWALK with one producer's row replaced."""
    return [row if r[3] == producer_id else r for r in crosswalk]


def test_a_tombstone_whose_survivor_is_archived_is_reported_not_re_pointed(build):
    """CR 14: an archived survivor is out of scope here as everywhere else. A non-null
    survivor_pm_id is an instruction to act; pointing it at a soft-deleted row is the
    #481 hazard by another door — the same gap CR 2 closed for parents."""
    b = build(crosswalk=_with(DEFAULT_CROSSWALK, P1, crosswalk_row(P1, PM1, "archived")))
    merges = {r[0]: r for r in b.rows("desired_person_merges")}

    assert merges[PM4][1] is None
    assert merges[PM6][1] is None  # two hops away, same survivor
    assert "not_null_desired_person_merges_survivor_pm_id" in b.warnings


def test_a_merge_pm_already_made_is_not_re_instructed(build):
    """P4's row says PM already merged it into PM1 — the merge usa-wa now publishes.
    A loser that already points at its survivor is nothing to re-point."""
    b = build(crosswalk=_with(DEFAULT_CROSSWALK, P4, crosswalk_row(P4, PM1, "merged")))
    losers = {r[2] for r in b.rows("desired_person_merges")}

    assert P4 not in losers
    assert P6 in losers  # PM6 → PM1 still stands


def test_an_archived_loser_is_out_of_scope(build):
    """A merge instruction is a write; an archived loser gets none, like every other write."""
    b = build(crosswalk=_with(DEFAULT_CROSSWALK, P4, crosswalk_row(P4, PM4, "archived")))

    assert P4 not in {r[2] for r in b.rows("desired_person_merges")}
