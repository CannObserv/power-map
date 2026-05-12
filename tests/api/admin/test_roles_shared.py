"""Unit tests for roles_shared helpers."""

import datetime as dt

from src.api.admin.roles_shared import _check_assignment_within_bounds


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
