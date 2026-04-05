# International Address Support

**Date:** 2026-04-05  
**Issue:** #44 (country field persistence) + broader international support  
**Address Validator upstream:** CannObserv/address-validator#86-89

---

## Goal

Extend Power Map's address subsystem to correctly store, display, normalize, and validate international addresses — with per-country field enforcement where standards exist and best-effort raw storage otherwise.

---

## Approved approach: service-driven metadata (Approach A)

Address Validator is the single authority for:
1. **Per-country field format** — which fields are present, their labels, required/optional state, and postal code patterns
2. **Normalization and validation** — now accepts an explicit `country` parameter (ISO 3166-1 alpha-2) instead of hardcoding `"US"`

Power Map caches country format metadata in-process with a 24h TTL. Country intelligence lives in one service; no bundled metadata in this repo.

---

## Key decisions and rationale

### Schema

- **Drop `DEFAULT 'US'`** on `addresses.country`. Country must be explicitly provided on every write. Existing rows remain valid (they have `'US'`). A future migration can add a `NOT NULL` constraint without a default once all write paths are updated.
- **`raw_input` always populated.** Already nullable but present; enforce non-null at the application layer for all new writes. Canonical fallback when the service can't parse structured fields.
- All structured fields (`address_line_1`, `city`, `region`, `postal_code`) remain nullable — they are enrichment, not requirements. For countries where the service can't parse, they're simply left null.
- No new columns needed beyond what #44 already exposes (`country` was always there).

### Country format metadata cache

- New module: `src/core/normalizers/address_meta.py`
- `get_country_format(country_code: str) -> dict` — fetches `GET /api/v1/countries/{code}/format`, caches per code with a 24h TTL using `cachetools.TTLCache`
- Falls back to a minimal US-default format dict on any network/parse error (graceful degradation — never blocks a form submission)
- Cache is process-local (same caveat as dup-count cache under multi-worker gunicorn)
- Exposed as a FastAPI dependency `get_country_format_dep` for admin routes that need to seed form field definitions

### Address Validator normalizer changes

- `ExternalAddressNormalizer.normalize()` accepts `country: str = "US"` and passes it in the payload instead of hardcoding
- `FallbackAddressNormalizer.normalize()` forwards `country` to the external normalizer
- `LocalAddressNormalizer` (fallback): US-only `usaddress` parsing unchanged; for non-US, stores `raw_input` only and sets `country` as given — no structured field parsing attempted
- `_maybe_confirm` in `orgs_addresses.py` passes the user-supplied `country` through to the normalizer and includes it in both `normalized_ctx` and `original_ctx`

### Admin UI — address form

- Country is the **first field** in `_address_form_row.html`: a text input (or select backed by a short list of common countries) defaulting to `"US"`
- On change, the form fetches `/admin/orgs/{org_id}/addresses/country-format/?code=XX` (a new HTMX endpoint) which returns a partial that re-renders the structured fields section with updated labels and required markers
- Field labels ("State" → "Province", "ZIP" → "Postal code") and visibility (hide fields absent from the format) update in place via `hx-swap="outerHTML"` on the fields container
- Server-side: the route reads the country format from cache to know which fields to include; no client-side JS address-data bundle needed
- `_address_form_row.html` placeholder text and `maxlength` driven by template context from the format response; safe fallback to US defaults when cache miss

### Admin UI — read row and confirm modal

- `_address_row.html`: show country badge/label when non-US (keep US implicit — no visual noise for the majority case)
- `_address_confirm_modal.html`:
  - Accept form: already has `country` hidden field (line 78) — now wired to a real route param
  - Keep my input form: add `country` hidden field (currently missing)
  - "You entered" panel: show country when non-US
- `_get_entity_address_or_404` query: add `a.country` to the SELECT (per #44 scope)

### Route handler changes (fixes #44 core)

Both `address_create` and `address_edit_row_post` in `orgs_addresses.py`:
- Add `country: str = Form("US")`
- Include `country` in the `INSERT INTO addresses` and `UPDATE addresses` statements
- Pass `country` through to `_maybe_confirm` so the normalizer receives it

### Ingestion pipeline

- `ExternalAddressNormalizer` change is transparent — the pipeline already passes `a.get("country", "US")` to the DB; now it also passes it to the normalizer call
- CSV sources currently have no country column — no change needed now; `"US"` default preserved for existing imports
- Future: add optional `Country` column to org/person CSV sources

### New `/country-format/` HTMX endpoint

`GET /admin/orgs/{org_id}/addresses/country-format/?code=XX`
- Auth-gated (check_auth)
- Fetches format from cache; returns `_address_fields_partial.html` — the structured fields block only (city/region/postal_code row plus address lines)
- Template context: field labels, required flags, postal code pattern hint, visibility booleans
- Used by the country `<select>` / `<input>` via `hx-get` + `hx-trigger="change"` + `hx-target="#address-structured-fields"`

---

## Out of scope

- Client-side postal code pattern validation (the `pattern` from the format endpoint is informational for now)
- Geocoding non-US addresses (dependent on Address Validator service capability)
- Updating CSV ingestion to accept a `Country` column (separate issue)
- People address forms (people don't currently have addresses in the admin UI)
- A country select populated from the full ISO 3166-1 list (start with a text input; can improve later)

---

## Testing strategy

- Unit tests for `get_country_format` cache: hit, miss, error fallback
- Unit tests for `ExternalAddressNormalizer` with non-US country (mock httpx)
- Unit tests for `LocalAddressNormalizer` non-US path (returns raw_input only, country as given)
- Integration tests for `address_create` / `address_edit_row_post`: assert `country` is persisted and returned in the read row
- Integration test: `_get_entity_address_or_404` returns `country` field
- Template render tests for `_address_confirm_modal.html`: Keep my input form includes country

---

## File map

| File | Change |
|---|---|
| `src/core/normalizers/address_meta.py` | New — country format cache |
| `src/core/normalizers/address.py` | Accept `country` param; non-US local fallback |
| `src/api/admin/orgs_addresses.py` | `country` Form param; include in INSERT/UPDATE; pass to normalizer; new `/country-format/` endpoint |
| `src/core/schema.sql` | Drop `DEFAULT 'US'` on `addresses.country` (migration) |
| `src/templates/admin/orgs/partials/_address_form_row.html` | Country field first; structured fields in swappable container |
| `src/templates/admin/orgs/partials/_address_fields_partial.html` | New — HTMX-swappable structured fields block |
| `src/templates/admin/orgs/partials/_address_confirm_modal.html` | Add country to Keep my input form; show in "You entered" panel |
| `src/templates/admin/orgs/partials/_address_row.html` | Show country badge when non-US |
| `tests/test_orgs_addresses.py` | country persistence + read; confirm modal forms |
| `tests/test_address_meta.py` | New — cache unit tests |
| `tests/test_address.py` | Normalizer country param tests |
