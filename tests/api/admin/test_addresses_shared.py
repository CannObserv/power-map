"""Unit tests for shared address validity-window helpers."""

from datetime import date

import pytest

from src.api.admin._addresses_shared import (
    DATE_FORMAT_ERROR,
    VALIDITY_ORDER_ERROR,
    parse_validity,
)


def test_blank_fields_are_open_ended():
    assert parse_validity("", "") == (None, None)
    assert parse_validity("  ", "  ") == (None, None)


def test_parses_iso_dates():
    assert parse_validity("2024-01-01", "2025-06-30") == (date(2024, 1, 1), date(2025, 6, 30))


def test_one_sided_windows():
    assert parse_validity("2024-01-01", "") == (date(2024, 1, 1), None)
    assert parse_validity("", "2025-06-30") == (None, date(2025, 6, 30))


def test_inverted_range_raises_order_error():
    with pytest.raises(ValueError, match=VALIDITY_ORDER_ERROR):
        parse_validity("2025-06-30", "2024-01-01")


@pytest.mark.parametrize("bad", ["not-a-date", "2024-13-45", "01/02/2024"])
def test_malformed_date_raises_format_error(bad):
    with pytest.raises(ValueError, match=DATE_FORMAT_ERROR):
        parse_validity(bad, "")
