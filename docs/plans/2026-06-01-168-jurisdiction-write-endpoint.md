---
title: "#168 — Jurisdiction write endpoint (phase 2)"
date: 2026-06-01
status: draft
---

# #168 — Jurisdiction write endpoint (phase 2)

## Problem

`POST /api/v1/observations` handles person and organization identity observations, but `entity_type = 'jurisdiction'` is unsupported. The schema (phase 1) already extended all `entity_type` CHECK constraints and seeded identifier types (`jur_ocd`, `jur_fips`, `jur_iso3166_2`), so the DB is write-ready. usa-wa needs the write path to bootstrap its initial jurisdiction records (`usa-wa`, 49 LDs, etc.) through the observation flow.

## Approach

Extend the existing observation pipeline at three layers:

1. **`ObservationRequest` schema** — add optional jurisdiction-specific fields (`jurisdiction_slug`, `jurisdiction_name`, `jurisdiction_type_slug`, `jurisdiction_valid_from`, `jurisdiction_valid_until`, `jurisdiction_notes`). Required only for NEW dispositions; ignored on AUTO_ATTACHED.

2. **`src/core/observation.py`** — two targeted changes:
   - `resolve_entity`: accept optional `create_data: dict | None = None`; pass to `_create_entity` for the new-entity case.
   - `_create_entity`: add `'jurisdiction'` branch — inserts into `jurisdictions` with required fields from `create_data`; returns REJECTED disposition (not ValueError) when `create_data` is missing for a NEW jurisdiction.
   - `write_names`: add `'jurisdiction'` as a no-op branch (name lives on the row, not a names table; silently skip rather than raise).

3. **`observations.py` route handler** — add `elif entity_type == "jurisdiction": pass` branch (no extra attribute writers needed beyond the generic ones already called for all types: `write_links`, `write_contact_methods`, `write_addresses`, `write_additional_identifiers`).

4. **`ChangeItem` schema** — extend `entity_type: Literal["person", "organization"]` to include `"jurisdiction"`.

The generic writers (`write_links`, `write_addresses`, `write_contact_methods`, `write_additional_identifiers`) already support `entity_type = 'jurisdiction'` at the DB level (phase 1 CHECK migrations) — no changes needed.

## Tradeoffs / alternatives

- **Separate `POST /api/v1/jurisdictions` write endpoint** — rejected for phase 2; the observation pattern is the established write path for all entity types per #164. A standalone endpoint would diverge from PM's upsert-by-identifier model.
- **Separate `resolve_jurisdiction` function** — cleaner in isolation but duplicates the identifier-lookup logic already in `resolve_entity`. Extending with `create_data` is a smaller diff and maintains a single lookup path.
- **Require jurisdiction fields in all jurisdiction observations** — rejected; AUTO_ATTACHED observations don't need name/slug/type (entity already exists). Making them required would break the two-observation model (first creates, subsequent just link).

## Steps

1. **Tests (red)** — write failing integration tests in `tests/api/public/test_observations.py` (or a new `test_observations_jurisdiction.py`):
   - NEW jurisdiction via observation (correct row created, disposition=`new`)
   - AUTO_ATTACHED jurisdiction (correct disposition, no duplicate row)
   - REJECTED when required jurisdiction fields missing for a new slug
   - REJECTED on unknown identifier type (unchanged)
   - Links, contacts, addresses, additional identifiers on jurisdictions
   - Auth: `observations:write` scope required (extend existing auth coverage)

2. **`ObservationRequest`** (`src/api/public/schemas.py`) — add six optional jurisdiction fields with a `@model_validator` that checks `valid_from ≤ valid_until` when both are set.

3. **`write_names`** (`src/core/observation.py`) — add `elif entity_type == "jurisdiction": return` to skip silently.

4. **`_create_entity` + `resolve_entity`** (`src/core/observation.py`):
   - `_create_entity`: add `elif entity_type == "jurisdiction"` — resolves `type_slug → type_id`, inserts into `jurisdictions`.
   - `resolve_entity`: add `create_data: dict | None = None` kwarg; pass to `_create_entity`; return `Disposition.REJECTED` (not raise) when entity_type is `'jurisdiction'` and no existing row found and `create_data` is None.

5. **Route handler** (`src/api/public/observations.py`) — build `jurisdiction_create_data` dict from request fields; pass to `resolve_entity(... create_data=...)`. Add `elif entity_type == "jurisdiction": pass` branch.

6. **`ObservationResponse` + `ChangeItem`** (`src/api/public/schemas.py`) — update `ObservationResponse.entity_type` comment; extend `ChangeItem.entity_type` Literal to include `"jurisdiction"`.

7. **`PUBLIC_API.md`** — note `entity_type: jurisdiction` in the observations section; document required fields for the NEW case.

## Open questions / risks

- **`jurisdiction_slug` uniqueness collision**: if a caller sends a well-known slug (`usa-wa`) as NEW but it already exists under a different identifier scheme, `resolve_entity` will return AUTO_ATTACHED (correct). If the slug they supply conflicts with an existing non-matching slug on the `jurisdictions` table, the DB UNIQUE constraint will raise — we should catch `asyncpg.UniqueViolationError` and return REJECTED.
- **`write_names` silent no-op**: if a caller sends `names` for a jurisdiction observation, they'll be silently dropped. Should we validate and REJECT? For now: no-op (forward-compatible). Can tighten in phase 3.
- **`ChangeItem.entity_type` is a Literal** — adding `"jurisdiction"` is a non-breaking additive change for consumers who handle the field as a string; it's technically a schema widening for strict Pydantic consumers. Flag in the PR.
