"""Address normalizers: local (usaddress), external (address-validator API), and fallback."""

import asyncio
from dataclasses import dataclass, field

import httpx
import usaddress

from src.core.normalizers.base import NormalizationResult, is_null_like

# ValidationResult.status → field_confidence.validation_status
_STATUS_MAP = {
    "confirmed": "confirmed",
    "confirmed_missing_secondary": "confirmed",
    "confirmed_bad_secondary": "confirmed",
    "not_confirmed": "failed",
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
    """

    api_key: str
    base_url: str = "https://address-validator.exe.xyz:8000"
    run_validation: bool = False
    max_retries: int = 3


@dataclass
class LocalAddressNormalizer:
    """Parses addresses locally using usaddress. Never calls external services.

    Always produces validation_status='not_attempted'.
    """

    def normalize(self, raw: str | None) -> NormalizationResult:
        """Parse *raw* into address components. Skips null-like input."""
        if is_null_like(raw):
            return NormalizationResult(value=None, skipped=True)
        raw = raw.strip()
        result: dict = {"raw_input": raw}
        try:
            tagged, _ = usaddress.tag(raw)
            result.update({
                "address_line_1": _build_line1(tagged),
                "address_line_2": tagged.get("OccupancyType") and _build_line2(tagged),
                "city": tagged.get("PlaceName"),
                "region": tagged.get("StateName"),
                "postal_code": tagged.get("ZipCode"),
                "country": "US",
                "standardized": None,
            })
        except usaddress.RepeatedLabelError:
            return NormalizationResult(
                value=result,
                warnings=["address parse ambiguous; stored raw_input only"],
                validation_detail={"provider": "usaddress", "status": "not_attempted"},
            )
        return NormalizationResult(
            value=result,
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

    429 handling: reads Retry-After header, sleeps, retries up to config.max_retries.
    Raises RuntimeError if retry budget is exhausted.
    """

    config: AddressNormalizerConfig

    async def normalize(self, raw: str | None) -> NormalizationResult:
        """Standardize or validate *raw* via the external API."""
        if is_null_like(raw):
            return NormalizationResult(value=None, skipped=True)
        raw = raw.strip()
        endpoint = "validate" if self.config.run_validation else "standardize"
        url = f"{self.config.base_url}/api/v1/{endpoint}"
        payload = {"address": raw, "country": "US"}
        headers = {"X-API-Key": self.config.api_key}

        for attempt in range(self.config.max_retries + 1):
            async with httpx.AsyncClient() as client:
                response = await client.post(url, json=payload, headers=headers)
            if response.status_code == 429:
                if attempt >= self.config.max_retries:
                    raise RuntimeError(
                        f"address-validator rate limit: exhausted {self.config.max_retries} retries"
                    )
                wait = float(response.headers.get("Retry-After", "1"))
                await asyncio.sleep(wait)
                continue
            response.raise_for_status()
            data = response.json()
            return self._parse_response(raw, data)

        raise RuntimeError("address-validator: retry loop exited unexpectedly")

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
        }
        detail: dict = {"provider": "address-validator"}
        confidence_hint = "unconfirmed"
        if self.config.run_validation and "validation" in data:
            v = data["validation"]
            detail.update({
                "status": v.get("status"),
                "dpv_match_code": v.get("dpv_match_code"),
                "provider": v.get("provider", "address-validator"),
            })
            confidence_hint = _STATUS_MAP.get(v.get("status", ""), "not_attempted")
        detail["warnings"] = data.get("warnings", [])
        warnings = [f"address-validator warning: {w}" for w in data.get("warnings", [])]
        return NormalizationResult(
            value=result,
            warnings=warnings,
            confidence_hint=confidence_hint,
            validation_detail=detail,
        )


@dataclass
class FallbackAddressNormalizer:
    """Tries ExternalAddressNormalizer; falls back to LocalAddressNormalizer on any error.

    Use this in production pipelines. Pass config=None to always use local.
    """

    config: AddressNormalizerConfig | None = None
    _local: LocalAddressNormalizer = field(default_factory=LocalAddressNormalizer, init=False)

    async def normalize(self, raw: str | None) -> NormalizationResult:
        """Normalize *raw* via external service, with local fallback."""
        if self.config is None or is_null_like(raw):
            return self._local.normalize(raw)
        try:
            external = ExternalAddressNormalizer(self.config)
            return await external.normalize(raw)
        except Exception as exc:
            result = self._local.normalize(raw)
            result.warnings.insert(0, f"fallback to local address parser: {exc}")
            return result
