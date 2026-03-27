# Address Normalizer Admin UI — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire `FallbackAddressNormalizer` into admin address create/edit, adding a confirmation step so users review normalizer output before saving; blank submissions are rejected inline.

**Architecture:** Six tasks in order: schema migration → normalizer update → blank guard → mode=save/edit paths → confirm template → mode=confirm normalization path. Each task is independently testable. Existing tests remain green throughout (no API key in test env → local normalizer → standardized=None → save directly, unchanged behaviour).

**Tech Stack:** Python 3.12, FastAPI, asyncpg, Jinja2/HTMX, pytest; `src.core.normalizers.address.FallbackAddressNormalizer`

---

## File Map

| File | Change |
|---|---|
| `src/core/schema.sql` | Add `latitude`, `longitude`, `components` columns + migration ALTERs |
| `src/core/normalizers/address.py` | `ExternalAddressNormalizer._parse_response` captures lat/lng/components |
| `src/api/admin/orgs_addresses.py` | Blank guard, `mode` param, normalization call, confirm routing; updated INSERT/UPDATE |
| `src/templates/admin/orgs/partials/_address_form_row.html` | Inline error display; fix `<tr>` id guard for dict `a` |
| `src/templates/admin/orgs/partials/_address_confirm_row.html` | **New** — three embedded forms: Accept / Keep my input / Edit |
| `tests/core/normalizers/test_address.py` | Tests for lat/lng/components capture |
| `tests/api/admin/test_orgs_addresses.py` | Tests for blank guard, mode paths, confirm routing |

---

## Task 1: Schema migration

**Files:**
- Modify: `src/core/schema.sql`
- Test: `tests/api/admin/test_orgs_addresses.py`

- [ ] **Step 1: Write the failing integration test**

Add to `tests/api/admin/test_orgs_addresses.py`:

```python
import asyncpg

@pytest.mark.integration
def test_addresses_table_has_normalizer_columns():
    dsn = _dsn()
    async def check():
        conn = await asyncpg.connect(dsn)
        try:
            await apply_schema(conn)
            cols = await conn.fetch(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name='addresses' AND table_schema='public'"
            )
            return {r["column_name"] for r in cols}
        finally:
            await conn.close()
    col_names = asyncio.run(check())
    assert "latitude" in col_names
    assert "longitude" in col_names
    assert "components" in col_names
```

- [ ] **Step 2: Run to verify it fails**

```bash
cd /home/exedev/power-map/.worktrees/42-address-normalizer
export $(cat ../../env | xargs)
uv run pytest tests/api/admin/test_orgs_addresses.py::test_addresses_table_has_normalizer_columns -v
```

Expected: FAIL — columns absent.

- [ ] **Step 3: Add columns to schema.sql**

In `src/core/schema.sql`, update the `CREATE TABLE IF NOT EXISTS addresses` block to include the new columns before `created_at`:

```sql
CREATE TABLE IF NOT EXISTS addresses (
    id             TEXT             PRIMARY KEY,
    raw_input      TEXT,
    standardized   TEXT,
    address_line_1 TEXT,
    address_line_2 TEXT,
    city           TEXT,
    region         TEXT,
    postal_code    TEXT,
    country        TEXT             NOT NULL DEFAULT 'US',
    latitude       DOUBLE PRECISION,
    longitude      DOUBLE PRECISION,
    components     JSONB,
    created_at     TIMESTAMPTZ      NOT NULL DEFAULT NOW(),
    updated_at     TIMESTAMPTZ      NOT NULL DEFAULT NOW()
);
```

Then add idempotent migration statements after the `CREATE TABLE` block (search for the existing `-- Migration:` comment block and add before it, or after the `addresses` trigger):

```sql
-- Migration: add normalizer enrichment columns to addresses
ALTER TABLE addresses ADD COLUMN IF NOT EXISTS latitude   DOUBLE PRECISION;
ALTER TABLE addresses ADD COLUMN IF NOT EXISTS longitude  DOUBLE PRECISION;
ALTER TABLE addresses ADD COLUMN IF NOT EXISTS components JSONB;
```

- [ ] **Step 4: Run test to verify it passes**

```bash
uv run pytest tests/api/admin/test_orgs_addresses.py::test_addresses_table_has_normalizer_columns -v
```

Expected: PASS.

- [ ] **Step 5: Run full non-integration suite to confirm no regressions**

```bash
uv run pytest -x -q --ignore=tests/api/admin
```

Expected: 215 passed.

- [ ] **Step 6: Commit**

```bash
git add src/core/schema.sql tests/api/admin/test_orgs_addresses.py
git commit -m "#42 feat: add latitude, longitude, components columns to addresses"
```

---

## Task 2: Normalizer — capture lat/lng/components from API response

**Files:**
- Modify: `src/core/normalizers/address.py`
- Test: `tests/core/normalizers/test_address.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/core/normalizers/test_address.py`:

```python
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
```

- [ ] **Step 2: Run to verify they fail**

```bash
uv run pytest tests/core/normalizers/test_address.py::test_external_standardize_captures_components tests/core/normalizers/test_address.py::test_external_validate_captures_lat_lng_and_components -v
```

Expected: FAIL — `r.value` has no `latitude`/`components` keys.

- [ ] **Step 3: Update `ExternalAddressNormalizer._parse_response`**

In `src/core/normalizers/address.py`, update `_parse_response`:

```python
def _parse_response(self, raw: str, data: dict) -> NormalizationResult:
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
```

- [ ] **Step 4: Run new tests and full normalizer suite**

```bash
uv run pytest tests/core/normalizers/test_address.py -v
```

Expected: all 11 tests pass (9 existing + 2 new).

- [ ] **Step 5: Commit**

```bash
git add src/core/normalizers/address.py tests/core/normalizers/test_address.py
git commit -m "#42 feat: capture latitude, longitude, components in ExternalAddressNormalizer"
```

---

## Task 3: Blank-field guard

**Files:**
- Modify: `src/templates/admin/orgs/partials/_address_form_row.html`
- Modify: `src/api/admin/orgs_addresses.py`
- Test: `tests/api/admin/test_orgs_addresses.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/api/admin/test_orgs_addresses.py`:

```python
def test_address_create_blank_returns_form_with_error(client, org_and_address):
    oid, _ = org_and_address
    r = client.post(
        f"/admin/orgs/{oid}/addresses/",
        headers=HTMX_HEADERS,
        data={"address_line_1": "", "city": "", "region": "", "postal_code": "",
              "address_type": "mailing"},
    )
    assert r.status_code == 200
    assert "<form" in r.text
    assert "required" in r.text.lower()


def test_address_edit_blank_returns_form_with_error(client, org_and_address):
    oid, eaid = org_and_address
    r = client.post(
        f"/admin/orgs/{oid}/addresses/{eaid}/edit-row/",
        headers=HTMX_HEADERS,
        data={"address_line_1": "", "city": "", "region": "", "postal_code": "",
              "address_type": "mailing"},
    )
    assert r.status_code == 200
    assert "<form" in r.text
    assert "required" in r.text.lower()
```

- [ ] **Step 2: Run to verify they fail**

```bash
uv run pytest tests/api/admin/test_orgs_addresses.py::test_address_create_blank_returns_form_with_error tests/api/admin/test_orgs_addresses.py::test_address_edit_blank_returns_form_with_error -v
```

Expected: FAIL — currently saves successfully with blank fields.

- [ ] **Step 3: Update the form template**

In `src/templates/admin/orgs/partials/_address_form_row.html`, the template uses `{% if a %}` as a branch condition in four places to decide between the edit path (existing row) and the new-row path. When `a` is a plain dict (the error re-render case), it is truthy even when `a["id"]` is `None`, so all four must be changed to `{% if a and a.id %}`:

1. `<tr>` id attribute (line 2):
   ```html
   <tr id="{% if a and a.id %}address-row-{{ a.id }}{% else %}address-row-new{% endif %}">
   ```
2. The `<form>` attributes that set `hx-post` / `hx-target` (lines 4–11 approximately):
   ```html
   <form {% if a and a.id %}
         hx-post="/admin/orgs/{{ org_id }}/addresses/{{ a.id }}/edit-row/"
         hx-target="#address-row-{{ a.id }}"
         {% else %}
         hx-post="/admin/orgs/{{ org_id }}/addresses/"
         hx-target="#address-row-new"
         {% endif %}
         hx-swap="outerHTML"
         style="display:grid;gap:var(--space-2)">
   ```
3. The Cancel button's `{% if a %}` block (near the bottom):
   ```html
   {% if a and a.id %}
   hx-get="/admin/orgs/{{ org_id }}/addresses/{{ a.id }}/read-row/"
   hx-target="#address-row-{{ a.id }}"
   hx-swap="outerHTML"
   {% else %}
   onclick="this.closest('tr').remove()"
   {% endif %}
   ```
4. Each `value="{{ a.field or '' if a else '' }}"` input — these are fine as-is (truthy dict still resolves the field), no change needed.

Finally, add error display inside `<form>`, as the first child element:
```html
{% if error %}<div class="alert alert--error" role="alert">{{ error }}</div>{% endif %}
```

- [ ] **Step 4: Add blank guard to both route handlers**

In `src/api/admin/orgs_addresses.py`, add this helper at module level (after imports):

```python
def _is_all_blank(*fields: str) -> bool:
    return not any(f.strip() for f in fields)
```

In `address_create`, after `await _get_org_or_404(org_id, db)`, add:

```python
if _is_all_blank(address_line_1, city, region, postal_code):
    if not is_htmx(request):
        return RedirectResponse(f"/admin/orgs/{org_id}/", status_code=303)
    return templates.TemplateResponse(
        request,
        "admin/orgs/partials/_address_form_row.html",
        {
            "org_id": org_id,
            "a": {
                "id": None,
                "address_line_1": address_line_1,
                "address_line_2": address_line_2,
                "city": city,
                "region": region,
                "postal_code": postal_code,
                "address_type": address_type,
                "display_name": display_name,
            },
            "error": "At least one address field is required.",
        },
    )
```

In `address_edit_row_post`, after `existing = await _get_entity_address_or_404(...)`, add the same guard but with `"id": addr_id` in the `a` dict.

- [ ] **Step 5: Run new tests and full address test suite**

```bash
uv run pytest tests/api/admin/test_orgs_addresses.py -v
```

Expected: all existing + 2 new tests pass.

- [ ] **Step 6: Commit**

```bash
git add src/templates/admin/orgs/partials/_address_form_row.html src/api/admin/orgs_addresses.py tests/api/admin/test_orgs_addresses.py
git commit -m "#42 feat: reject blank address submissions with inline error"
```

---

## Task 4: mode=save and mode=edit paths

**Files:**
- Modify: `src/api/admin/orgs_addresses.py`
- Test: `tests/api/admin/test_orgs_addresses.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/api/admin/test_orgs_addresses.py`:

```python
def test_address_create_mode_save_stores_standardized(client, org_and_address):
    oid, _ = org_and_address
    r = client.post(
        f"/admin/orgs/{oid}/addresses/",
        headers=HTMX_HEADERS,
        data={
            "address_line_1": "456 OAK AVE",
            "city": "SEATTLE",
            "region": "WA",
            "postal_code": "98101",
            "address_type": "mailing",
            "mode": "save",
            "standardized": "456 OAK AVE SEATTLE WA 98101",
            "latitude": "47.6062",
            "longitude": "-122.3321",
            "components": '{"spec":"usps-pub28","spec_version":"unknown","values":{}}',
        },
    )
    assert r.status_code == 200
    assert "456 OAK AVE SEATTLE WA 98101" in r.text
    assert "<form" not in r.text


def test_address_edit_mode_save_stores_standardized(client, org_and_address):
    oid, eaid = org_and_address
    r = client.post(
        f"/admin/orgs/{oid}/addresses/{eaid}/edit-row/",
        headers=HTMX_HEADERS,
        data={
            "address_line_1": "123 MAIN ST",
            "city": "OLYMPIA",
            "region": "WA",
            "postal_code": "98501",
            "address_type": "mailing",
            "mode": "save",
            "standardized": "123 MAIN ST OLYMPIA WA 98501",
        },
    )
    assert r.status_code == 200
    assert "123 MAIN ST OLYMPIA WA 98501" in r.text
    assert "<form" not in r.text


def test_address_create_mode_edit_returns_prefilled_form(client, org_and_address):
    oid, _ = org_and_address
    r = client.post(
        f"/admin/orgs/{oid}/addresses/",
        headers=HTMX_HEADERS,
        data={
            "address_line_1": "789 PINE RD",
            "city": "TACOMA",
            "region": "WA",
            "postal_code": "98402",
            "address_type": "physical",
            "mode": "edit",
        },
    )
    assert r.status_code == 200
    assert "<form" in r.text
    assert "789 PINE RD" in r.text
```

- [ ] **Step 2: Run to verify they fail**

```bash
uv run pytest tests/api/admin/test_orgs_addresses.py::test_address_create_mode_save_stores_standardized tests/api/admin/test_orgs_addresses.py::test_address_edit_mode_save_stores_standardized tests/api/admin/test_orgs_addresses.py::test_address_create_mode_edit_returns_prefilled_form -v
```

Expected: FAIL — `mode` param not yet recognized.

- [ ] **Step 3: Add imports to orgs_addresses.py**

At the top of `src/api/admin/orgs_addresses.py`, add:

```python
import json
import os
```

- [ ] **Step 4: Add new Form parameters to both route handlers**

Add these parameters to `address_create` and `address_edit_row_post` signatures:

```python
mode: str = Form("confirm"),
standardized: str = Form(""),
latitude: str = Form(""),
longitude: str = Form(""),
components: str = Form(""),
```

- [ ] **Step 5: Extract save logic into a helper**

Add this helper below `_is_all_blank` in `orgs_addresses.py`:

```python
def _parse_normalizer_fields(
    standardized: str,
    latitude: str,
    longitude: str,
    components: str,
) -> tuple:
    """Parse mode=save normalizer form fields into DB-ready values."""
    _standardized = standardized.strip() or None
    _latitude = float(latitude.strip()) if latitude.strip() else None
    _longitude = float(longitude.strip()) if longitude.strip() else None
    _components = json.loads(components.strip()) if components.strip() else None
    return _standardized, _latitude, _longitude, _components
```

- [ ] **Step 6: Update INSERT in address_create to include new columns**

Replace the existing `INSERT INTO addresses` execute call with:

```python
_standardized, _latitude, _longitude, _components = _parse_normalizer_fields(
    standardized, latitude, longitude, components
)
await db.execute(
    "INSERT INTO addresses"
    " (id, address_line_1, address_line_2, city, region, postal_code,"
    "  standardized, latitude, longitude, components)"
    " VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)",
    aid,
    address_line_1.strip() or None,
    address_line_2.strip() or None,
    city.strip() or None,
    region.strip() or None,
    postal_code.strip() or None,
    _standardized,
    _latitude,
    _longitude,
    _components,
)
```

- [ ] **Step 7: Update UPDATE in address_edit_row_post to include new columns**

Replace the existing `UPDATE addresses` execute call with:

```python
_standardized, _latitude, _longitude, _components = _parse_normalizer_fields(
    standardized, latitude, longitude, components
)
await db.execute(
    "UPDATE addresses"
    " SET address_line_1=$1, address_line_2=$2, city=$3, region=$4, postal_code=$5,"
    "     standardized=$6, latitude=$7, longitude=$8, components=$9"
    " WHERE id=$10",
    address_line_1.strip() or None,
    address_line_2.strip() or None,
    city.strip() or None,
    region.strip() or None,
    postal_code.strip() or None,
    _standardized,
    _latitude,
    _longitude,
    _components,
    existing["address_id"],
)
```

- [ ] **Step 8: Add mode=edit handling**

In `address_create`, after the blank guard, add:

```python
if mode == "edit":
    if not is_htmx(request):
        return RedirectResponse(f"/admin/orgs/{org_id}/", status_code=303)
    return templates.TemplateResponse(
        request,
        "admin/orgs/partials/_address_form_row.html",
        {
            "org_id": org_id,
            "a": {
                "id": None,
                "address_line_1": address_line_1,
                "address_line_2": address_line_2,
                "city": city,
                "region": region,
                "postal_code": postal_code,
                "address_type": address_type,
                "display_name": display_name,
            },
        },
    )
```

In `address_edit_row_post`, add the same block but use `"id": addr_id`.

- [ ] **Step 9: Wrap remaining save logic in `if mode in ("save", "confirm")`**

The rest of the existing handler body (the INSERT/entity_addresses INSERT and the response) already handles mode=save correctly now. For mode=confirm, we'll add the normalizer call in Task 6. For now, the fallthrough behaviour (confirm → save directly) is correct since we haven't added the confirm branch yet.

- [ ] **Step 10: Run new and existing tests**

```bash
uv run pytest tests/api/admin/test_orgs_addresses.py -v
```

Expected: all tests pass.

- [ ] **Step 11: Commit**

```bash
git add src/api/admin/orgs_addresses.py tests/api/admin/test_orgs_addresses.py
git commit -m "#42 feat: add mode=save/edit paths and persist normalizer fields on address"
```

---

## Task 5: Confirm partial template

**Files:**
- Create: `src/templates/admin/orgs/partials/_address_confirm_row.html`

No failing test first — this is a template; the test is in Task 6.

- [ ] **Step 1: Create the template**

```html
{# admin/orgs/partials/_address_confirm_row.html #}
{# Context: org_id, addr_id (None for create), original (dict), normalized (dict), validation_status (str|None) #}
<tr id="{% if addr_id %}address-row-{{ addr_id }}{% else %}address-row-new{% endif %}">
  <td colspan="4" style="padding:var(--space-2) var(--space-4)">
    <div style="display:grid;gap:var(--space-3)">

      <div>
        <strong>Proposed:</strong> {{ normalized.standardized }}
        {% if validation_status %}
          &nbsp;<span class="badge {% if 'confirmed' in validation_status %}badge--success{% else %}badge--warning{% endif %}">
            {{ validation_status | replace("_", " ") }}
          </span>
        {% endif %}
      </div>

      <div style="display:flex;gap:var(--space-2);flex-wrap:wrap">

        {# Accept: save with normalized values #}
        <form {% if addr_id %}
              hx-post="/admin/orgs/{{ org_id }}/addresses/{{ addr_id }}/edit-row/"
              hx-target="#address-row-{{ addr_id }}"
              {% else %}
              hx-post="/admin/orgs/{{ org_id }}/addresses/"
              hx-target="#address-row-new"
              {% endif %}
              hx-swap="outerHTML">
          <input type="hidden" name="mode" value="save">
          <input type="hidden" name="address_line_1" value="{{ normalized.address_line_1 or '' }}">
          <input type="hidden" name="address_line_2" value="{{ normalized.address_line_2 or '' }}">
          <input type="hidden" name="city" value="{{ normalized.city or '' }}">
          <input type="hidden" name="region" value="{{ normalized.region or '' }}">
          <input type="hidden" name="postal_code" value="{{ normalized.postal_code or '' }}">
          <input type="hidden" name="country" value="{{ normalized.country or 'US' }}">
          <input type="hidden" name="standardized" value="{{ normalized.standardized or '' }}">
          <input type="hidden" name="latitude" value="{{ normalized.latitude if normalized.latitude is not none else '' }}">
          <input type="hidden" name="longitude" value="{{ normalized.longitude if normalized.longitude is not none else '' }}">
          <input type="hidden" name="components" value="{{ normalized.components_json or '' }}">
          <input type="hidden" name="address_type" value="{{ original.address_type }}">
          <input type="hidden" name="display_name" value="{{ original.display_name or '' }}">
          <button type="submit" class="btn btn--sm btn--primary">Accept</button>
        </form>

        {# Keep my input: save with original values, no normalizer data #}
        <form {% if addr_id %}
              hx-post="/admin/orgs/{{ org_id }}/addresses/{{ addr_id }}/edit-row/"
              hx-target="#address-row-{{ addr_id }}"
              {% else %}
              hx-post="/admin/orgs/{{ org_id }}/addresses/"
              hx-target="#address-row-new"
              {% endif %}
              hx-swap="outerHTML">
          <input type="hidden" name="mode" value="save">
          <input type="hidden" name="address_line_1" value="{{ original.address_line_1 or '' }}">
          <input type="hidden" name="address_line_2" value="{{ original.address_line_2 or '' }}">
          <input type="hidden" name="city" value="{{ original.city or '' }}">
          <input type="hidden" name="region" value="{{ original.region or '' }}">
          <input type="hidden" name="postal_code" value="{{ original.postal_code or '' }}">
          <input type="hidden" name="address_type" value="{{ original.address_type }}">
          <input type="hidden" name="display_name" value="{{ original.display_name or '' }}">
          <button type="submit" class="btn btn--sm btn--secondary">Keep my input</button>
        </form>

        {# Edit: return to form with original values pre-filled #}
        <form {% if addr_id %}
              hx-post="/admin/orgs/{{ org_id }}/addresses/{{ addr_id }}/edit-row/"
              hx-target="#address-row-{{ addr_id }}"
              {% else %}
              hx-post="/admin/orgs/{{ org_id }}/addresses/"
              hx-target="#address-row-new"
              {% endif %}
              hx-swap="outerHTML">
          <input type="hidden" name="mode" value="edit">
          <input type="hidden" name="address_line_1" value="{{ original.address_line_1 or '' }}">
          <input type="hidden" name="address_line_2" value="{{ original.address_line_2 or '' }}">
          <input type="hidden" name="city" value="{{ original.city or '' }}">
          <input type="hidden" name="region" value="{{ original.region or '' }}">
          <input type="hidden" name="postal_code" value="{{ original.postal_code or '' }}">
          <input type="hidden" name="address_type" value="{{ original.address_type }}">
          <input type="hidden" name="display_name" value="{{ original.display_name or '' }}">
          <button type="submit" class="btn btn--sm btn--secondary">Edit</button>
        </form>

      </div>
    </div>
  </td>
</tr>
```

- [ ] **Step 2: Commit**

```bash
git add src/templates/admin/orgs/partials/_address_confirm_row.html
git commit -m "#42 feat: add address confirmation partial template"
```

---

## Task 6: mode=confirm — normalization call and confirm routing

**Files:**
- Modify: `src/api/admin/orgs_addresses.py`
- Test: `tests/api/admin/test_orgs_addresses.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/api/admin/test_orgs_addresses.py`, at the top with other imports:

```python
from unittest.mock import AsyncMock, MagicMock, patch
```

Add tests:

```python
@patch("src.api.admin.orgs_addresses.FallbackAddressNormalizer")
def test_address_create_confirm_shows_confirm_partial(mock_cls, client, org_and_address):
    oid, _ = org_and_address
    inst = AsyncMock()
    inst.normalize.return_value = MagicMock(
        skipped=False,
        value={
            "address_line_1": "123 MAIN ST",
            "address_line_2": None,
            "city": "SEATTLE",
            "region": "WA",
            "postal_code": "98101",
            "country": "US",
            "standardized": "123 MAIN ST SEATTLE WA 98101",
            "latitude": None,
            "longitude": None,
            "components": None,
        },
        validation_detail=None,
    )
    mock_cls.return_value = inst
    r = client.post(
        f"/admin/orgs/{oid}/addresses/",
        headers=HTMX_HEADERS,
        data={
            "address_line_1": "123 Main St",
            "city": "Seattle",
            "region": "WA",
            "postal_code": "98101",
            "address_type": "mailing",
        },
    )
    assert r.status_code == 200
    assert "123 MAIN ST SEATTLE WA 98101" in r.text
    assert "Accept" in r.text
    assert "Keep my input" in r.text


@patch("src.api.admin.orgs_addresses.FallbackAddressNormalizer")
def test_address_create_confirm_saves_directly_when_no_standardized(mock_cls, client, org_and_address):
    oid, _ = org_and_address
    inst = AsyncMock()
    inst.normalize.return_value = MagicMock(
        skipped=False,
        value={"standardized": None, "address_line_1": "123 Main St",
               "city": "Seattle", "region": "WA", "postal_code": "98101",
               "country": "US", "address_line_2": None,
               "latitude": None, "longitude": None, "components": None},
        validation_detail=None,
    )
    mock_cls.return_value = inst
    r = client.post(
        f"/admin/orgs/{oid}/addresses/",
        headers=HTMX_HEADERS,
        data={
            "address_line_1": "123 Main St",
            "city": "Seattle",
            "region": "WA",
            "postal_code": "98101",
            "address_type": "mailing",
        },
    )
    assert r.status_code == 200
    assert "<form" not in r.text
    assert "Accept" not in r.text


@patch("src.api.admin.orgs_addresses.FallbackAddressNormalizer")
def test_address_confirm_shows_validation_status(mock_cls, client, org_and_address):
    oid, _ = org_and_address
    inst = AsyncMock()
    inst.normalize.return_value = MagicMock(
        skipped=False,
        value={
            "address_line_1": "123 MAIN ST",
            "address_line_2": None,
            "city": "SEATTLE",
            "region": "WA",
            "postal_code": "98101",
            "country": "US",
            "standardized": "123 MAIN ST SEATTLE WA 98101",
            "latitude": 47.6062,
            "longitude": -122.3321,
            "components": None,
        },
        validation_detail={"status": "confirmed", "dpv_match_code": "Y", "provider": "usps"},
    )
    mock_cls.return_value = inst
    r = client.post(
        f"/admin/orgs/{oid}/addresses/",
        headers=HTMX_HEADERS,
        data={
            "address_line_1": "123 Main St",
            "city": "Seattle",
            "region": "WA",
            "postal_code": "98101",
            "address_type": "mailing",
        },
    )
    assert r.status_code == 200
    assert "confirmed" in r.text
```

- [ ] **Step 2: Run to verify they fail**

```bash
uv run pytest tests/api/admin/test_orgs_addresses.py::test_address_create_confirm_shows_confirm_partial tests/api/admin/test_orgs_addresses.py::test_address_create_confirm_saves_directly_when_no_standardized tests/api/admin/test_orgs_addresses.py::test_address_confirm_shows_validation_status -v
```

Expected: FAIL.

- [ ] **Step 3: Add normalizer imports to orgs_addresses.py**

```python
from src.core.normalizers.address import AddressNormalizerConfig, FallbackAddressNormalizer
```

- [ ] **Step 4: Add `_build_normalizer` helper**

```python
def _build_normalizer() -> FallbackAddressNormalizer:
    api_key = os.environ.get("ADDRESS_VALIDATOR_API_KEY")
    run_validation = os.environ.get("ADDRESS_VALIDATOR_RUN_VALIDATION", "").lower() == "true"
    config = AddressNormalizerConfig(api_key=api_key, run_validation=run_validation) if api_key else None
    return FallbackAddressNormalizer(config=config)
```

- [ ] **Step 5: Add mode=confirm handling in address_create**

In `address_create`, after the mode=edit block and before the save logic, add:

```python
if mode == "confirm":
    raw = " ".join(filter(None, [
        address_line_1.strip(), address_line_2.strip(),
        city.strip(), region.strip(), postal_code.strip(),
    ]))
    result = await _build_normalizer().normalize(raw)
    if result.value and result.value.get("standardized"):
        validation_status = None
        if result.validation_detail and "status" in result.validation_detail:
            validation_status = result.validation_detail["status"]
        normalized_ctx = {
            "address_line_1": result.value.get("address_line_1") or address_line_1.strip(),
            "address_line_2": result.value.get("address_line_2") or address_line_2.strip(),
            "city": result.value.get("city") or city.strip(),
            "region": result.value.get("region") or region.strip(),
            "postal_code": result.value.get("postal_code") or postal_code.strip(),
            "country": result.value.get("country", "US"),
            "standardized": result.value.get("standardized"),
            "latitude": result.value.get("latitude"),
            "longitude": result.value.get("longitude"),
            "components_json": json.dumps(result.value["components"]) if result.value.get("components") else "",
        }
        original_ctx = {
            "address_line_1": address_line_1,
            "address_line_2": address_line_2,
            "city": city,
            "region": region,
            "postal_code": postal_code,
            "address_type": address_type,
            "display_name": display_name,
        }
        if not is_htmx(request):
            return RedirectResponse(f"/admin/orgs/{org_id}/", status_code=303)
        return templates.TemplateResponse(
            request,
            "admin/orgs/partials/_address_confirm_row.html",
            {
                "org_id": org_id,
                "addr_id": None,
                "normalized": normalized_ctx,
                "original": original_ctx,
                "validation_status": validation_status,
            },
        )
    # normalizer returned no standardized → fall through to save directly
```

- [ ] **Step 6: Add the same mode=confirm block to address_edit_row_post**

Same structure, but use `"addr_id": addr_id` in the template context.

- [ ] **Step 7: Run all address tests**

```bash
uv run pytest tests/api/admin/test_orgs_addresses.py -v
```

Expected: all tests pass.

- [ ] **Step 8: Run full non-integration suite**

```bash
uv run pytest -x -q --ignore=tests/api/admin
```

Expected: 215 passed.

- [ ] **Step 9: Commit**

```bash
git add src/api/admin/orgs_addresses.py tests/api/admin/test_orgs_addresses.py
git commit -m "#42 feat: wire FallbackAddressNormalizer into admin address create/edit with confirm step"
```
