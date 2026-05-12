"""Tests for address normalizers."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import usaddress

from src.core.normalizers.address import (
    AddressNormalizerConfig,
    ExternalAddressNormalizer,
    FallbackAddressNormalizer,
    LocalAddressNormalizer,
    _reset_normalizer,
    get_address_normalizer,
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


# ---------------------------------------------------------------------------
# country param
# ---------------------------------------------------------------------------


def test_local_non_us_stores_raw_only():
    """Non-US country: no usaddress parsing, raw_input stored, country preserved."""
    n = LocalAddressNormalizer()
    r = n.normalize("10 Downing St, London SW1A 2AA", country="GB")
    assert r.skipped is False
    assert r.value["raw_input"] == "10 Downing St, London SW1A 2AA"
    assert r.value["country"] == "GB"
    assert r.value.get("address_line_1") is None
    assert r.value.get("city") is None
    assert r.confidence_hint == "not_attempted"


def test_local_us_parses_normally():
    n = LocalAddressNormalizer()
    r = n.normalize("123 Main St, Seattle WA 98101", country="US")
    assert r.value["country"] == "US"
    assert r.value.get("city") is not None


async def test_external_passes_country_in_payload():
    config = AddressNormalizerConfig(api_key="test-key")
    n = ExternalAddressNormalizer(config)
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "address_line_1": "10 DOWNING ST",
        "city": "LONDON",
        "region": None,
        "postal_code": "SW1A 2AA",
        "country": "GB",
        "standardized": "10 DOWNING ST LONDON SW1A 2AA",
        "components": None,
        "warnings": [],
    }
    with patch("httpx.AsyncClient") as MockClient:
        MockClient.return_value.__aenter__ = AsyncMock(return_value=MockClient.return_value)
        MockClient.return_value.__aexit__ = AsyncMock(return_value=False)
        MockClient.return_value.post = AsyncMock(return_value=mock_response)
        await n.normalize("10 Downing St, London", country="GB")
    payload = MockClient.return_value.post.call_args[1]["json"]
    assert payload["country"] == "GB"


async def test_fallback_forwards_country_to_external(config):
    n = FallbackAddressNormalizer(config)
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "address_line_1": "1 INFINITE LOOP",
        "city": "CUPERTINO",
        "region": "CA",
        "postal_code": "95014",
        "country": "US",
        "standardized": "1 INFINITE LOOP CUPERTINO CA 95014",
        "components": None,
        "warnings": [],
    }
    with patch("httpx.AsyncClient") as MockClient:
        MockClient.return_value.__aenter__ = AsyncMock(return_value=MockClient.return_value)
        MockClient.return_value.__aexit__ = AsyncMock(return_value=False)
        MockClient.return_value.post = AsyncMock(return_value=mock_response)
        await n.normalize("1 Infinite Loop, Cupertino CA", country="US")
    payload = MockClient.return_value.post.call_args[1]["json"]
    assert payload["country"] == "US"


async def test_fallback_non_us_falls_back_to_local_raw_only(config):
    """On service error, non-US falls back to local which stores raw only."""
    n = FallbackAddressNormalizer(config)
    with patch("httpx.AsyncClient") as MockClient:
        MockClient.return_value.__aenter__ = AsyncMock(return_value=MockClient.return_value)
        MockClient.return_value.__aexit__ = AsyncMock(return_value=False)
        MockClient.return_value.post = AsyncMock(side_effect=Exception("timeout"))
        r = await n.normalize("10 Downing St, London SW1A 2AA", country="GB")
    assert r.value["country"] == "GB"
    assert r.value.get("city") is None  # local doesn't parse non-US


# ---------------------------------------------------------------------------
# get_address_normalizer singleton
# ---------------------------------------------------------------------------


def test_get_address_normalizer_returns_fallback_instance():
    """get_address_normalizer returns a FallbackAddressNormalizer."""
    _reset_normalizer()
    n = get_address_normalizer()
    assert isinstance(n, FallbackAddressNormalizer)


def test_get_address_normalizer_returns_same_instance():
    """Repeated calls return the same cached instance."""
    _reset_normalizer()
    n1 = get_address_normalizer()
    n2 = get_address_normalizer()
    assert n1 is n2


def test_get_address_normalizer_without_api_key_has_no_config():
    """Without ADDRESS_VALIDATOR_API_KEY, config is None (local-only normalizer)."""
    _reset_normalizer()
    with patch.dict("os.environ", {}, clear=True):
        # Ensure key absent
        import os

        os.environ.pop("ADDRESS_VALIDATOR_API_KEY", None)
        n = get_address_normalizer()
    assert n.config is None


def test_get_address_normalizer_with_api_key_sets_config():
    """With ADDRESS_VALIDATOR_API_KEY set, config is populated."""
    _reset_normalizer()
    with patch.dict("os.environ", {"ADDRESS_VALIDATOR_API_KEY": "test-key-123"}, clear=False):
        n = get_address_normalizer()
    assert n.config is not None
    assert n.config.api_key == "test-key-123"
    assert n.config.run_validation is False


def test_get_address_normalizer_with_run_validation_true():
    """ADDRESS_VALIDATOR_RUN_VALIDATION=true sets run_validation=True."""
    _reset_normalizer()
    with patch.dict(
        "os.environ",
        {"ADDRESS_VALIDATOR_API_KEY": "key", "ADDRESS_VALIDATOR_RUN_VALIDATION": "true"},
        clear=False,
    ):
        n = get_address_normalizer()
    assert n.config is not None
    assert n.config.run_validation is True


def test_reset_normalizer_clears_cache():
    """_reset_normalizer allows re-initialization on next call."""
    _reset_normalizer()
    with patch.dict("os.environ", {"ADDRESS_VALIDATOR_API_KEY": "key-a"}, clear=False):
        n1 = get_address_normalizer()
    _reset_normalizer()
    with patch.dict("os.environ", {"ADDRESS_VALIDATOR_API_KEY": "key-b"}, clear=False):
        n2 = get_address_normalizer()
    assert n1 is not n2
    assert n1.config.api_key == "key-a"
    assert n2.config.api_key == "key-b"
