# src/core/normalizers/address_meta.py
"""Per-country address field format: fetch from Address Validator, 24h TTL cache."""

import os
import time

import httpx

from src.core.logging import get_logger

logger = get_logger(__name__)

_ADDRESS_VALIDATOR_BASE = os.environ.get(
    "ADDRESS_VALIDATOR_BASE_URL", "https://address-validator.exe.xyz:8000"
)
_ADDRESS_VALIDATOR_API_KEY = os.environ.get("ADDRESS_VALIDATOR_API_KEY", "")

_FORMAT_TTL = 86_400  # 24 hours

# Per-code cache: {country_code: {"value": dict, "expires": float}}
_format_cache: dict[str, dict] = {}

US_DEFAULT_FORMAT: dict = {
    "country": "US",
    "fields": [
        {"key": "address_line_1", "label": "Address line 1", "required": True},
        {"key": "address_line_2", "label": "Address line 2", "required": False},
        {"key": "city", "label": "City", "required": True},
        {"key": "region", "label": "State", "required": True},
        {"key": "postal_code", "label": "ZIP code", "required": False},
    ],
}


def invalidate_country_format_cache() -> None:
    """Expire all cached country formats (useful in tests)."""
    _format_cache.clear()


async def get_country_format(country_code: str) -> dict:
    """Return field format for *country_code* (ISO 3166-1 alpha-2).

    Fetches from Address Validator and caches for 24h.
    Falls back to US_DEFAULT_FORMAT on any error.
    US is returned from the constant without a network call.
    """
    code = country_code.upper()
    if code == "US":
        return US_DEFAULT_FORMAT

    entry = _format_cache.get(code)
    if entry and time.monotonic() < entry["expires"]:
        return entry["value"]

    url = f"{_ADDRESS_VALIDATOR_BASE.rstrip('/')}/api/v2/countries/{code}/format"
    try:
        headers = {"X-API-Key": _ADDRESS_VALIDATOR_API_KEY} if _ADDRESS_VALIDATOR_API_KEY else {}
        async with httpx.AsyncClient(follow_redirects=True) as client:
            response = await client.get(url, headers=headers)
            response.raise_for_status()
            fmt = response.json()
    except Exception as exc:
        logger.warning(
            "country format fetch failed for %s (%s): %s; falling back to US default",
            code,
            url,
            exc,
        )
        return US_DEFAULT_FORMAT

    _format_cache[code] = {"value": fmt, "expires": time.monotonic() + _FORMAT_TTL}
    return fmt
