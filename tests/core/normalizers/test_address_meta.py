# tests/core/normalizers/test_address_meta.py
"""Unit tests for country format metadata cache."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.normalizers.address_meta import (
    US_DEFAULT_FORMAT,
    get_country_format,
    invalidate_country_format_cache,
)


@pytest.fixture(autouse=True)
def clear_cache():
    invalidate_country_format_cache()
    yield
    invalidate_country_format_cache()


async def test_get_country_format_returns_format_from_service():
    mock_format = {
        "country": "CA",
        "fields": [
            {"key": "address_line_1", "label": "Address line 1", "required": True},
            {"key": "region", "label": "Province", "required": True},
        ],
    }
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = mock_format
    mock_response.raise_for_status = MagicMock()

    with patch("httpx.AsyncClient") as MockClient:
        MockClient.return_value.__aenter__ = AsyncMock(return_value=MockClient.return_value)
        MockClient.return_value.__aexit__ = AsyncMock(return_value=False)
        MockClient.return_value.get = AsyncMock(return_value=mock_response)
        result = await get_country_format("CA")

    assert result["country"] == "CA"
    assert result["fields"][1]["label"] == "Province"


async def test_get_country_format_caches_result():
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"country": "GB", "fields": []}
    mock_response.raise_for_status = MagicMock()

    with patch("httpx.AsyncClient") as MockClient:
        MockClient.return_value.__aenter__ = AsyncMock(return_value=MockClient.return_value)
        MockClient.return_value.__aexit__ = AsyncMock(return_value=False)
        MockClient.return_value.get = AsyncMock(return_value=mock_response)
        await get_country_format("GB")
        await get_country_format("GB")
        assert MockClient.return_value.get.call_count == 1


async def test_get_country_format_falls_back_to_us_default_on_error():
    with patch("httpx.AsyncClient") as MockClient:
        MockClient.return_value.__aenter__ = AsyncMock(return_value=MockClient.return_value)
        MockClient.return_value.__aexit__ = AsyncMock(return_value=False)
        MockClient.return_value.get = AsyncMock(side_effect=Exception("timeout"))
        result = await get_country_format("XX")

    assert result == US_DEFAULT_FORMAT


async def test_get_country_format_us_uses_default_without_network_call():
    """US format returned from constant; no HTTP call needed."""
    with patch("httpx.AsyncClient") as MockClient:
        result = await get_country_format("US")
    MockClient.assert_not_called()
    assert result == US_DEFAULT_FORMAT


async def test_invalidate_cache_causes_re_fetch():
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"country": "DE", "fields": []}
    mock_response.raise_for_status = MagicMock()

    with patch("httpx.AsyncClient") as MockClient:
        MockClient.return_value.__aenter__ = AsyncMock(return_value=MockClient.return_value)
        MockClient.return_value.__aexit__ = AsyncMock(return_value=False)
        MockClient.return_value.get = AsyncMock(return_value=mock_response)
        await get_country_format("DE")
        invalidate_country_format_cache()
        await get_country_format("DE")
        assert MockClient.return_value.get.call_count == 2
