"""Address normalizers: local (usaddress), external (address-validator API), and fallback."""

import asyncio
import logging
import math
import os
from dataclasses import dataclass, field
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

import httpx
import usaddress

from src.core.logging import get_logger
from src.core.normalizers.base import NormalizationResult, is_null_like

logger = get_logger(__name__)

# Declared rather than httpx's implicit 5 s: the admin address form blocks on this
# call. /validate may wait on USPS or Google upstream, so reads keep 5 s; a connect
# through the exe.dev proxy is near-instant when the service is up (#589).
_REQUEST_TIMEOUT = httpx.Timeout(5.0, connect=2.0)

# Failures that are fast and usually heal within a second: the service restarting
# behind the proxy. Timeouts are excluded (a retry doubles an already-slow wait), and
# so is 500 (address-validator#239 turns its warm-up 500 into a 503).
_TRANSIENT_ERRORS = (httpx.ConnectError, httpx.RemoteProtocolError)
_TRANSIENT_STATUSES = frozenset({502, 503, 504})

# address-validator answers 429 only once every upstream provider is exhausted, with an
# integer Retry-After of 0-1 s in practice; nothing limits per API key. The default cap
# is for the shared normalizer, which the admin form and public-API writes block on; a
# longer Retry-After falls back at once rather than retry early into another 429 (#597).
_DEFAULT_RETRY_AFTER_CAP = 2.0

# The wait for a 429 whose Retry-After is missing or unreadable.
_DEFAULT_RETRY_AFTER = 1.0

# HTTP statuses meaning the validator read the input and refused it; a later retry
# fails the same way. Anything else that reaches the fallback is "unavailable".
_REJECTED_STATUSES = frozenset({400, 422})

# The ErrorResponse code for a country the validator doesn't cover (only US and CA
# take raw strings). A capability limit, not unreadable input: reason "unsupported".
_UNSUPPORTED_ERROR = "country_not_supported"

# ValidationResult.status → field_confidence.validation_status
_STATUS_MAP = {
    "confirmed": "confirmed",
    "confirmed_missing_secondary": "confirmed",
    "confirmed_bad_secondary": "confirmed",
    "not_confirmed": "failed",
    "not_found": "failed",
    "invalid": "failed",
    "error": "failed",
    "unavailable": "not_attempted",
}


@dataclass
class AddressNormalizerConfig:
    """Configuration for the external address normalizer.

    Args:
        api_key: Value for the X-API-Key header.
        base_url: Base URL of the address-validator service.
        run_validation: If True, call /validate (includes standardization).
                        If False, call /standardize only.
        max_retries: Max 429 retry attempts before giving up.
        retry_after_cap: Longest Retry-After, in seconds, one 429 may wait out;
                         a longer one gives up at once. Short by default for
                         interactive callers; the CSV import raises it.
        transient_retries: Max retries on a connect error, dropped connection,
                           or 502/503/504 before giving up.
        transient_backoff: Seconds before the first transient retry; the
                           Nth retry waits N times this.
    """

    api_key: str
    base_url: str = "https://address-validator.exe.xyz:8000"
    run_validation: bool = False
    max_retries: int = 3
    retry_after_cap: float = _DEFAULT_RETRY_AFTER_CAP
    transient_retries: int = 2
    transient_backoff: float = 0.5


@dataclass
class LocalAddressNormalizer:
    """Parses addresses locally using usaddress. Never calls external services.

    Always produces validation_status='not_attempted'.
    """

    def normalize(self, raw: str | None, country: str = "US") -> NormalizationResult:
        """Parse *raw* into address components. Skips null-like input."""
        if is_null_like(raw):
            return NormalizationResult(value=None, skipped=True)
        raw = raw.strip()
        result: dict = {"raw_input": raw, "country": country}
        if country.upper() != "US":
            return NormalizationResult(
                value=result,
                confidence_hint="not_attempted",
                validation_detail={"provider": "usaddress", "status": "not_attempted"},
            )
        try:
            tagged, _ = usaddress.tag(raw)
            result.update(
                {
                    "address_line_1": _build_line1(tagged),
                    "address_line_2": _build_line2(tagged) if tagged.get("OccupancyType") else None,
                    "city": tagged.get("PlaceName"),
                    "region": tagged.get("StateName"),
                    "postal_code": tagged.get("ZipCode"),
                    "standardized": None,
                }
            )
        except usaddress.RepeatedLabelError:
            return NormalizationResult(
                value=result,
                confidence_hint="not_attempted",
                warnings=["address parse ambiguous; stored raw_input only"],
                validation_detail={"provider": "usaddress", "status": "not_attempted"},
            )
        return NormalizationResult(
            value=result,
            confidence_hint="not_attempted",
            validation_detail={"provider": "usaddress", "status": "not_attempted"},
        )


def _build_line1(tagged: dict) -> str | None:
    """Build address line 1 from usaddress tagged components."""
    parts = [
        tagged.get("AddressNumber"),
        tagged.get("StreetNamePreDirectional"),
        tagged.get("StreetName"),
        tagged.get("StreetNamePostType"),
        tagged.get("StreetNamePostDirectional"),
    ]
    line = " ".join(p for p in parts if p)
    return line or None


def _build_line2(tagged: dict) -> str | None:
    """Build address line 2 from usaddress tagged components."""
    parts = [tagged.get("OccupancyType"), tagged.get("OccupancyIdentifier")]
    line = " ".join(p for p in parts if p)
    return line or None


@dataclass
class ExternalAddressNormalizer:
    """Calls the address-validator API to standardize or validate addresses.

    Endpoint selection:
      - config.run_validation=False → POST /api/v1/standardize
      - config.run_validation=True  → POST /api/v1/validate (includes standardization)

    429 handling (#597): waits out Retry-After (seconds or an HTTP-date) and retries
    up to config.max_retries times. Raises RuntimeError once that budget is spent,
    or at once when Retry-After exceeds config.retry_after_cap.

    Transient handling (#589): a connect error, dropped connection, or 502/503/504
    is retried up to config.transient_retries times with linear backoff; the last
    failure is then raised for the caller's fallback. Timeouts, 500, and other
    4xx are raised at once.
    """

    config: AddressNormalizerConfig

    async def normalize(self, raw: str | None, country: str = "US") -> NormalizationResult:
        """Standardize or validate *raw* via the external API."""
        if is_null_like(raw):
            return NormalizationResult(value=None, skipped=True)
        raw = raw.strip()
        endpoint = "validate" if self.config.run_validation else "standardize"
        url = f"{self.config.base_url}/api/v2/{endpoint}"
        payload = {"address": raw, "country": country}
        headers = {"X-API-Key": self.config.api_key}

        rate_limited = 0
        transient = 0
        async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT) as client:
            while True:
                try:
                    response = await client.post(url, json=payload, headers=headers)
                except _TRANSIENT_ERRORS:
                    if transient >= self.config.transient_retries:
                        raise
                    transient += 1
                    await asyncio.sleep(self.config.transient_backoff * transient)
                    continue
                if response.status_code == 429:
                    if rate_limited >= self.config.max_retries:
                        raise RuntimeError(
                            "address-validator rate limit: exhausted "
                            f"{self.config.max_retries} retries"
                        )
                    wait = _retry_after_seconds(response.headers.get("Retry-After"))
                    if wait > self.config.retry_after_cap:
                        raise RuntimeError(
                            f"address-validator rate limit: Retry-After {wait:g}s exceeds "
                            f"the {self.config.retry_after_cap:g}s cap"
                        )
                    rate_limited += 1
                    await asyncio.sleep(wait)
                    continue
                if (
                    response.status_code in _TRANSIENT_STATUSES
                    and transient < self.config.transient_retries
                ):
                    transient += 1
                    await asyncio.sleep(self.config.transient_backoff * transient)
                    continue
                response.raise_for_status()
                data = response.json()
                return self._parse_response(raw, data)

    def _parse_response(self, raw: str, data: dict) -> NormalizationResult:
        """Parse API response into a NormalizationResult."""
        result = {
            "raw_input": raw,
            "address_line_1": data.get("address_line_1"),
            "address_line_2": data.get("address_line_2"),
            "city": data.get("city"),
            "region": data.get("region"),
            "postal_code": data.get("postal_code"),
            "country": data.get("country", "US"),
            "standardized": data.get("standardized") or data.get("validated"),
            "latitude": data.get("latitude"),
            "longitude": data.get("longitude"),
            "components": data.get("components"),
        }
        detail: dict = {"provider": "address-validator"}
        confidence_hint = "unconfirmed"
        if self.config.run_validation and "validation" in data:
            v = data["validation"]
            detail.update(
                {
                    "status": v.get("status"),
                    "dpv_match_code": v.get("dpv_match_code"),
                    "provider": v.get("provider", "address-validator"),
                }
            )
            confidence_hint = _STATUS_MAP.get(v.get("status", ""), "not_attempted")
        detail["warnings"] = data.get("warnings", [])
        warnings = [f"address-validator warning: {w}" for w in data.get("warnings", [])]
        return NormalizationResult(
            value=result,
            warnings=warnings,
            confidence_hint=confidence_hint,
            validation_detail=detail,
        )


def _retry_after_seconds(header: str | None) -> float:
    """Seconds a 429's ``Retry-After`` asks for: delay-seconds or an HTTP-date (RFC 9110).

    address-validator sends integer seconds. A missing or unreadable header, or a
    negative or non-finite number, waits the default rather than falling back (#597).
    """
    if header is None:
        return _DEFAULT_RETRY_AFTER
    try:
        seconds = float(header)
    except ValueError:
        return _http_date_delay(header)
    return seconds if math.isfinite(seconds) and seconds >= 0 else _DEFAULT_RETRY_AFTER


def _http_date_delay(header: str) -> float:
    """Seconds from now until the HTTP-date *header*; 0 if past, the default if unparseable."""
    try:
        when = parsedate_to_datetime(header)
    except (TypeError, ValueError):
        return _DEFAULT_RETRY_AFTER
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    return max(0.0, (when - datetime.now(UTC)).total_seconds())


def _error_code(response: httpx.Response) -> str | None:
    """The ``error`` code from an address-validator ErrorResponse body, if it has one."""
    try:
        return response.json().get("error")
    except (ValueError, AttributeError):
        return None


def _fallback_reason(exc: Exception) -> str:
    """Classify why the external normalizer failed.

    ``unsupported`` — 422 ``country_not_supported``: the validator doesn't cover the
    country. ``rejected`` — any other 400/422: it refused the input. Retrying either
    won't help. ``unavailable`` — anything else: an outage, a timeout, 5xx, 429
    exhaustion, or a bad API key; worth re-standardizing once the service is back (#595).
    """
    if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code in _REJECTED_STATUSES:
        if _error_code(exc.response) == _UNSUPPORTED_ERROR:
            return "unsupported"
        return "rejected"
    return "unavailable"


@dataclass
class FallbackAddressNormalizer:
    """Tries ExternalAddressNormalizer; falls back to LocalAddressNormalizer on any error.

    Use this in production pipelines. Pass config=None to always use local.

    A fallback marks ``validation_detail["fallback"]`` with ``_fallback_reason`` so
    callers can tell the curator (#589); a config=None run is deliberate and
    carries no marker.
    """

    config: AddressNormalizerConfig | None = None
    _local: LocalAddressNormalizer = field(default_factory=LocalAddressNormalizer, init=False)

    async def normalize(self, raw: str | None, country: str = "US") -> NormalizationResult:
        """Normalize *raw* via external service, with local fallback."""
        if self.config is None or is_null_like(raw):
            return self._local.normalize(raw, country=country)
        try:
            external = ExternalAddressNormalizer(self.config)
            return await external.normalize(raw, country=country)
        except Exception as exc:
            reason = _fallback_reason(exc)
            status = exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else None
            # Exception type and status only: the address is PII and stays out of logs.
            # An unsupported country is expected, not an incident: INFO, not WARNING.
            logger.log(
                logging.INFO if reason == "unsupported" else logging.WARNING,
                "address-validator %s (%s, status=%s); fell back to local parser",
                reason,
                type(exc).__name__,
                status,
            )
            result = self._local.normalize(raw, country=country)
            result.validation_detail["fallback"] = reason
            result.warnings.insert(0, f"fallback to local address parser: {exc}")
            return result


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_normalizer: FallbackAddressNormalizer | None = None


def get_address_normalizer() -> FallbackAddressNormalizer:
    """Return a shared FallbackAddressNormalizer, initializing lazily on first call.

    Reads ADDRESS_VALIDATOR_API_KEY, ADDRESS_VALIDATOR_RUN_VALIDATION, and
    ADDRESS_VALIDATOR_BASE_URL (trailing slash tolerated) from the environment
    on first call; result is cached for the lifetime of the process.
    Call _reset_normalizer() in tests to clear the cache.
    """
    global _normalizer
    if _normalizer is None:
        api_key = os.environ.get("ADDRESS_VALIDATOR_API_KEY")
        run_validation = os.environ.get("ADDRESS_VALIDATOR_RUN_VALIDATION", "").lower() == "true"
        if api_key:
            config = AddressNormalizerConfig(api_key=api_key, run_validation=run_validation)
            base_url = os.environ.get("ADDRESS_VALIDATOR_BASE_URL", "").rstrip("/")
            if base_url:
                config.base_url = base_url
        else:
            config = None
        _normalizer = FallbackAddressNormalizer(config=config)
    return _normalizer


def _reset_normalizer() -> None:
    """Reset the singleton cache. For use in tests only."""
    global _normalizer
    _normalizer = None
