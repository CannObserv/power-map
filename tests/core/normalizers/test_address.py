"""Tests for address normalizers."""

import os
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
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
from tests.core.normalizers.http_mock import mock_http_client

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
    with mock_http_client(mock_response):
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
    with mock_http_client(mock_response) as MockClient:
        r = await external_validate.normalize("123 Main St, Seattle WA 98101")
    called_url = MockClient.return_value.post.call_args[0][0]
    assert "/validate" in called_url
    assert r.confidence_hint == "confirmed"


async def test_external_429_retries_then_raises(external):
    mock_response = MagicMock()
    mock_response.status_code = 429
    mock_response.headers = {"Retry-After": "0"}  # 0s for fast tests
    with mock_http_client(mock_response):
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
    with mock_http_client(mock_response):
        r = await n.normalize("123 Main St, Seattle WA 98101")
    assert r.validation_detail["provider"] == "address-validator"


async def test_fallback_uses_local_on_service_error(config):
    n = FallbackAddressNormalizer(config)
    with mock_http_client(side_effect=Exception("timeout")):
        r = await n.normalize("123 Main St, Seattle WA 98101")
    assert r.validation_detail["provider"] == "usaddress"
    assert "fallback" in r.warnings[0].lower()


# ---------------------------------------------------------------------------
# Transient failures: timeout, retry, fallback reason, log (#589)
# ---------------------------------------------------------------------------

_URL = "https://address-validator.exe.xyz:8000/api/v2/standardize"
_RAW = "123 Main St, Seattle WA 98101"


def _ok_response():
    r = MagicMock()
    r.status_code = 200
    r.json.return_value = {
        "address_line_1": "123 MAIN ST",
        "city": "SEATTLE",
        "region": "WA",
        "postal_code": "98101",
        "country": "US",
        "standardized": "123 MAIN ST SEATTLE WA 98101",
        "warnings": [],
    }
    return r


def _status_response(code: int, body: dict | None = None) -> httpx.Response:
    """A real httpx.Response, so raise_for_status() raises HTTPStatusError."""
    return httpx.Response(code, json=body, request=httpx.Request("POST", _URL))


def _error_422(code: str) -> httpx.Response:
    """address-validator's ErrorResponse: {"error": <snake_case code>, "message": ...}."""
    return _status_response(422, {"error": code, "message": "x"})


@pytest.fixture
def no_sleep():
    """Patch the retry backoff sleep; yield the mock for delay assertions."""
    with patch("src.core.normalizers.address.asyncio.sleep", new=AsyncMock()) as m:
        yield m


async def test_external_sets_explicit_timeout(external):
    """httpx's implicit 5 s default is replaced by a declared, short-connect timeout."""
    with mock_http_client(_ok_response()) as MockClient:
        await external.normalize(_RAW)
    assert MockClient.call_args.kwargs.get("timeout") == httpx.Timeout(5.0, connect=2.0)


@pytest.mark.parametrize(
    "first",
    [
        httpx.ConnectError("connection refused"),
        httpx.RemoteProtocolError("server disconnected"),
        _status_response(502),
        _status_response(503),
        _status_response(504),
    ],
    ids=["connect-error", "remote-protocol-error", "502", "503", "504"],
)
async def test_external_retries_transient_failure_then_succeeds(external, no_sleep, first):
    with mock_http_client(side_effect=[first, _ok_response()]) as MockClient:
        r = await external.normalize(_RAW)
    assert r.value["standardized"] == "123 MAIN ST SEATTLE WA 98101"
    assert MockClient.return_value.post.await_count == 2
    no_sleep.assert_awaited_once_with(0.5)


async def test_external_transient_retries_back_off_then_raise(external, no_sleep):
    """Two retries (0.5 s, 1.0 s), then the last error propagates to the fallback."""
    with mock_http_client(side_effect=httpx.ConnectError("refused")) as MockClient:
        with pytest.raises(httpx.ConnectError):
            await external.normalize(_RAW)
    assert MockClient.return_value.post.await_count == 3
    assert [c.args[0] for c in no_sleep.await_args_list] == [0.5, 1.0]


async def test_external_transient_status_exhausted_raises_http_status_error(external, no_sleep):
    with mock_http_client(side_effect=[_status_response(503)] * 3) as MockClient:
        with pytest.raises(httpx.HTTPStatusError):
            await external.normalize(_RAW)
    assert MockClient.return_value.post.await_count == 3


@pytest.mark.parametrize(
    "failure",
    [
        httpx.ReadTimeout("read timed out"),
        httpx.ConnectTimeout("connect timed out"),
        _status_response(500),
        _status_response(422),
        _status_response(400),
    ],
    ids=["read-timeout", "connect-timeout", "500", "422", "400"],
)
async def test_external_does_not_retry_slow_or_permanent_failure(external, no_sleep, failure):
    """A timeout already cost the caller seconds; a 500 or 4xx won't heal on retry."""
    with mock_http_client(side_effect=[failure, _ok_response()]) as MockClient:
        with pytest.raises((httpx.TimeoutException, httpx.HTTPStatusError)):
            await external.normalize(_RAW)
    assert MockClient.return_value.post.await_count == 1
    no_sleep.assert_not_awaited()


@pytest.mark.parametrize(
    ("failure", "reason"),
    [
        (httpx.ConnectError("refused"), "unavailable"),
        (httpx.ReadTimeout("slow"), "unavailable"),
        (_status_response(500), "unavailable"),
        (_status_response(503), "unavailable"),
        (_status_response(401), "unavailable"),
        (RuntimeError("address-validator rate limit: exhausted 3 retries"), "unavailable"),
        (_status_response(400), "rejected"),
        (_status_response(422), "rejected"),
        (_error_422("address_required"), "rejected"),
        (_error_422("country_not_supported"), "unsupported"),
    ],
    ids=[
        "connect",
        "timeout",
        "500",
        "503",
        "401",
        "429-exhausted",
        "400",
        "422-no-body",
        "422-address-required",
        "422-country-not-supported",
    ],
)
async def test_fallback_records_reason(config, no_sleep, failure, reason):
    """The reason is structured, so callers need not parse the warning string."""
    n = FallbackAddressNormalizer(config)
    # A list side_effect returns Responses and raises exceptions, one per attempt.
    with mock_http_client(side_effect=[failure] * 3):
        r = await n.normalize(_RAW)
    assert r.validation_detail["provider"] == "usaddress"
    assert r.validation_detail["fallback"] == reason
    assert r.value["standardized"] is None


async def test_fallback_logs_warning_without_the_address(config, no_sleep, caplog):
    n = FallbackAddressNormalizer(config)
    with mock_http_client(side_effect=[_status_response(503)] * 3):
        with caplog.at_level("WARNING", logger="src.core.normalizers.address"):
            await n.normalize(_RAW)
    records = [r for r in caplog.records if r.name == "src.core.normalizers.address"]
    assert len(records) == 1
    msg = records[0].getMessage()
    assert records[0].levelname == "WARNING"
    assert "unavailable" in msg
    assert "HTTPStatusError" in msg
    assert "503" in msg
    assert "Main St" not in msg
    assert "98101" not in msg


async def test_fallback_logs_unsupported_country_at_info(config, caplog):
    """A country the validator doesn't cover is a known limit, not an incident."""
    n = FallbackAddressNormalizer(config)
    with mock_http_client(side_effect=[_error_422("country_not_supported")]):
        with caplog.at_level("INFO", logger="src.core.normalizers.address"):
            await n.normalize("10 Downing St, London SW1A 2AA", country="GB")
    records = [r for r in caplog.records if r.name == "src.core.normalizers.address"]
    assert [r.levelname for r in records] == ["INFO"]
    assert "unsupported" in records[0].getMessage()


# ---------------------------------------------------------------------------
# 429 Retry-After: per-caller cap, header parsing (#597)
# ---------------------------------------------------------------------------


def _too_many(retry_after: str | None = None) -> httpx.Response:
    """address-validator's 429: every upstream provider is exhausted."""
    headers = {} if retry_after is None else {"Retry-After": retry_after}
    return httpx.Response(429, headers=headers, request=httpx.Request("POST", _URL))


def _http_date(when: datetime) -> str:
    """An RFC 9110 HTTP-date, e.g. ``Wed, 21 Oct 2026 07:28:00 GMT``."""
    return format_datetime(when, usegmt=True)


@pytest.mark.parametrize(
    ("retry_after", "wait"),
    [
        ("0", 0.0),
        ("1", 1.0),
        ("1.5", 1.5),
        (None, 1.0),
        ("soon", 1.0),
        ("-5", 1.0),
        ("nan", 1.0),
        ("inf", 1.0),
        (_http_date(datetime(2000, 1, 1, tzinfo=UTC)), 0.0),
        # RFC 9110's obsolete forms, which a recipient must still accept;
        # asctime carries no zone, so it parses to a naive datetime.
        ("Sunday, 06-Nov-94 08:49:37 GMT", 0.0),
        ("Sun Nov  6 08:49:37 1994", 0.0),
    ],
    ids=[
        "zero",
        "integer",
        "fraction",
        "missing",
        "garbage",
        "negative",
        "nan",
        "inf",
        "past-date",
        "past-rfc850-date",
        "past-asctime-date",
    ],
)
async def test_external_429_waits_retry_after_then_succeeds(external, no_sleep, retry_after, wait):
    """An unreadable header waits the 1 s default instead of sending the call to the fallback."""
    with mock_http_client(side_effect=[_too_many(retry_after), _ok_response()]) as MockClient:
        r = await external.normalize(_RAW)
    assert r.value["standardized"] == "123 MAIN ST SEATTLE WA 98101"
    assert MockClient.return_value.post.await_count == 2
    no_sleep.assert_awaited_once_with(wait)


async def test_external_429_honours_a_future_http_date(no_sleep):
    """RFC 9110 lets Retry-After be a date; it is waited out, not misread as garbage."""
    external = ExternalAddressNormalizer(
        AddressNormalizerConfig(api_key="test-key", retry_after_cap=120.0)
    )
    retry_at = _http_date(datetime.now(UTC) + timedelta(seconds=60))
    with mock_http_client(side_effect=[_too_many(retry_at), _ok_response()]):
        await external.normalize(_RAW)
    (wait,) = no_sleep.await_args.args
    assert 55.0 < wait <= 60.0


@pytest.mark.parametrize(
    "retry_after",
    ["30", _http_date(datetime(2099, 1, 1, tzinfo=UTC))],
    ids=["seconds", "http-date"],
)
async def test_external_429_over_the_cap_gives_up_without_waiting(external, no_sleep, retry_after):
    """The admin form blocks on this call: a long wait falls back now, not after 3x it."""
    with mock_http_client(side_effect=[_too_many(retry_after), _ok_response()]) as MockClient:
        with pytest.raises(RuntimeError, match="rate limit"):
            await external.normalize(_RAW)
    assert MockClient.return_value.post.await_count == 1
    no_sleep.assert_not_awaited()


def test_retry_after_cap_defaults_short_for_interactive_callers():
    """Admin saves and public-API writes share the default; each wait stays under 2 s."""
    assert AddressNormalizerConfig(api_key="k").retry_after_cap == 2.0


async def test_external_429_cap_is_set_per_caller(no_sleep):
    """A bulk caller raises the cap and waits out the server's Retry-After."""
    external = ExternalAddressNormalizer(
        AddressNormalizerConfig(api_key="test-key", retry_after_cap=60.0)
    )
    with mock_http_client(side_effect=[_too_many("30"), _ok_response()]) as MockClient:
        r = await external.normalize(_RAW)
    assert r.value["standardized"] == "123 MAIN ST SEATTLE WA 98101"
    assert MockClient.return_value.post.await_count == 2
    no_sleep.assert_awaited_once_with(30.0)


async def test_external_429_exhausts_max_retries_within_the_cap(external, no_sleep):
    """Waits under the cap still stop after max_retries (3), then the fallback takes over."""
    with mock_http_client(side_effect=[_too_many("1")] * 4) as MockClient:
        with pytest.raises(RuntimeError, match="exhausted 3 retries"):
            await external.normalize(_RAW)
    assert MockClient.return_value.post.await_count == 4
    assert [c.args[0] for c in no_sleep.await_args_list] == [1.0, 1.0, 1.0]


async def test_fallback_429_over_the_cap_records_unavailable(config, no_sleep):
    """A rate-limited save is worth re-standardizing later (#595): reason unavailable."""
    n = FallbackAddressNormalizer(config)
    with mock_http_client(side_effect=[_too_many("30"), _ok_response()]):
        r = await n.normalize(_RAW)
    assert r.validation_detail["fallback"] == "unavailable"
    assert r.value["standardized"] is None
    assert "exceeds the 2s cap" in r.warnings[0]
    no_sleep.assert_not_awaited()


async def test_local_only_normalizer_records_no_fallback():
    """No API key configured is a deliberate local run, not a degraded one."""
    r = await FallbackAddressNormalizer(config=None).normalize(_RAW)
    assert "fallback" not in r.validation_detail


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
    with mock_http_client(mock_response):
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
    with mock_http_client(mock_response):
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
    with mock_http_client(mock_response) as MockClient:
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
    with mock_http_client(mock_response) as MockClient:
        await n.normalize("1 Infinite Loop, Cupertino CA", country="US")
    payload = MockClient.return_value.post.call_args[1]["json"]
    assert payload["country"] == "US"


async def test_fallback_non_us_falls_back_to_local_raw_only(config):
    """On service error, non-US falls back to local which stores raw only."""
    n = FallbackAddressNormalizer(config)
    with mock_http_client(side_effect=Exception("timeout")):
        r = await n.normalize("10 Downing St, London SW1A 2AA", country="GB")
    assert r.value["country"] == "GB"
    assert r.value.get("city") is None  # local doesn't parse non-US


# ---------------------------------------------------------------------------
# v2 API path assertions
# ---------------------------------------------------------------------------


async def test_external_standardize_uses_v2_path(external):
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
    with mock_http_client(mock_response) as MockClient:
        await external.normalize("123 Main St, Seattle WA 98101")
    called_url = MockClient.return_value.post.call_args[0][0]
    assert "/api/v2/standardize" in called_url


async def test_external_validate_uses_v2_path(external_validate):
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "address_line_1": "123 MAIN ST",
        "address_line_2": None,
        "city": "SEATTLE",
        "region": "WA",
        "postal_code": "98101",
        "country": "US",
        "validated": "123 MAIN ST SEATTLE WA 98101",
        "components": {},
        "warnings": [],
        "validation": {"status": "confirmed", "dpv_match_code": "Y", "provider": "usps"},
    }
    with mock_http_client(mock_response) as MockClient:
        await external_validate.normalize("123 Main St, Seattle WA 98101")
    called_url = MockClient.return_value.post.call_args[0][0]
    assert "/api/v2/validate" in called_url


# ---------------------------------------------------------------------------
# v2 _STATUS_MAP — non-US statuses
# ---------------------------------------------------------------------------


async def _validate_with_status(external_validate, status: str):
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "address_line_1": "",
        "address_line_2": None,
        "city": "",
        "region": None,
        "postal_code": "",
        "country": "CA",
        "validated": None,
        "components": None,
        "warnings": [],
        "validation": {"status": status, "provider": "libpostal"},
    }
    with mock_http_client(mock_response):
        return await external_validate.normalize("123 Fake St, Toronto ON", country="CA")


async def test_external_validate_not_found_maps_to_failed(external_validate):
    r = await _validate_with_status(external_validate, "not_found")
    assert r.confidence_hint == "failed"


async def test_external_validate_invalid_maps_to_failed(external_validate):
    r = await _validate_with_status(external_validate, "invalid")
    assert r.confidence_hint == "failed"


async def test_external_validate_error_maps_to_failed(external_validate):
    r = await _validate_with_status(external_validate, "error")
    assert r.confidence_hint == "failed"


# ---------------------------------------------------------------------------
# get_address_normalizer singleton
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _fresh_normalizer():
    """Isolate the get_address_normalizer singleton per test (module-wide autouse)."""
    _reset_normalizer()
    yield
    _reset_normalizer()


def test_get_address_normalizer_returns_fallback_instance():
    """get_address_normalizer returns a FallbackAddressNormalizer."""
    n = get_address_normalizer()
    assert isinstance(n, FallbackAddressNormalizer)


def test_get_address_normalizer_returns_same_instance():
    """Repeated calls return the same cached instance."""
    n1 = get_address_normalizer()
    n2 = get_address_normalizer()
    assert n1 is n2


def test_get_address_normalizer_without_api_key_has_no_config():
    """Without ADDRESS_VALIDATOR_API_KEY, config is None (local-only normalizer)."""
    with patch.dict("os.environ", {}, clear=True):
        n = get_address_normalizer()
    assert n.config is None


def test_get_address_normalizer_with_api_key_sets_config():
    """With ADDRESS_VALIDATOR_API_KEY set, config is populated."""
    with patch.dict("os.environ", {"ADDRESS_VALIDATOR_API_KEY": "test-key-123"}, clear=False):
        n = get_address_normalizer()
    assert n.config is not None
    assert n.config.api_key == "test-key-123"
    assert n.config.run_validation is False


def test_get_address_normalizer_keeps_the_interactive_retry_after_cap():
    """The admin form and public-API writes share this instance: each 429 wait stays short."""
    with patch.dict("os.environ", {"ADDRESS_VALIDATOR_API_KEY": "test-key-123"}, clear=False):
        n = get_address_normalizer()
    assert n.config.retry_after_cap == 2.0


def test_get_address_normalizer_honors_base_url_env_and_strips_slash():
    """#257 CR: ADDRESS_VALIDATOR_BASE_URL re-points standardize/validate too, rstrip'd."""
    with patch.dict(
        "os.environ",
        {
            "ADDRESS_VALIDATOR_API_KEY": "test-key-123",
            "ADDRESS_VALIDATOR_BASE_URL": "https://validator.test:8001/",
        },
        clear=False,
    ):
        n = get_address_normalizer()
    assert n.config.base_url == "https://validator.test:8001"


def test_get_address_normalizer_default_base_url_without_env():
    """Without ADDRESS_VALIDATOR_BASE_URL, the dataclass default stands."""
    with patch.dict("os.environ", {"ADDRESS_VALIDATOR_API_KEY": "test-key-123"}, clear=False):
        os.environ.pop("ADDRESS_VALIDATOR_BASE_URL", None)
        n = get_address_normalizer()
    assert n.config.base_url == "https://address-validator.exe.xyz:8000"


def test_get_address_normalizer_with_run_validation_true():
    """ADDRESS_VALIDATOR_RUN_VALIDATION=true sets run_validation=True."""
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
