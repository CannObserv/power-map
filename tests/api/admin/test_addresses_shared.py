"""Unit tests for shared address validity-window and field-context helpers."""

import re
from datetime import date
from unittest.mock import AsyncMock, patch

import pytest

from src.api.admin._addresses_shared import (
    DATE_FORMAT_ERROR,
    VALIDITY_ORDER_ERROR,
    AddressEchoParams,
    field_context,
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
    with pytest.raises(ValueError, match=re.escape(VALIDITY_ORDER_ERROR)):
        parse_validity("2025-06-30", "2024-01-01")


@pytest.mark.parametrize("bad", ["not-a-date", "2024-13-45", "01/02/2024"])
def test_malformed_date_raises_format_error(bad):
    with pytest.raises(ValueError, match=re.escape(DATE_FORMAT_ERROR)):
        parse_validity(bad, "")


@pytest.mark.parametrize("raw", ["ca", " ca ", "CA", " CA"])
async def test_field_context_normalizes_country_code(raw):
    mock = AsyncMock(return_value={"fields": []})
    with patch("src.api.admin._addresses_shared.get_country_format", new=mock):
        await field_context(raw)
    mock.assert_awaited_once_with("CA")


@pytest.mark.parametrize("blank", ["", "  ", None])
async def test_field_context_blank_country_falls_back_to_us(blank):
    mock = AsyncMock(return_value={"fields": []})
    with patch("src.api.admin._addresses_shared.get_country_format", new=mock):
        await field_context(blank)
    mock.assert_awaited_once_with("US")


async def test_field_context_shapes_labels_and_visibility():
    fmt = {
        "fields": [
            {"key": "city", "label": "Town", "required": True},
            {"key": "postal_code", "label": "Postcode", "required": False},
        ]
    }
    with patch(
        "src.api.admin._addresses_shared.get_country_format", new=AsyncMock(return_value=fmt)
    ):
        ctx = await field_context("GB")
    assert ctx == {
        "field_labels": {"city": "Town", "postal_code": "Postcode"},
        "field_visible": {"city", "postal_code"},
    }


def test_address_echo_params_as_row_maps_fields():
    """#258 CR: as_row() shapes the echo params into the partial's `a` context."""
    p = AddressEchoParams(
        address_line_1="1 A St",
        address_line_2="Apt 2",
        city="Olympia",
        region="WA",
        postal_code="98501",
        addr_id="EA1",
    )
    assert p.as_row() == {
        "id": "EA1",
        "address_line_1": "1 A St",
        "address_line_2": "Apt 2",
        "city": "Olympia",
        "region": "WA",
        "postal_code": "98501",
    }


def test_address_echo_params_blank_addr_id_is_none():
    """#258 CR: a blank addr_id (new row) maps to id=None so ids render row-scoped as -new."""
    assert AddressEchoParams().as_row()["id"] is None
