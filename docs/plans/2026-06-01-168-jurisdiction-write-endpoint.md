---
title: "#168 — Jurisdiction write endpoint (phase 2)"
date: 2026-06-01
status: draft
---

# #168 — Jurisdiction write endpoint (phase 2)

## Problem

There is no write path for jurisdiction entities. The schema (phase 1) already extended all `entity_type` CHECK constraints and seeded identifier types (`jur_ocd`, `jur_fips`, `jur_iso3166_2`), so the DB is write-ready. usa-wa needs the write path to bootstrap its initial jurisdiction records (`usa-wa`, 49 LDs, etc.) through the observation flow.

## Approach

Add `POST /api/v1/jurisdictions/observations` as a new per-entity observation endpoint — the first of what will become `/people/observations` + `/organizations/observations` (#169). Lives under the existing jurisdiction router, uses a clean `JurisdictionObservationRequest` schema with only jurisdiction-relevant fields. Leaves `POST /observations` (org/person) untouched.

Core observation logic stays in `src/core/observation.py`:
- `resolve_entity`: accept optional `create_data: dict | None = None`; pass to `_create_entity` for the new-entity case. No-op for existing callers.
- `_create_entity`: add `'jurisdiction'` branch — resolves `type_slug → type_id`, inserts into `jurisdictions`. Returns `Disposition.REJECTED` (not raises) when `create_data` is missing on a NEW disposition.
- `write_names`: add `'jurisdiction'` as a no-op branch (name lives on the row, not a names table).

The generic writers (`write_links`, `write_addresses`, `write_contact_methods`, `write_additional_identifiers`) already support `entity_type = 'jurisdiction'` at the DB level (phase 1 CHECK migrations) — no changes needed.

## Tradeoffs / alternatives

- **Extending `POST /observations`** — rejected; `ObservationRequest` already has ~12 flat fields and strains under entity-type branching. Jurisdiction's core fields (slug, type, bitemporal validity) don't map cleanly to the existing schema. Per-entity endpoint establishes the right pattern (#169 tracks the person/org follow-on).
- **Separate `resolve_jurisdiction` function** — cleaner in isolation but duplicates the identifier-lookup logic in `resolve_entity`. Extending with `create_data` is a smaller diff and maintains a single lookup path in core.
- **Require jurisdiction fields in all observations** — rejected; AUTO_ATTACHED observations don't need name/slug/type (entity already exists). Required-only-for-NEW is the correct model.

## Steps

1. **Tests (red)** — `tests/api/public/test_observations_jurisdiction.py`:
   - NEW jurisdiction (correct row created, disposition=`new`)
   - AUTO_ATTACHED jurisdiction (correct disposition, no duplicate)
   - REJECTED when required fields missing on NEW
   - REJECTED on unknown identifier type
   - Links, additional identifiers on jurisdictions
   - Auth: `observations:write` scope required; missing/invalid key tests in `test_auth.py`

2. **`JurisdictionObservationRequest`** (`src/api/public/schemas.py`) — new model with: `identifier_type`, `identifier_value`, `jurisdiction_slug`, `jurisdiction_name`, `jurisdiction_type_slug` (required for NEW; ignored on AUTO_ATTACHED), `jurisdiction_valid_from`, `jurisdiction_valid_until`, `jurisdiction_notes` (optional), `links`, `contact_methods`, `addresses`, `additional_identifiers`. `@model_validator`: `valid_from ≤ valid_until` when both set.

3. **`write_names`** (`src/core/observation.py`) — add `elif entity_type == "jurisdiction": return`.

4. **`_create_entity` + `resolve_entity`** (`src/core/observation.py`):
   - `_create_entity(conn, entity_type, create_data=None)`: add jurisdiction branch.
   - `resolve_entity(..., create_data=None)`: pass through to `_create_entity`; return REJECTED when entity_type is `'jurisdiction'`, no existing row, and `create_data` is None.

5. **`POST /jurisdictions/observations`** (`src/api/public/jurisdictions.py`) — new route in the existing jurisdictions router; `require_scope("observations:write")`; builds `create_data` dict from request; calls `resolve_entity`; calls generic writers; returns `ObservationResponse`.

6. **`ChangeItem`** (`src/api/public/schemas.py`) — extend `entity_type: Literal["person", "organization"]` to include `"jurisdiction"`.

7. **`PUBLIC_API.md`** — add `POST /jurisdictions/observations` to the Jurisdictions section; document required-for-NEW fields and disposition semantics.

## Open questions / risks

- **Slug uniqueness collision on NEW**: if the supplied `jurisdiction_slug` already exists under a different identifier, the DB UNIQUE constraint raises `asyncpg.UniqueViolationError` — catch it and return REJECTED.
- **`write_names` silent no-op**: names in a jurisdiction observation payload are dropped silently. Acceptable for MVP; can validate and REJECT in a later pass.
- **`ChangeItem.entity_type` widening**: adding `"jurisdiction"` to the Literal is non-breaking for string consumers; flag for strict Pydantic consumers in the PR.
