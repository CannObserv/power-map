# tests/core/normalizers/test_address_meta.py
"""Unit tests for country format metadata cache."""

from unittest.mock import MagicMock

import pytest

from src.core.normalizers.address_meta import (
    US_DEFAULT_FORMAT,
    get_country_format,
    invalidate_country_format_cache,
)
from tests.core.normalizers.http_mock import mock_http_client


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

    with mock_http_client(mock_response, method="get"):
        result = await get_country_format("CA")

    assert result["country"] == "CA"
    assert result["fields"][1]["label"] == "Province"


async def test_get_country_format_caches_result():
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"country": "GB", "fields": []}
    mock_response.raise_for_status = MagicMock()

    with mock_http_client(mock_response, method="get") as MockClient:
        await get_country_format("GB")
        await get_country_format("GB")
        assert MockClient.return_value.get.call_count == 1


async def test_get_country_format_falls_back_to_us_default_on_error():
    with mock_http_client(method="get", side_effect=Exception("timeout")):
        result = await get_country_format("XX")

    assert result == US_DEFAULT_FORMAT


async def test_get_country_format_us_uses_default_without_network_call():
    """US format returned from constant; no HTTP call needed."""
    with mock_http_client(method="get") as MockClient:
        result = await get_country_format("US")
    MockClient.assert_not_called()
    assert result == US_DEFAULT_FORMAT


async def test_get_country_format_uses_v2_path():
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"country": "CA", "fields": []}
    mock_response.raise_for_status = MagicMock()

    with mock_http_client(mock_response, method="get") as MockClient:
        await get_country_format("CA")

    called_url = MockClient.return_value.get.call_args[0][0]
    assert "/api/v2/" in called_url


async def test_get_country_format_strips_trailing_slash_from_base_url(monkeypatch):
    """#257: a trailing slash on the base URL must not produce a double-slash path."""
    monkeypatch.setattr(
        "src.core.normalizers.address_meta._ADDRESS_VALIDATOR_BASE",
        "https://validator.test:8000/",
    )
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"country": "CA", "fields": []}
    mock_response.raise_for_status = MagicMock()

    with mock_http_client(mock_response, method="get") as MockClient:
        await get_country_format("CA")

    called_url = MockClient.return_value.get.call_args[0][0]
    assert called_url == "https://validator.test:8000/api/v2/countries/CA/format"


async def test_get_country_format_follows_redirects():
    """#257: the client must follow redirects (e.g. upstream path normalization)."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"country": "CA", "fields": []}
    mock_response.raise_for_status = MagicMock()

    with mock_http_client(mock_response, method="get") as MockClient:
        await get_country_format("CA")

    assert MockClient.call_args.kwargs.get("follow_redirects") is True


async def test_get_country_format_sets_explicit_timeout():
    """#257 CR: bounded timeout so a validator outage can't block the admin form ~5s."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"country": "CA", "fields": []}
    mock_response.raise_for_status = MagicMock()

    with mock_http_client(mock_response, method="get") as MockClient:
        await get_country_format("CA")

    assert MockClient.call_args.kwargs.get("timeout") == 3.0


async def test_get_country_format_logs_warning_on_fallback(caplog):
    """#257: the US-default fallback must leave a server-side trace."""
    with mock_http_client(method="get", side_effect=Exception("boom")):
        with caplog.at_level("WARNING", logger="src.core.normalizers.address_meta"):
            result = await get_country_format("XX")

    assert result == US_DEFAULT_FORMAT
    assert any(
        "XX" in r.message and "boom" in r.message and r.levelname == "WARNING"
        for r in caplog.records
    )


async def test_invalidate_cache_causes_re_fetch():
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"country": "DE", "fields": []}
    mock_response.raise_for_status = MagicMock()

    with mock_http_client(mock_response, method="get") as MockClient:
        await get_country_format("DE")
        invalidate_country_format_cache()
        await get_country_format("DE")
        assert MockClient.return_value.get.call_count == 2
