# International Address Support Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire `country` through all address write/read paths, add per-country field format metadata cache, update the normalizer to pass `country` to the Address Validator service, and update admin UI templates to show/adapt per country.

**Architecture:** Three independent layers — (1) normalizer + metadata cache in `src/core/normalizers/`, (2) route handler fixes in `src/api/admin/orgs_addresses.py`, (3) template updates. Each layer is independently testable. The Address Validator service now accepts `country` on `/standardize` and `/validate` and exposes `GET /api/v1/countries/{code}/format`. Power Map caches format responses in-process with a 24h TTL using the existing manual dict pattern.

**Tech Stack:** Python 3.12, FastAPI, asyncpg, HTMX, Jinja2, httpx (for format fetch), `time.monotonic()` TTL cache (no new dependencies).

**Worktree:** `.worktrees/feat/62-international-addresses`

---

## File Map

| File | Action |
|---|---|
| `src/core/normalizers/address_meta.py` | Create — country format fetch + 24h TTL cache |
| `src/core/normalizers/address.py` | Modify — add `country` param to `normalize()` on all three classes |
| `src/api/admin/orgs_addresses.py` | Modify — `country` Form param, INSERT/UPDATE, pass to normalizer, new `/country-format/` endpoint |
| `src/core/schema.sql` | Modify — drop `DEFAULT 'US'` from `addresses.country` (migration block) |
| `src/templates/admin/orgs/partials/_address_fields_partial.html` | Create — HTMX-swappable structured fields block |
| `src/templates/admin/orgs/partials/_address_form_row.html` | Modify — country input first, structured fields use new partial |
| `src/templates/admin/orgs/partials/_address_confirm_modal.html` | Modify — country in Keep my input form + "You entered" panel |
| `src/templates/admin/orgs/partials/_address_row.html` | Modify — show country when non-US |
| `tests/core/normalizers/test_address_meta.py` | Create — cache unit tests |
| `tests/core/normalizers/test_address.py` | Modify — add country param tests |
| `tests/api/admin/test_orgs_addresses.py` | Modify — country persistence + read + country-format endpoint |
| `tests/api/admin/test_orgs_addresses_unit.py` | No change |

---

## Task 1: Country format metadata cache

**Files:**
- Create: `src/core/normalizers/address_meta.py`
- Create: `tests/core/normalizers/test_address_meta.py`

- [ ] **Step 1: Write failing tests**

```python
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
```

- [ ] **Step 2: Run tests — confirm they fail**

```bash
cd .worktrees/feat/62-international-addresses
uv run pytest tests/core/normalizers/test_address_meta.py -v 2>&1 | head -20
```

Expected: `ImportError` or `ModuleNotFoundError` for `address_meta`.

- [ ] **Step 3: Implement `address_meta.py`**

```python
# src/core/normalizers/address_meta.py
"""Per-country address field format: fetch from Address Validator, 24h TTL cache."""

import os
import time

import httpx

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

    try:
        url = f"{_ADDRESS_VALIDATOR_BASE}/api/v1/countries/{code}/format"
        headers = {"X-API-Key": _ADDRESS_VALIDATOR_API_KEY} if _ADDRESS_VALIDATOR_API_KEY else {}
        async with httpx.AsyncClient() as client:
            response = await client.get(url, headers=headers)
            response.raise_for_status()
            fmt = response.json()
    except Exception:
        return US_DEFAULT_FORMAT

    _format_cache[code] = {"value": fmt, "expires": time.monotonic() + _FORMAT_TTL}
    return fmt
```

- [ ] **Step 4: Run tests — confirm they pass**

```bash
uv run pytest tests/core/normalizers/test_address_meta.py -v
```

Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add src/core/normalizers/address_meta.py tests/core/normalizers/test_address_meta.py
git commit -m "#62 feat: add address_meta country format cache (24h TTL)"
```

---

## Task 2: Normalizer — pass `country` param

**Files:**
- Modify: `src/core/normalizers/address.py`
- Modify: `tests/core/normalizers/test_address.py`

- [ ] **Step 1: Write failing tests** (append to existing test file)

```python
# Append to tests/core/normalizers/test_address.py

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
```

- [ ] **Step 2: Run tests — confirm they fail**

```bash
uv run pytest tests/core/normalizers/test_address.py -v -k "country or non_us or forwards" 2>&1 | tail -15
```

Expected: failures on the new tests (old tests still pass).

- [ ] **Step 3: Update `address.py`**

Update the three normalizer classes:

**`LocalAddressNormalizer.normalize`** — add `country: str = "US"`:
```python
def normalize(self, raw: str | None, country: str = "US") -> NormalizationResult:
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
        result.update({
            "address_line_1": _build_line1(tagged),
            "address_line_2": _build_line2(tagged) if tagged.get("OccupancyType") else None,
            "city": tagged.get("PlaceName"),
            "region": tagged.get("StateName"),
            "postal_code": tagged.get("ZipCode"),
            "standardized": None,
        })
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
```

**`ExternalAddressNormalizer.normalize`** — add `country: str = "US"` and pass it in the payload:
```python
async def normalize(self, raw: str | None, country: str = "US") -> NormalizationResult:
    if is_null_like(raw):
        return NormalizationResult(value=None, skipped=True)
    raw = raw.strip()
    endpoint = "validate" if self.config.run_validation else "standardize"
    url = f"{self.config.base_url}/api/v1/{endpoint}"
    payload = {"address": raw, "country": country}
    headers = {"X-API-Key": self.config.api_key}
    # ... rest unchanged
```

**`FallbackAddressNormalizer.normalize`** — add `country: str = "US"` and forward it:
```python
async def normalize(self, raw: str | None, country: str = "US") -> NormalizationResult:
    if self.config is None or is_null_like(raw):
        return self._local.normalize(raw, country=country)
    try:
        external = ExternalAddressNormalizer(self.config)
        return await external.normalize(raw, country=country)
    except Exception as exc:
        result = self._local.normalize(raw, country=country)
        result.warnings.insert(0, f"fallback to local address parser: {exc}")
        return result
```

- [ ] **Step 4: Run all address tests**

```bash
uv run pytest tests/core/normalizers/test_address.py -v
```

Expected: all pass (including existing tests — `country="US"` default preserves old behavior).

- [ ] **Step 5: Commit**

```bash
git add src/core/normalizers/address.py tests/core/normalizers/test_address.py
git commit -m "#62 feat: add country param to address normalizers; non-US local stores raw only"
```

---

## Task 3: Route handler — wire `country` through create/edit/read

**Files:**
- Modify: `src/api/admin/orgs_addresses.py`
- Modify: `tests/api/admin/test_orgs_addresses.py`

- [ ] **Step 1: Write failing tests** (append to existing integration test file)

```python
# Append to tests/api/admin/test_orgs_addresses.py

def test_address_create_persists_country(client, org_and_address):
    oid, _ = org_and_address
    r = client.post(
        f"/admin/orgs/{oid}/addresses/",
        headers=HTMX_HEADERS,
        data={
            "address_line_1": "10 Downing St",
            "city": "London",
            "postal_code": "SW1A 2AA",
            "address_type": "physical",
            "country": "GB",
            "mode": "save",
        },
    )
    assert r.status_code == 200
    assert "<form" not in r.text
    # Country shown for non-US
    assert "GB" in r.text


def test_address_edit_persists_country(client, org_and_address):
    oid, eaid = org_and_address
    r = client.post(
        f"/admin/orgs/{oid}/addresses/{eaid}/edit-row/",
        headers=HTMX_HEADERS,
        data={
            "address_line_1": "10 Downing St",
            "city": "London",
            "postal_code": "SW1A 2AA",
            "address_type": "physical",
            "country": "GB",
            "mode": "save",
        },
    )
    assert r.status_code == 200
    assert "GB" in r.text


def test_address_read_row_returns_country(client, org_and_address):
    """After creating a GB address, read-row returns the country."""
    dsn = _dsn()
    oid, _ = org_and_address

    async def insert_gb_address():
        conn = await asyncpg.connect(dsn)
        try:
            aid = generate_id()
            eaid = generate_id()
            await conn.execute(
                "INSERT INTO addresses (id, address_line_1, city, postal_code, country)"
                " VALUES ($1, '10 Downing St', 'London', 'SW1A 2AA', 'GB')",
                aid,
            )
            await conn.execute(
                "INSERT INTO entity_addresses"
                " (id, entity_type, entity_id, address_id, address_type)"
                " VALUES ($1, 'organization', $2, $3, 'physical')",
                eaid, oid, aid,
            )
            return eaid, aid
        finally:
            await conn.close()

    async def cleanup(aid, eaid):
        conn = await asyncpg.connect(dsn)
        try:
            await conn.execute("DELETE FROM entity_addresses WHERE id=$1", eaid)
            await conn.execute("DELETE FROM addresses WHERE id=$1", aid)
        finally:
            await conn.close()

    eaid, aid = asyncio.run(insert_gb_address())
    try:
        r = client.get(f"/admin/orgs/{oid}/addresses/{eaid}/read-row/", headers=HTMX_HEADERS)
        assert r.status_code == 200
        assert "GB" in r.text
    finally:
        asyncio.run(cleanup(aid, eaid))


def test_address_us_country_not_shown_in_read_row(client, org_and_address):
    """US country is implicit — not shown in the read row."""
    oid, eaid = org_and_address
    r = client.get(f"/admin/orgs/{oid}/addresses/{eaid}/read-row/", headers=HTMX_HEADERS)
    assert r.status_code == 200
    # US should not appear as a badge or label (keep it clean for the majority case)
    assert ">US<" not in r.text
```

- [ ] **Step 2: Run tests — confirm they fail**

```bash
uv run pytest tests/api/admin/test_orgs_addresses.py -v -k "country" 2>&1 | tail -15
```

Expected: failures (country not persisted/shown).

- [ ] **Step 3: Update `_get_entity_address_or_404`** — add `a.country` to SELECT

```python
async def _get_entity_address_or_404(addr_id: str, org_id: str, db):
    row = await db.fetchrow(
        """SELECT ea.id, ea.address_type, ea.display_name,
                  a.id AS address_id, a.standardized, a.address_line_1, a.address_line_2,
                  a.city, a.region, a.postal_code, a.country
           FROM entity_addresses ea JOIN addresses a ON a.id = ea.address_id
           WHERE ea.id=$1 AND ea.entity_type='organization' AND ea.entity_id=$2""",
        addr_id,
        org_id,
    )
    if not row:
        raise HTTPException(status_code=404)
    return row
```

- [ ] **Step 4: Update `_maybe_confirm`** — accept and forward `country`

Add `country: str = "US"` parameter. Pass to `_NORMALIZER.normalize()`. Include in `original_ctx`:

```python
async def _maybe_confirm(
    request,
    org_id: str,
    addr_id: str | None,
    address_line_1: str,
    address_line_2: str,
    city: str,
    region: str,
    postal_code: str,
    address_type: str,
    display_name: str,
    country: str = "US",
):
    raw = " ".join(filter(None, [
        address_line_1.strip(), address_line_2.strip(),
        city.strip(), region.strip(), postal_code.strip(),
    ]))
    result = await _NORMALIZER.normalize(raw, country=country)
    # ... unchanged until normalized_ctx ...
    normalized_ctx = {
        # ... existing fields ...
        "country": result.value.get("country", country),
        # ... rest unchanged
    }
    original_ctx = {
        # ... existing fields ...
        "country": country,
    }
```

- [ ] **Step 5: Update `address_create`** — add `country` Form param, INSERT, normalizer call

Add to route params:
```python
country: str = Form("US"),
```

Update the `_maybe_confirm` call:
```python
confirm = await _maybe_confirm(
    request, org_id, None,
    address_line_1, address_line_2, city, region, postal_code,
    address_type, display_name, country,
)
```

Update INSERT:
```python
await db.execute(
    "INSERT INTO addresses"
    " (id, address_line_1, address_line_2, city, region, postal_code,"
    "  country, standardized, latitude, longitude, components)"
    " VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)",
    aid,
    address_line_1.strip() or None,
    address_line_2.strip() or None,
    city.strip() or None,
    region.strip() or None,
    postal_code.strip() or None,
    country.strip() or "US",
    _standardized,
    _latitude,
    _longitude,
    _components,
)
```

Also pass `country` through in the `mode == "edit"` and blank-input error re-render contexts so the form repopulates it correctly:
```python
"a": {
    "id": None,
    "address_line_1": address_line_1,
    # ... other fields ...
    "country": country,
},
```

- [ ] **Step 6: Update `address_edit_row_post`** — same changes as create

Add `country: str = Form("US")`.

Update `_maybe_confirm` call with `country`.

Update UPDATE statement:
```python
await db.execute(
    "UPDATE addresses"
    " SET address_line_1=$1, address_line_2=$2, city=$3, region=$4, postal_code=$5,"
    "     country=$6, standardized=$7, latitude=$8, longitude=$9, components=$10"
    " WHERE id=$11",
    address_line_1.strip() or None,
    address_line_2.strip() or None,
    city.strip() or None,
    region.strip() or None,
    postal_code.strip() or None,
    country.strip() or "US",
    _standardized,
    _latitude,
    _longitude,
    _components,
    existing["address_id"],
)
```

Pass `country` in all re-render `"a"` dicts.

- [ ] **Step 7: Run failing tests again**

```bash
uv run pytest tests/api/admin/test_orgs_addresses.py -v -k "country" 2>&1 | tail -20
```

The `country_shown` tests will still fail (templates not yet updated). `country` persistence tests should pass now.

- [ ] **Step 8: Run full address integration tests — confirm no regressions**

```bash
uv run pytest tests/api/admin/test_orgs_addresses.py tests/api/admin/test_orgs_addresses_unit.py -v
```

Expected: all prior tests pass, new persistence tests pass.

- [ ] **Step 9: Commit**

```bash
git add src/api/admin/orgs_addresses.py tests/api/admin/test_orgs_addresses.py
git commit -m "#62 fix: wire country through address create/edit/read routes (#44)"
```

---

## Task 4: Schema — drop `DEFAULT 'US'`

**Files:**
- Modify: `src/core/schema.sql`

- [ ] **Step 1: Add migration block to `schema.sql`**

Find the migration section at the bottom of `schema.sql` (near line 607 where the `latitude`/`longitude`/`components` additions live) and append:

```sql
-- Migration: drop DEFAULT 'US' from addresses.country
-- Existing rows keep their 'US' value; application layer now always provides country explicitly.
ALTER TABLE addresses ALTER COLUMN country DROP DEFAULT;
```

- [ ] **Step 2: Verify `apply_schema` is idempotent**

```bash
export $(cat /etc/power-map/.env | xargs) 2>/dev/null
export $(cat .env | xargs) 2>/dev/null
uv run python -c "
import asyncio, asyncpg, os
from src.core.db import apply_schema

async def run():
    conn = await asyncpg.connect(os.environ['DATABASE_URL'])
    await apply_schema(conn)
    await apply_schema(conn)  # second call should not error
    await conn.close()
    print('ok')

asyncio.run(run())
"
```

Expected: `ok` (no errors on double-run).

- [ ] **Step 3: Commit**

```bash
git add src/core/schema.sql
git commit -m "#62 chore: drop DEFAULT 'US' from addresses.country"
```

---

## Task 5: Templates — confirm modal + read row

**Files:**
- Modify: `src/templates/admin/orgs/partials/_address_confirm_modal.html`
- Modify: `src/templates/admin/orgs/partials/_address_row.html`
- Modify: `tests/api/admin/test_orgs_addresses.py`

- [ ] **Step 1: Write failing tests** (append to integration test file)

```python
# Append to tests/api/admin/test_orgs_addresses.py

@patch("src.api.admin.orgs_addresses._NORMALIZER")
def test_confirm_modal_keep_my_input_has_country(mock_normalizer, client, org_and_address):
    """Keep my input form must include country hidden field."""
    oid, _ = org_and_address
    mock_normalizer.normalize = AsyncMock(return_value=MagicMock(
        skipped=False,
        value={
            "address_line_1": "10 DOWNING ST",
            "address_line_2": None,
            "city": "LONDON",
            "region": None,
            "postal_code": "SW1A 2AA",
            "country": "GB",
            "standardized": "10 DOWNING ST LONDON SW1A 2AA",
            "latitude": None,
            "longitude": None,
            "components": None,
        },
        validation_detail=None,
    ))
    r = client.post(
        f"/admin/orgs/{oid}/addresses/",
        headers=HTMX_HEADERS,
        data={
            "address_line_1": "10 Downing St",
            "city": "London",
            "postal_code": "SW1A 2AA",
            "address_type": "mailing",
            "country": "GB",
        },
    )
    assert r.status_code == 200
    assert r.headers.get("hx-retarget") == "#address-confirm-portal"
    # Both forms (Keep my input and Accept) must carry country
    assert r.text.count('name="country"') == 2


@patch("src.api.admin.orgs_addresses._NORMALIZER")
def test_confirm_modal_shows_country_in_you_entered_when_non_us(
    mock_normalizer, client, org_and_address
):
    oid, _ = org_and_address
    mock_normalizer.normalize = AsyncMock(return_value=MagicMock(
        skipped=False,
        value={
            "address_line_1": "10 DOWNING ST",
            "address_line_2": None,
            "city": "LONDON",
            "region": None,
            "postal_code": "SW1A 2AA",
            "country": "GB",
            "standardized": "10 DOWNING ST LONDON SW1A 2AA",
            "latitude": None,
            "longitude": None,
            "components": None,
        },
        validation_detail=None,
    ))
    r = client.post(
        f"/admin/orgs/{oid}/addresses/",
        headers=HTMX_HEADERS,
        data={
            "address_line_1": "10 Downing St",
            "city": "London",
            "postal_code": "SW1A 2AA",
            "address_type": "mailing",
            "country": "GB",
        },
    )
    assert "GB" in r.text
```

- [ ] **Step 2: Run tests — confirm they fail**

```bash
uv run pytest tests/api/admin/test_orgs_addresses.py -v -k "confirm_modal" 2>&1 | tail -15
```

- [ ] **Step 3: Update `_address_confirm_modal.html`**

Add `country` to the "You entered" panel (show when non-US):
```html
<div>
  {{ original.address_line_1 }}
  {% if original.address_line_2 %}<br>{{ original.address_line_2 }}{% endif %}
  <br>{{ original.city }}{% if original.region %}, {{ original.region }}{% endif %} {{ original.postal_code }}
  {% if original.country and original.country != 'US' %}<br>{{ original.country }}{% endif %}
</div>
```

Add `country` hidden field to the "Keep my input" form (after `display_name`):
```html
<input type="hidden" name="country" value="{{ original.country or 'US' }}">
```

- [ ] **Step 4: Update `_address_row.html`** — show country badge when non-US

After the address text cell, add country display:
```html
<td>
  {% if a.standardized %}{{ a.standardized }}
  {% else %}{{ [a.address_line_1, a.city, a.region, a.postal_code] | select | join(', ') }}{% endif %}
  {% if a.country and a.country != 'US' %}
  <span class="badge badge--neutral" style="margin-left:var(--space-1)">{{ a.country }}</span>
  {% endif %}
</td>
```

- [ ] **Step 5: Run template tests**

```bash
uv run pytest tests/api/admin/test_orgs_addresses.py -v -k "confirm_modal or country" 2>&1 | tail -20
```

Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add src/templates/admin/orgs/partials/_address_confirm_modal.html \
        src/templates/admin/orgs/partials/_address_row.html \
        tests/api/admin/test_orgs_addresses.py
git commit -m "#62 feat: show country in confirm modal and read row"
```

---

## Task 6: Address form — country field + dynamic fields partial

**Files:**
- Create: `src/templates/admin/orgs/partials/_address_fields_partial.html`
- Modify: `src/templates/admin/orgs/partials/_address_form_row.html`
- Modify: `src/api/admin/orgs_addresses.py` (new `/country-format/` endpoint)
- Modify: `tests/api/admin/test_orgs_addresses.py`

- [ ] **Step 1: Write failing tests** (append to integration test file)

```python
# Append to tests/api/admin/test_orgs_addresses.py

def test_address_form_row_has_country_field(client, org_and_address):
    oid, _ = org_and_address
    r = client.get(f"/admin/orgs/{oid}/addresses/new-row/", headers=HTMX_HEADERS)
    assert r.status_code == 200
    assert 'name="country"' in r.text


def test_country_format_endpoint_returns_fields_partial(client, org_and_address):
    oid, _ = org_and_address
    with patch(
        "src.api.admin.orgs_addresses.get_country_format",
        new=AsyncMock(return_value={
            "country": "CA",
            "fields": [
                {"key": "address_line_1", "label": "Address line 1", "required": True},
                {"key": "address_line_2", "label": "Apt/suite", "required": False},
                {"key": "city", "label": "City", "required": True},
                {"key": "region", "label": "Province", "required": True},
                {"key": "postal_code", "label": "Postal code", "required": False},
            ],
        })
    ):
        r = client.get(
            f"/admin/orgs/{oid}/addresses/country-format/?country=CA",
            headers=HTMX_HEADERS,
        )
    assert r.status_code == 200
    assert "Province" in r.text
    assert "Postal code" in r.text


def test_country_format_endpoint_us_returns_default_labels(client, org_and_address):
    oid, _ = org_and_address
    with patch(
        "src.api.admin.orgs_addresses.get_country_format",
        new=AsyncMock(return_value={
            "country": "US",
            "fields": [
                {"key": "address_line_1", "label": "Address line 1", "required": True},
                {"key": "address_line_2", "label": "Address line 2", "required": False},
                {"key": "city", "label": "City", "required": True},
                {"key": "region", "label": "State", "required": True},
                {"key": "postal_code", "label": "ZIP code", "required": False},
            ],
        })
    ):
        r = client.get(
            f"/admin/orgs/{oid}/addresses/country-format/?country=US",
            headers=HTMX_HEADERS,
        )
    assert r.status_code == 200
    assert "State" in r.text
    assert "ZIP" in r.text
```

- [ ] **Step 2: Run tests — confirm they fail**

```bash
uv run pytest tests/api/admin/test_orgs_addresses.py -v -k "country_field or country_format" 2>&1 | tail -15
```

- [ ] **Step 3: Create `_address_fields_partial.html`**

This partial renders the dynamic structured fields block. It is both the initial server-side inclusion in the form row AND the HTMX swap target when country changes.

```html
{# admin/orgs/partials/_address_fields_partial.html #}
{# Context: a (existing row or None), fields (list of field defs from country format) #}
{# Rendered inside <div id="address-structured-fields"> in _address_form_row.html #}
{% set field_map = {} %}
{% for f in fields %}{% set _ = field_map.update({f.key: f}) %}{% endfor %}

{% if field_map.address_line_1 is defined %}
<div class="form-group" style="margin-bottom:0">
  <input type="text" name="address_line_1"
         placeholder="{{ field_map.address_line_1.label }}"
         value="{{ a.address_line_1 or '' if a else '' }}">
</div>
{% endif %}

{% if field_map.address_line_2 is defined %}
<div class="form-group" style="margin-bottom:0">
  <input type="text" name="address_line_2"
         placeholder="{{ field_map.address_line_2.label }} (optional)"
         value="{{ a.address_line_2 or '' if a else '' }}">
</div>
{% endif %}

{% if field_map.city is defined or field_map.region is defined or field_map.postal_code is defined %}
<div style="display:flex;gap:var(--space-2)">
  {% if field_map.city is defined %}
  <div class="form-group" style="margin-bottom:0;flex:2">
    <input type="text" name="city"
           placeholder="{{ field_map.city.label }}"
           value="{{ a.city or '' if a else '' }}">
  </div>
  {% endif %}
  {% if field_map.region is defined %}
  <div class="form-group" style="margin-bottom:0;flex:1">
    <input type="text" name="region"
           placeholder="{{ field_map.region.label }}"
           value="{{ a.region or '' if a else '' }}">
  </div>
  {% endif %}
  {% if field_map.postal_code is defined %}
  <div class="form-group" style="margin-bottom:0;flex:1">
    <input type="text" name="postal_code"
           placeholder="{{ field_map.postal_code.label }}"
           value="{{ a.postal_code or '' if a else '' }}">
  </div>
  {% endif %}
</div>
{% endif %}
```

Note: Jinja2 doesn't support dict assignment inside `{% set %}` with `.update()` directly. Use `namespace` instead:

```html
{# Simpler approach — build lookup from fields list directly #}
{% for f in fields %}
  {% if f.key == "address_line_1" %}
  <div class="form-group" style="margin-bottom:0">
    <input type="text" name="address_line_1"
           placeholder="{{ f.label }}"
           value="{{ a.address_line_1 or '' if a else '' }}">
  </div>
  {% endif %}
  {# ... etc for each field key #}
{% endfor %}
```

Actually, the cleanest approach for Jinja2 is to pass explicit context vars from the route:

The `/country-format/` route extracts labels from the format and passes them as template vars:

```python
# In the route:
field_labels = {f["key"]: f["label"] for f in fmt.get("fields", [])}
field_visible = {f["key"] for f in fmt.get("fields", [])}
```

Then the template uses `field_labels` and `field_visible` directly:

```html
{# admin/orgs/partials/_address_fields_partial.html #}
{# Context: a, field_labels (dict key→label), field_visible (set of visible keys) #}
{% if "address_line_1" in field_visible %}
<div class="form-group" style="margin-bottom:0">
  <input type="text" name="address_line_1"
         placeholder="{{ field_labels.get('address_line_1', 'Address line 1') }}"
         value="{{ a.address_line_1 or '' if a else '' }}">
</div>
{% endif %}

{% if "address_line_2" in field_visible %}
<div class="form-group" style="margin-bottom:0">
  <input type="text" name="address_line_2"
         placeholder="{{ field_labels.get('address_line_2', 'Address line 2') }} (optional)"
         value="{{ a.address_line_2 or '' if a else '' }}">
</div>
{% endif %}

<div style="display:flex;gap:var(--space-2)">
  {% if "city" in field_visible %}
  <div class="form-group" style="margin-bottom:0;flex:2">
    <input type="text" name="city"
           placeholder="{{ field_labels.get('city', 'City') }}"
           value="{{ a.city or '' if a else '' }}">
  </div>
  {% endif %}
  {% if "region" in field_visible %}
  <div class="form-group" style="margin-bottom:0;flex:1">
    <input type="text" name="region"
           placeholder="{{ field_labels.get('region', 'State') }}"
           value="{{ a.region or '' if a else '' }}">
  </div>
  {% endif %}
  {% if "postal_code" in field_visible %}
  <div class="form-group" style="margin-bottom:0;flex:1">
    <input type="text" name="postal_code"
           placeholder="{{ field_labels.get('postal_code', 'ZIP') }}"
           value="{{ a.postal_code or '' if a else '' }}">
  </div>
  {% endif %}
</div>
```

- [ ] **Step 4: Update `_address_form_row.html`**

Replace the existing address line / city / region / postal_code blocks with:
1. A country input at the top
2. A `<div id="address-structured-fields">` that includes the fields partial with US defaults

```html
{# Country input — drives dynamic field reload #}
<div class="form-group" style="margin-bottom:0">
  <input type="text" name="country" id="address-country-input"
         placeholder="Country (ISO code, e.g. US, GB, CA)"
         maxlength="2"
         value="{{ a.country or 'US' if a else 'US' }}"
         hx-get="/admin/orgs/{{ org_id }}/addresses/country-format/"
         hx-trigger="change"
         hx-target="#address-structured-fields"
         hx-swap="innerHTML"
         hx-include="[name='country']">
</div>

<div id="address-structured-fields">
  {% include "admin/orgs/partials/_address_fields_partial.html" %}
</div>
```

The country input uses `hx-include="[name='country']"` to pass the current value as `country=XX` on change. The endpoint accepts `country` (not `code`) — see Step 5. The partial is also used on initial render with US defaults.

The `new-row` and `edit-row-get` routes must pass `field_labels` and `field_visible` to the template context. Import `get_country_format` and call it in those routes.

- [ ] **Step 5: Add `/country-format/` endpoint to `orgs_addresses.py`**

Add the import at the top:
```python
from src.core.normalizers.address_meta import US_DEFAULT_FORMAT, get_country_format
```

Add the new route (before the delete route):
```python
@router.get("/country-format/")
async def address_country_format(
    org_id: str,
    request: Request,
    country: str = "US",
    user: AdminUser | RedirectResponse = Depends(get_admin_user),
    db=Depends(get_db),
):
    """Return HTMX partial of structured address fields for the given country code."""
    redirect, user = check_auth(user)
    if redirect:
        return redirect
    await _get_org_or_404(org_id, db)
    fmt = await get_country_format(country.upper())
    field_labels = {f["key"]: f["label"] for f in fmt.get("fields", [])}
    field_visible = {f["key"] for f in fmt.get("fields", [])}
    return templates.TemplateResponse(
        request,
        "admin/orgs/partials/_address_fields_partial.html",
        {"org_id": org_id, "a": None, "field_labels": field_labels, "field_visible": field_visible},
    )
```

Update `address_new_row` and `address_edit_row_get` to pass field context:
```python
async def address_new_row(...):
    # ...
    fmt = await get_country_format("US")
    field_labels = {f["key"]: f["label"] for f in fmt.get("fields", [])}
    field_visible = {f["key"] for f in fmt.get("fields", [])}
    return templates.TemplateResponse(
        request,
        "admin/orgs/partials/_address_form_row.html",
        {"org_id": org_id, "a": None, "field_labels": field_labels, "field_visible": field_visible},
    )
```

For `address_edit_row_get`, use the existing address's country to fetch the right format:
```python
row = await _get_entity_address_or_404(addr_id, org_id, db)
fmt = await get_country_format(row["country"] or "US")
field_labels = {f["key"]: f["label"] for f in fmt.get("fields", [])}
field_visible = {f["key"] for f in fmt.get("fields", [])}
```

Error re-render paths in `address_create` and `address_edit_row_post` also need `field_labels`/`field_visible`. Add a helper at module level:

```python
async def _field_context(country: str) -> dict:
    fmt = await get_country_format(country.upper() or "US")
    return {
        "field_labels": {f["key"]: f["label"] for f in fmt.get("fields", [])},
        "field_visible": {f["key"] for f in fmt.get("fields", [])},
    }
```

Call `await _field_context(country)` in every place that re-renders `_address_form_row.html`.

- [ ] **Step 6: Run all tests**

```bash
uv run pytest tests/api/admin/test_orgs_addresses.py -v 2>&1 | tail -30
```

Expected: all pass including the new country field and country-format endpoint tests.

- [ ] **Step 7: Full test suite**

```bash
uv run pytest --no-cov -q
```

Expected: all 247+ pass, 0 failures.

- [ ] **Step 8: Commit**

```bash
git add \
  src/api/admin/orgs_addresses.py \
  src/templates/admin/orgs/partials/_address_fields_partial.html \
  src/templates/admin/orgs/partials/_address_form_row.html \
  tests/api/admin/test_orgs_addresses.py
git commit -m "#62 feat: address form country field + dynamic fields partial via /country-format/"
```

---

## Task 7: Final verification

- [ ] **Step 1: Run full test suite**

```bash
export $(cat /etc/power-map/.env | xargs) 2>/dev/null
export $(cat .env | xargs) 2>/dev/null
uv run pytest --no-cov -q
```

Expected: all tests pass, 0 failures.

- [ ] **Step 2: Smoke test in dev browser**

Open `https://power-map.exe.xyz:8001/admin/orgs/` in a browser. Navigate to any org's detail page. Verify:
- "Add address" form shows a country input defaulting to "US"
- Changing country to "CA" updates field labels (Province instead of State, Postal code instead of ZIP)
- Creating a GB address saves correctly and shows "GB" badge in the read row
- Edit an existing US address: no country badge shown

- [ ] **Step 3: Commit any fixes, then push**

```bash
git push -u origin feat/62-international-addresses
```
