# #174 — Link event_place_address_id to structured address records

## Problem

`entity_events.event_place_address_id` is wired as a FK to `addresses(id) ON DELETE SET NULL`
but nothing ever writes it. Admins can only capture `event_place_text` (freeform). Structured
geocoded linkage — enabling place-faceted queries and API consumers that want coordinates — is
unreachable.

## Approach

Option B: admin address lookup. Admin populates `event_place_text` (always) and optionally links
a known `addresses.id`. Geocode-on-create (Option A) is deferred: event places are often
historical/informal strings that the mail-delivery normalizer handles poorly.

Constraint: address must have `precision IN ('city','postal','street')` or `NULL`. Enforced at
the app layer on create and edit.

No schema changes — the FK column exists; `addresses.precision` landed in #170.

## Steps

### 1. Tests (red)
Write failing tests in `tests/api/admin/test_orgs_events.py` and `test_people_events.py`:
- `test_event_create_links_address` — POST with valid address id → row stored, read-row shows badge
- `test_event_create_address_not_found` → 422 / form error
- `test_event_create_address_low_precision` — address with precision='region' → form error
- `test_event_edit_links_address` — PATCH existing event to add address id
- `test_event_edit_clears_address` — edit to remove address id (blank string)
- `test_event_read_row_shows_address_badge` — GET read-row with linked address returns city/region

Mirror 4 of the above in `test_people_events.py`.

Write failing observation tests in `tests/api/public/` for `event_place_address_id` passthrough.

### 2. Backend — `src/api/admin/_events_shared.py`
- Add `_validate_event_place_address(conn, raw_id)` helper:
  - strips input; returns `(None, None)` when blank
  - fetches `SELECT id, precision FROM addresses WHERE id=$1`
  - 404-style form error if not found
  - precision check: must be NULL or in `('city','postal','street')`
  - returns `(address_id, None)` on success, `(None, error_string)` on failure
- Extend `_EVENT_FETCH_QUERY` and `_EVENT_SINGLE_QUERY` with
  `LEFT JOIN addresses pa ON pa.id = ee.event_place_address_id`
  and select `pa.city AS place_city, pa.region AS place_region,
  pa.standardized AS place_standardized, pa.precision AS place_precision`
- `event_create` and `event_edit_row_post`: accept `event_place_address_id: str = Form("")`,
  call `_validate_event_place_address`, return form error on failure; include in INSERT / UPDATE

### 3. Templates
- `admin/shared/_event_form_row.html`: add address ID input after `event_place_text` field
- `admin/shared/_event_row.html`: place column shows city/region/precision badge when
  `ev.event_place_address_id` is set

### 4. Public API
- `src/api/public/schemas.py`:
  - New `EventPlaceAddress(BaseModel)`: `id, city, region, standardized, precision` (all optional
    except id)
  - `EntityEvent`: add `event_place_address: EventPlaceAddress | None = None`
  - `ObservationEventItem`: add `event_place_address_id: str | None = None`
- `src/api/public/events.py` — `row_to_event` includes `event_place_address` when cols present
- `src/api/public/orgs.py` and `people.py` events queries: add LEFT JOIN + address columns
- `src/core/observation.py` — `write_events`: validate `event_place_address_id` FK + precision;
  include in INSERT

## Out of scope
- Option A (geocode on create) — follow-on issue
- Admin typeahead for address search — follow-on
- Creating new `addresses` rows — linking only
