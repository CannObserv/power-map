"""The assignment models (#527 step 8).

`desired_role_assignments` is one row per published span, keyed by its
`span_key`, carrying the person's and the role's producer ids for the applier
to resolve — never PM ids, which a create does not have. A span whose anchor
left scope is neither diffed nor created. `desired_role_assignment_dates`
derives the three owned columns from `valid_to` — the dataset's own rule, since
`is_active` is exactly "no end" — and lets each pinned slot win by presence
(#498), keeping the pair legal against `chk_current_no_end_date`.
"""

from datetime import date

import pytest

pytest.importorskip("dbt.adapters.duckdb")

from tests.core.ingestion.mapping.conftest import (  # noqa: E402
    DEFAULT_OVERLAY,
    P1,
    P3,
    R1,
    R2,
    RA1,
    RA2,
    SPAN_CLOSED,
    SPAN_NEW,
    SPAN_OPEN,
    SPAN_OUT,
    fixture_csv,
    overlay_row,
)

SPANS, DATES = "desired_role_assignments", "desired_role_assignment_dates"


def _by_span(built, model):
    return {r[1]: r for r in built.rows(model)}


def _pinned(*pins):
    return [*DEFAULT_OVERLAY, *(overlay_row("assignment", *p) for p in pins)]


def test_each_published_span_is_one_row_keyed_by_its_span_key(build):
    rows = _by_span(build(), SPANS)

    assert rows == {
        SPAN_OPEN: (RA1, SPAN_OPEN, P1, R1, date(2021, 1, 11)),
        SPAN_CLOSED: (RA2, SPAN_CLOSED, P1, R2, date(2017, 1, 9)),
        SPAN_NEW: (None, SPAN_NEW, P3, R1, date(2023, 1, 9)),
    }


def test_a_span_whose_anchor_left_scope_is_neither_diffed_nor_created(build):
    """Its anchor was archived: the applier must not touch it, nor mint its twin."""
    built = build()

    assert SPAN_OUT not in _by_span(built, SPANS)
    assert SPAN_OUT not in _by_span(built, DATES)


def test_the_dates_follow_valid_to(build):
    assert _by_span(build(), DATES) == {
        SPAN_OPEN: (RA1, SPAN_OPEN, date(2021, 1, 11), None, True),
        SPAN_CLOSED: (RA2, SPAN_CLOSED, date(2017, 1, 9), date(2020, 12, 31), False),
        SPAN_NEW: (None, SPAN_NEW, date(2023, 1, 9), date(2024, 12, 31), False),
    }


def test_a_pinned_start_date_wins(build):
    rows = _by_span(build(overlay=_pinned((RA1, "start_date", "2021-02-01"))), DATES)

    assert rows[SPAN_OPEN][2:] == (date(2021, 2, 1), None, True)


def test_a_pinned_end_date_closes_an_open_span(build):
    rows = _by_span(build(overlay=_pinned((RA1, "end_date", "2024-06-30"))), DATES)

    assert rows[SPAN_OPEN][2:] == (date(2021, 1, 11), date(2024, 6, 30), False)


def test_a_pinned_current_reopens_a_closed_span(build):
    """`str(True)` is what a pin stores for a boolean slot."""
    rows = _by_span(build(overlay=_pinned((RA2, "is_current", "True"))), DATES)

    assert rows[SPAN_CLOSED][2:] == (date(2017, 1, 9), None, True)


def test_a_null_end_pin_keeps_the_producers_currency(build):
    """PM holds the span closed with no known end: allowed, and not a reopening."""
    rows = _by_span(build(overlay=_pinned((RA2, "end_date", None))), DATES)

    assert rows[SPAN_CLOSED][2:] == (date(2017, 1, 9), None, False)


def test_pins_that_contradict_the_check_fail_the_build(build):
    """The admin never pins such a pair; if one lands, the UPDATE must not be planned."""
    built = build(
        overlay=_pinned((RA1, "is_current", "True"), (RA1, "end_date", "2024-06-30")),
        must_succeed=False,
    )

    assert "desired_role_assignment_dates_current_has_no_end" in built.failures


def test_a_malformed_pin_is_named_and_applied_nowhere(build):
    built = build(overlay=_pinned((RA1, "start_date", "soon")))

    assert "overlay_value_malformed" in built.warnings
    assert _by_span(built, DATES)[SPAN_OPEN][2] == date(2021, 1, 11)


def test_a_span_whose_role_is_unpublished_is_kept_and_named(build):
    """Dropping it would read as absence — the applier would archive a live tenure."""
    orphan = f"{P1}|caucus-role:9|caucus|9|2021-22"
    line = ",".join(
        [P1, "1", "usa_wa_legislature", "caucus-role:9", "caucus", "9"]
        + ["2021-22", "2021-22", "2021-01-11", "", "true", orphan]
    )

    built = build(datasets={"assignments": fixture_csv("assignments", add=[line])})

    assert _by_span(built, SPANS)[orphan][3] is None
    assert "unresolved_assignment_roles" in built.warnings


def test_assignment_pins_are_in_the_overlay_vocabulary(build):
    built = build(
        overlay=_pinned(
            (RA1, "start_date", "2021-02-01"),
            (RA2, "end_date", "2020-06-30"),
            (RA2, "is_current", "False"),
        )
    )

    assert "overlay_field_unmapped" not in built.warnings
