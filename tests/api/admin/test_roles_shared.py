"""Unit tests for roles_shared helpers."""

import datetime as dt

from src.api.admin.roles_shared import (
    _check_assignment_within_bounds,
    positioned_at_large_error,
    positionless_seat_error,
)


def test_bounds_no_constraints_returns_none():
    assert _check_assignment_within_bounds(None, None, None, None) is None


def test_bounds_start_before_established_returns_error():
    err = _check_assignment_within_bounds(dt.date(2009, 12, 31), None, dt.date(2010, 1, 1), None)
    assert err is not None
    assert "established" in err.lower()


def test_bounds_start_on_established_ok():
    assert (
        _check_assignment_within_bounds(dt.date(2010, 1, 1), None, dt.date(2010, 1, 1), None)
        is None
    )


def test_bounds_end_after_abolished_returns_error():
    err = _check_assignment_within_bounds(None, dt.date(2021, 1, 1), None, dt.date(2020, 12, 31))
    assert err is not None
    assert "abolished" in err.lower()


def test_bounds_end_on_abolished_ok():
    assert (
        _check_assignment_within_bounds(None, dt.date(2020, 12, 31), None, dt.date(2020, 12, 31))
        is None
    )


def test_bounds_start_after_abolished_returns_error():
    err = _check_assignment_within_bounds(dt.date(2021, 6, 1), None, None, dt.date(2020, 12, 31))
    assert err is not None


def test_bounds_end_before_established_returns_error():
    err = _check_assignment_within_bounds(None, dt.date(2009, 1, 1), dt.date(2010, 1, 1), None)
    assert err is not None


def test_bounds_null_dates_with_constraints_ok():
    """Null assignment dates are valid regardless of role bounds."""
    assert (
        _check_assignment_within_bounds(None, None, dt.date(2010, 1, 1), dt.date(2020, 12, 31))
        is None
    )


def test_bounds_within_range_ok():
    assert (
        _check_assignment_within_bounds(
            dt.date(2015, 1, 1),
            dt.date(2019, 12, 31),
            dt.date(2010, 1, 1),
            dt.date(2020, 12, 31),
        )
        is None
    )


# --- positionless_seat_error (#273) ---


def test_positionless_seat_error_flags_missing_qualifier():
    """A requires_qualifier office with a missing/blank qualifier returns a message."""
    assert positionless_seat_error(True, None) is not None
    assert positionless_seat_error(True, "") is not None
    assert positionless_seat_error(True, "   ") is not None


def test_positionless_seat_error_ok_when_qualifier_present():
    assert positionless_seat_error(True, "Position 1") is None


def test_positionless_seat_error_ignores_non_requiring_office():
    """An office that doesn't require a qualifier is never flagged."""
    assert positionless_seat_error(False, None) is None
    assert positionless_seat_error(False, "Position 1") is None


# --- positioned_at_large_error (#302) ---


def test_positioned_at_large_error_flags_supplied_qualifier():
    """A positionless office given a qualifier is an error — the mirror of #273."""
    assert positioned_at_large_error(True, "Position 1") is not None


def test_positioned_at_large_error_ok_when_qualifier_absent():
    """Absent, empty and whitespace all count as absent — same rule as #273."""
    assert positioned_at_large_error(True, None) is None
    assert positioned_at_large_error(True, "") is None
    assert positioned_at_large_error(True, "   ") is None


def test_positioned_at_large_error_ignores_non_forbidding_office():
    """A positioned or single-seat office is unaffected by the guard."""
    assert positioned_at_large_error(False, "Position 1") is None
    assert positioned_at_large_error(False, None) is None
