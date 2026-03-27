# Address Normalizer: Admin UI Design

**Issue:** #42
**Date:** 2026-03-27

## Goal

Wire `FallbackAddressNormalizer` into the admin address create/edit flow so that:
- Blank submissions are rejected inline
- The external API enriches addresses with standardized form, corrected components, geocodes, and (when configured) USPS DPV validation
- Users review and confirm normalizer output before it is saved

## Approved approach

### Schema migration

Add three columns to `addresses`:

```sql
latitude    DOUBLE PRECISION,
longitude   DOUBLE PRECISION,
components  JSONB              -- raw ComponentSet.values from the address-validator API
```

`standardized` (existing) maps to both `standardize.standardized` and `validate.validated`.
Validation provenance (status, dpv_match_code, provider, warnings) continues to be stored in `field_confidence.validation_detail`.

### Normalizer update

`ExternalAddressNormalizer._parse_response` currently ignores `latitude`, `longitude`, and `components`. Update it to capture all three into `NormalizationResult.value`. Local normalizer (`LocalAddressNormalizer`) is unchanged — it produces no geocode and no components beyond what it already parses.

### Endpoint configuration

`AddressNormalizerConfig.run_validation: bool` already controls `/standardize` vs `/validate`. Expose via env var `ADDRESS_VALIDATOR_RUN_VALIDATION=true` read at app startup. No normalizer code change required.

### Blank-field guard

Before any normalization attempt: if all of `address_line_1`, `city`, `region`, `postal_code` are blank after strip → return `_address_form_row.html` at HTTP 200 with inline error "At least one address field is required." No save, no normalizer call. Follows the contact inline-error pattern (HTTP 200, `error` + `value_input` context vars).

### Confirmation flow: `mode` parameter

Both `address_create` and `address_edit_row_post` gain `mode: str = Form("confirm")`:

| mode | behaviour |
|---|---|
| `confirm` (default) | run blank guard → call normalizer → if `standardized`/`validated` returned, render `_address_confirm_row.html`; otherwise save directly |
| `save` | save directly (no normalization); used by Accept and Keep My Input buttons |
| `edit` | return `_address_form_row.html` pre-filled with submitted values; used by Edit button |

### New template: `_address_confirm_row.html`

Replaces the `<tr>` in-place (same `hx-swap="outerHTML"` target as the form). Contains three embedded forms:

1. **Accept** — hidden inputs with normalized values (address_line_1/2, city, region, postal_code, country, standardized, lat, lng, display_name, address_type) + `mode=save`
2. **Keep my input** — hidden inputs with original submitted values (no standardized/lat/lng) + `mode=save`
3. **Edit** — hidden inputs with original submitted values + `mode=edit`

When `run_validation=true`, the partial displays the USPS validation status (e.g. "Confirmed delivery point", "Address not found") alongside the normalizer's proposed address to aid the user's decision.

### Input assembly

The form submits individual fields; the normalizer takes a raw string. Concatenate before calling:

```python
raw = " ".join(filter(None, [address_line_1, address_line_2, city, region, postal_code])).strip()
```

Send as `address` (raw string) to let the API run its own parse → standardize pipeline.

### Bypass when no API key

`FallbackAddressNormalizer(config=None)` delegates to `LocalAddressNormalizer`, which returns `standardized=None`. The route saves directly without showing the confirm step. No user-visible change when `ADDRESS_VALIDATOR_API_KEY` is unset.

## Out of scope

- Retroactively normalizing existing DB rows (separate data migration)
- People admin addresses
- Overwriting individual address fields from normalizer output when user chooses Keep My Input
