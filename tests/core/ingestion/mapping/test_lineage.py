"""org_lineage → org link events (#592).

usa-wa's `org_lineage` publishes committee links — `succeeded_by`, `merged_with`,
`split_from` — keyed (subject, slug, linked). PM owns them on anchored pairs: a
link lands only when both ends are in scope, and one with an end that is not is
named by a warn test and lands the night after its create executes.
"""

import pytest

pytest.importorskip("dbt.adapters.duckdb")

from tests.core.ingestion.mapping.conftest import (  # noqa: E402
    DEFAULT_CROSSWALK,
    MO4,
    MO5,
    MO6,
    MO7,
    MO10,
    O4,
    O5,
    O6,
    O7,
    O9,
    O10,
    crosswalk_row,
    fixture_csv,
)

LINEAGE = "org_lineage"
UNRESOLVED = "unresolved_org_lineage"
O11 = "01O11000000000000000000000A"  # published, never anchored: a create
O12 = "01O12000000000000000000000A"  # in no dataset at all


def _link(subject: str, slug: str, linked: str, year: int = 2023) -> str:
    return f"{subject},{slug},{linked},1,2,{year},,"


def test_staging_carries_every_published_link_typed(build):
    b = build()

    assert b.columns("stg_usa_wa__org_lineage") == [
        "subject_entity_id",
        "slug",
        "linked_entity_id",
        "effective_year",
    ]
    assert b.rows("stg_usa_wa__org_lineage", order_by="subject_entity_id, slug") == [
        (O10, "merged_with", O4, 2001),
        (O5, "succeeded_by", O4, 2021),
        (O7, "split_from", O6, 2015),
    ]


def test_an_unknown_link_type_fails_the_build_rather_than_being_ignored(build):
    """A fourth slug is a contract change PM has not mapped: halt, never drop it."""
    b = build(
        datasets={LINEAGE: fixture_csv(LINEAGE, add=[f"{O6},absorbed_by,{O4},31000,31001,2023,,"])},
        must_succeed=False,
    )

    assert (
        "accepted_values_stg_usa_wa__org_lineage_slug__succeeded_by__merged_with__split_from"
        in b.failures
    )


def test_each_link_between_in_scope_orgs_is_claimed_with_both_pm_ids(build):
    b = build()

    assert b.columns("desired_organization_lineage") == [
        "pm_id",
        "producer_id",
        "event_type",
        "linked_pm_id",
        "linked_producer_id",
        "event_year",
    ]
    assert b.rows("desired_organization_lineage", order_by="producer_id, event_type") == [
        (MO10, O10, "merged_with", MO4, O4, 2001),
        (MO5, O5, "succeeded_by", MO4, O4, 2021),
        (MO7, O7, "split_from", MO6, O6, 2015),
    ]
    assert UNRESOLVED not in b.warnings


def test_a_link_to_an_org_this_run_creates_waits_and_is_named(build):
    """The linked end has no PM id until its create executes; the link lands the night after."""
    org = f"{O11},New Committee,,,House,committee,2025-26,2025-26,true"
    b = build(
        datasets={
            "organizations": fixture_csv("organizations", add=[org]),
            LINEAGE: fixture_csv(LINEAGE, add=[_link(O4, "succeeded_by", O11)]),
        }
    )

    assert O11 not in {r[4] for r in b.rows("desired_organization_lineage")}
    assert UNRESOLVED in b.warnings


def test_a_link_from_an_org_this_run_creates_waits_too(build):
    org = f"{O11},New Committee,,,House,committee,2025-26,2025-26,true"
    b = build(
        datasets={
            "organizations": fixture_csv("organizations", add=[org]),
            LINEAGE: fixture_csv(LINEAGE, add=[_link(O11, "split_from", O4)]),
        }
    )

    assert O11 not in {r[1] for r in b.rows("desired_organization_lineage")}
    assert UNRESOLVED in b.warnings


def test_a_link_with_an_archived_end_claims_nothing(build):
    """Out of scope is out of scope at either end: the applier must not touch it."""
    crosswalk = [r for r in DEFAULT_CROSSWALK if r[3] != O6] + [
        crosswalk_row(O6, MO6, "archived", kind="organization")
    ]
    b = build(crosswalk=crosswalk)

    assert O7 not in {r[1] for r in b.rows("desired_organization_lineage")}
    assert UNRESOLVED in b.warnings


def test_a_link_naming_an_org_nobody_publishes_is_named(build):
    b = build(datasets={LINEAGE: fixture_csv(LINEAGE, add=[_link(O4, "succeeded_by", O12)])})

    assert O12 not in {r[4] for r in b.rows("desired_organization_lineage")}
    assert UNRESOLVED in b.warnings


def test_a_link_naming_a_tombstoned_producer_id_is_named_not_followed(build):
    """O9 is merged into O4 on usa-wa's side; a link still naming O9 is a producer defect."""
    b = build(datasets={LINEAGE: fixture_csv(LINEAGE, add=[_link(O9, "succeeded_by", O6)])})

    assert O9 not in {r[1] for r in b.rows("desired_organization_lineage")}
    assert UNRESOLVED in b.warnings


def test_a_duplicated_link_halts_the_build(build):
    """The manifest keys a link on (subject, type, linked); a repeat is a model defect."""
    b = build(
        datasets={LINEAGE: fixture_csv(LINEAGE, add=[_link(O5, "succeeded_by", O4, 2022)])},
        must_succeed=False,
    )

    assert "desired_organization_lineage_key_unique" in b.failures


def test_a_link_from_an_org_to_itself_is_named_not_claimed(build):
    """CR 1: a dated self-link would end the org's own lifespan; entity_events has no
    CHECK against one, so the mart refuses it and the warn test names it."""
    b = build(datasets={LINEAGE: fixture_csv(LINEAGE, add=[_link(O6, "succeeded_by", O6)])})

    assert O6 not in {r[1] for r in b.rows("desired_organization_lineage")}
    assert UNRESOLVED in b.warnings
