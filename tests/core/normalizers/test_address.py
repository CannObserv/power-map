"""Tests for address normalizers."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import usaddress

from src.core.normalizers.address import (
    AddressNormalizerConfig,
    ExternalAddressNormalizer,
    FallbackAddressNormalizer,
    LocalAddressNormalizer,
)

# ---------------------------------------------------------------------------
# LocalAddressNormalizer
# ---------------------------------------------------------------------------

def test_local_null_like_skipped():
    n = LocalAddressNormalizer()
    r = n.normalize(None)
    assert r.skipped is True


def test_local_parses_address():
    n = LocalAddressNormalizer()
    r = n.normalize("123 Main St, Seattle WA 98101")
    assert r.skipped is False
    assert r.value is not None
    assert r.value["raw_input"] == "123 Main St, Seattle WA 98101"
    assert r.confidence_hint == "not_attempted"
    assert r.validation_detail == {"provider": "usaddress", "status": "not_attempted"}


def test_local_ambiguous_stores_raw_only():
    """usaddress.RepeatedLabelError → raw_input stored with warning, no crash."""
    n = LocalAddressNormalizer()
    with patch("usaddress.tag", side_effect=usaddress.RepeatedLabelError("123", {}, [])):
        r = n.normalize("123 Main 456 Oak St")
    assert r.value["raw_input"] == "123 Main 456 Oak St"
    assert r.confidence_hint == "not_attempted"
    assert r.validation_detail == {"provider": "usaddress", "status": "not_attempted"}
    assert any("ambiguous" in w for w in r.warnings)


# ---------------------------------------------------------------------------
# ExternalAddressNormalizer
# ---------------------------------------------------------------------------

@pytest.fixture
def config():
    return AddressNormalizerConfig(api_key="test-key", run_validation=False)


@pytest.fixture
def config_with_validation():
    return AddressNormalizerConfig(api_key="test-key", run_validation=True)


@pytest.fixture
def external(config):
    return ExternalAddressNormalizer(config)


@pytest.fixture
def external_validate(config_with_validation):
    return ExternalAddressNormalizer(config_with_validation)


async def test_external_null_like_skipped(external):
    r = await external.normalize(None)
    assert r.skipped is True


async def test_external_standardize_success(external):
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "address_line_1": "123 MAIN ST",
        "address_line_2": None,
        "city": "SEATTLE",
        "region": "WA",
        "postal_code": "98101",
        "country": "US",
        "standardized": "123 MAIN ST SEATTLE WA 98101",
        "components": {},
        "warnings": [],
    }
    with patch("httpx.AsyncClient") as MockClient:
        MockClient.return_value.__aenter__ = AsyncMock(return_value=MockClient.return_value)
        MockClient.return_value.__aexit__ = AsyncMock(return_value=False)
        MockClient.return_value.post = AsyncMock(return_value=mock_response)
        r = await external.normalize("123 Main St, Seattle WA 98101")
    assert r.value["standardized"] == "123 MAIN ST SEATTLE WA 98101"
    assert r.validation_detail["provider"] == "address-validator"


async def test_external_validate_endpoint_used_when_configured(external_validate):
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "address_line_1": "123 MAIN ST",
        "address_line_2": None,
        "city": "SEATTLE",
        "region": "WA",
        "postal_code": "98101",
        "country": "US",
        "validated": "123 MAIN ST  SEATTLE WA 98101",
        "components": {},
        "warnings": [],
        "validation": {
            "status": "confirmed",
            "dpv_match_code": "Y",
            "provider": "usps",
        },
    }
    with patch("httpx.AsyncClient") as MockClient:
        MockClient.return_value.__aenter__ = AsyncMock(return_value=MockClient.return_value)
        MockClient.return_value.__aexit__ = AsyncMock(return_value=False)
        MockClient.return_value.post = AsyncMock(return_value=mock_response)
        r = await external_validate.normalize("123 Main St, Seattle WA 98101")
    called_url = MockClient.return_value.post.call_args[0][0]
    assert "/validate" in called_url
    assert r.confidence_hint == "confirmed"


async def test_external_429_retries_then_raises(external):
    mock_response = MagicMock()
    mock_response.status_code = 429
    mock_response.headers = {"Retry-After": "0"}  # 0s for fast tests
    with patch("httpx.AsyncClient") as MockClient:
        MockClient.return_value.__aenter__ = AsyncMock(return_value=MockClient.return_value)
        MockClient.return_value.__aexit__ = AsyncMock(return_value=False)
        MockClient.return_value.post = AsyncMock(return_value=mock_response)
        with pytest.raises(RuntimeError, match="rate limit"):
            await external.normalize("123 Main St, Seattle WA 98101")


# ---------------------------------------------------------------------------
# FallbackAddressNormalizer
# ---------------------------------------------------------------------------

async def test_fallback_uses_external_on_success(config):
    n = FallbackAddressNormalizer(config)
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "address_line_1": "123 MAIN ST",
        "address_line_2": None,
        "city": "SEATTLE",
        "region": "WA",
        "postal_code": "98101",
        "country": "US",
        "standardized": "123 MAIN ST SEATTLE WA 98101",
        "components": {},
        "warnings": [],
    }
    with patch("httpx.AsyncClient") as MockClient:
        MockClient.return_value.__aenter__ = AsyncMock(return_value=MockClient.return_value)
        MockClient.return_value.__aexit__ = AsyncMock(return_value=False)
        MockClient.return_value.post = AsyncMock(return_value=mock_response)
        r = await n.normalize("123 Main St, Seattle WA 98101")
    assert r.validation_detail["provider"] == "address-validator"


async def test_fallback_uses_local_on_service_error(config):
    n = FallbackAddressNormalizer(config)
    with patch("httpx.AsyncClient") as MockClient:
        MockClient.return_value.__aenter__ = AsyncMock(return_value=MockClient.return_value)
        MockClient.return_value.__aexit__ = AsyncMock(return_value=False)
        MockClient.return_value.post = AsyncMock(side_effect=Exception("timeout"))
        r = await n.normalize("123 Main St, Seattle WA 98101")
    assert r.validation_detail["provider"] == "usaddress"
    assert "fallback" in r.warnings[0].lower()


async def test_external_standardize_captures_components(external):
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "address_line_1": "123 MAIN ST",
        "address_line_2": "",
        "city": "SEATTLE",
        "region": "WA",
        "postal_code": "98101",
        "country": "US",
        "standardized": "123 MAIN ST SEATTLE WA 98101",
        "components": {
            "spec": "usps-pub28",
            "spec_version": "unknown",
            "values": {"AddressNumber": "123", "StreetName": "MAIN"},
        },
        "warnings": [],
    }
    with patch("httpx.AsyncClient") as MockClient:
        MockClient.return_value.__aenter__ = AsyncMock(return_value=MockClient.return_value)
        MockClient.return_value.__aexit__ = AsyncMock(return_value=False)
        MockClient.return_value.post = AsyncMock(return_value=mock_response)
        r = await external.normalize("123 Main St Seattle WA")
    assert r.value["components"] == {
        "spec": "usps-pub28",
        "spec_version": "unknown",
        "values": {"AddressNumber": "123", "StreetName": "MAIN"},
    }
    assert r.value["latitude"] is None
    assert r.value["longitude"] is None


async def test_external_validate_captures_lat_lng_and_components(external_validate):
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "address_line_1": "123 MAIN ST",
        "address_line_2": None,
        "city": "SEATTLE",
        "region": "WA",
        "postal_code": "98101-1234",
        "country": "US",
        "validated": "123 MAIN ST  SEATTLE WA 98101-1234",
        "components": {"spec": "usps-pub28", "spec_version": "unknown", "values": {}},
        "latitude": 47.6062,
        "longitude": -122.3321,
        "warnings": [],
        "validation": {"status": "confirmed", "dpv_match_code": "Y", "provider": "usps"},
    }
    with patch("httpx.AsyncClient") as MockClient:
        MockClient.return_value.__aenter__ = AsyncMock(return_value=MockClient.return_value)
        MockClient.return_value.__aexit__ = AsyncMock(return_value=False)
        MockClient.return_value.post = AsyncMock(return_value=mock_response)
        r = await external_validate.normalize("123 Main St Seattle WA")
    assert r.value["latitude"] == 47.6062
    assert r.value["longitude"] == -122.3321
    assert r.value["components"] == {"spec": "usps-pub28", "spec_version": "unknown", "values": {}}
