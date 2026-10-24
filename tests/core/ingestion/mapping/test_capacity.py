"""role_periods → role_capacity_periods (#671).

usa-wa's `role_periods` 1.0.0 publishes, per role, how many principals it seats
at once in each period: dense, tiled, `valid_to` the inclusive last day and null
when open, `max_holders` null for no limit. `desired_role_capacity_periods`
claims each period under the role's PM id; a period whose role PM cannot claim
is named by a warn test and claims nothing.
"""

from datetime import date

import pytest

pytest.importorskip("dbt.adapters.duckdb")

from tests.core.ingestion.mapping.conftest import (  # noqa: E402
    DEFAULT_CROSSWALK,
    MR1,
    MR2,
    R1,
    R2,
    R3,
    crosswalk_row,
    fixture_csv,
)

PERIODS = "role_periods"
CAPACITY = "desired_role_capacity_periods"
UNRESOLVED = "unresolved_role_capacity_periods"


def _with(*lines: str, replace=None) -> dict[str, str]:
    return {PERIODS: fixture_csv(PERIODS, add=list(lines), replace=replace)}


def test_staging_carries_every_published_period_typed(build):
    """The basis is usa-wa's provenance; PM keeps no column for it (design S3)."""
    b = build()

    assert b.columns("stg_usa_wa__role_periods") == [
        "role_key",
        "valid_from",
        "valid_to",
        "max_holders",
    ]
    assert b.rows("stg_usa_wa__role_periods", order_by="role_key, valid_from") == [
        ("committee-member-role:31640", date(2021, 1, 1), None, None),
        ("party-role:republican", date(2017, 1, 1), None, None),
        ("seat:senate:ld-34", date(1889, 1, 1), date(1890, 12, 31), 3),
        ("seat:senate:ld-34", date(1891, 1, 1), None, 1),
    ]


def test_a_capacity_that_is_not_a_number_fails_the_build(build):
    """Read as null it would mean "no limit" and hide every overlap on the seat.

    Staging is a view, so the strict cast fails where the mart first reads it.
    """
    b = build(
        datasets=_with(replace={"1889-01-01,1890-12-31,3,": "1889-01-01,1890-12-31,three,"}),
        must_succeed=False,
    )

    assert CAPACITY in b.failures


def test_each_period_is_claimed_under_its_roles_pm_id(build):
    """A role this run creates carries no pm_id; the applier inserts under the new row."""
    b = build()

    assert b.columns(CAPACITY) == ["pm_id", "producer_id", "valid_from", "valid_to", "max_holders"]
    assert b.rows(CAPACITY, order_by="producer_id, valid_from") == [
        (MR1, R1, date(2021, 1, 1), None, None),
        (MR2, R2, date(2017, 1, 1), None, None),
        (None, R3, date(1889, 1, 1), date(1890, 12, 31), 3),
        (None, R3, date(1891, 1, 1), None, 1),
    ]
    assert UNRESOLVED not in b.warnings


def test_a_period_whose_role_pm_will_not_create_is_named_and_claims_nothing(build):
    """R4 is a seat PM's guards would refuse; desired_roles drops it (#529)."""
    b = build(datasets=_with("seat:house:ld-34,2001-01-01,,1,kind"))

    assert UNRESOLVED in b.warnings
    assert all(r[1] != "seat:house:ld-34" for r in b.rows(CAPACITY))
    assert len(b.rows(CAPACITY)) == 4


def test_a_period_for_an_unpublished_role_is_named(build):
    b = build(datasets=_with("seat:senate:ld-99,1889-01-01,,1,kind"))

    assert UNRESOLVED in b.warnings


def test_two_periods_on_one_start_fail_the_build(build):
    """The applier's key: a duplicate cannot be landed, so the build halts on it."""
    b = build(datasets=_with("seat:senate:ld-34,1891-01-01,,2,roster"), must_succeed=False)

    assert f"{CAPACITY}_key_unique" in b.failures


def test_two_producer_roles_pm_holds_as_one_fail_the_build(build):
    """CR 1: a `merged` role anchor stays in scope under its survivor's pm_id, so two
    usa-wa roles PM merged would both claim one (role, start), which the partial
    unique index would refuse mid-execute. desired_roles' own pm_id key halts the
    build first, so no capacity period can reach the applier twice."""
    merged = [
        r if r[3] != R2 else crosswalk_row(R2, MR1, "merged", kind="role")
        for r in DEFAULT_CROSSWALK
    ]
    b = build(
        crosswalk=merged,
        datasets=_with("party-role:republican,2021-01-01,,,kind"),
        must_succeed=False,
    )

    assert "unique_desired_roles_pm_id" in b.failures


@pytest.mark.parametrize(
    ("before", "after"),
    [
        ("1889-01-01,1890-12-31,3,", "1889-01-01,1888-12-31,3,"),  # ends before it starts
        ("1889-01-01,1890-12-31,3,", "1889-01-01,1890-12-31,0,"),  # seats no one
    ],
)
def test_a_period_the_table_would_refuse_fails_the_build(build, before, after):
    """CR 2: chk_capacity_period_range / chk_capacity_period_max, mirrored. Planned
    clean, such a row would abort the supervised execute; it halts the build instead."""
    b = build(datasets=_with(replace={before: after}), must_succeed=False)

    assert f"{CAPACITY}_invalid" in b.failures
