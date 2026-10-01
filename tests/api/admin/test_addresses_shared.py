"""Unit tests for shared address validity-window and field-context helpers."""

import dataclasses
import re
from datetime import date
from unittest.mock import AsyncMock, patch

import pytest

from src.api.admin._addresses_shared import (
    DATE_FORMAT_ERROR,
    VALIDITY_ORDER_ERROR,
    AddressEchoParams,
    fallback_notice,
    field_context,
    parse_validity,
    same_address,
    saved_flash_body,
    saved_flash_key,
)
from src.api.admin.deps import SHARED_FLASH_MESSAGES
from src.core.normalizers.base import NormalizationResult


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


def test_address_echo_params_is_frozen():
    """#258 CR round 2: read-only echo bundle — mutation raises."""
    p = AddressEchoParams(city="Olympia")
    with pytest.raises(dataclasses.FrozenInstanceError):
        p.city = "Tacoma"


# ---------------------------------------------------------------------------
# Fallback notice (#589)
# ---------------------------------------------------------------------------


def _result(detail):
    return NormalizationResult(value={"standardized": None}, validation_detail=detail)


@pytest.mark.parametrize(
    ("reason", "expected"),
    [
        ("unavailable", "Not standardized: the address service is unavailable."),
        ("rejected", "Not standardized: the address service couldn't read it."),
    ],
)
def test_fallback_notice_names_the_reason(reason, expected):
    detail = {"provider": "usaddress", "status": "not_attempted", "fallback": reason}
    assert fallback_notice(_result(detail)) == expected


@pytest.mark.parametrize(
    "detail",
    [
        None,
        {"provider": "usaddress", "status": "not_attempted"},  # config=None: deliberate
        {"provider": "google", "status": "not_found"},  # the validator answered
        {"provider": "usaddress", "fallback": "something-new"},  # unknown reason
    ],
    ids=["no-detail", "local-only", "validator-answered", "unknown-reason"],
)
def test_fallback_notice_is_none_without_a_known_fallback(detail):
    assert fallback_notice(_result(detail)) is None


def test_saved_flash_body_appends_the_notice():
    assert saved_flash_body("Address added.", None) == "Address added."
    assert saved_flash_body("Address saved.", "Not standardized.") == (
        "Address saved. Not standardized."
    )


def test_saved_flash_key_selects_a_registered_success_key():
    """A save that happened is a success (#353); the key only changes the body."""
    assert saved_flash_key(None) == "saved"
    key = saved_flash_key("Not standardized.")
    assert key == "saved_unstandardized"
    level, body = SHARED_FLASH_MESSAGES[key]
    assert level == "success"
    assert "not standardized" in body.lower()


_STORED = {
    "address_line_1": "1 OLD RD",
    "address_line_2": None,
    "city": "OLYMPIA",
    "region": "WA",
    "postal_code": "98501",
    "country": "US",
}


def test_same_address_matches_identical_columns():
    assert same_address(_STORED, "1 OLD RD", None, "OLYMPIA", "WA", "98501", "US")


@pytest.mark.parametrize(
    "changed",
    [
        ("2 OLD RD", None, "OLYMPIA", "WA", "98501", "US"),
        ("1 OLD RD", "STE 4", "OLYMPIA", "WA", "98501", "US"),
        ("1 OLD RD", None, "LACEY", "WA", "98501", "US"),
        ("1 OLD RD", None, "OLYMPIA", "OR", "98501", "US"),
        ("1 OLD RD", None, "OLYMPIA", "WA", "98502", "US"),
        ("1 OLD RD", None, "OLYMPIA", "WA", "98501", "CA"),
        ("1 Old Rd", None, "OLYMPIA", "WA", "98501", "US"),
    ],
    ids=["line1", "line2", "city", "region", "postal", "country", "case"],
)
def test_same_address_detects_any_column_change(changed):
    """Exact match only: a case change is a change, and might standardize differently."""
    assert not same_address(_STORED, *changed)
