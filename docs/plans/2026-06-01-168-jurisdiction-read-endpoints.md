---
title: "#168 — Jurisdiction read endpoints (phase 1)"
date: 2026-06-01
status: implemented
---

# #168 — Jurisdiction read endpoints (phase 1)

## Problem

`Role.district` is an unanchored text label in usa-wa's schema. Without a first-class Jurisdiction entity in power-map, cross-cohort queries like "bills sponsored by the Senator from LD 21 in the 2022 cycle" are impossible, and redistricting breaks identity continuity. The write path is gated on #162/#164 (both now closed), but read endpoints are independent and unblock usa-wa's cache/sidecar integration immediately.

## Approach

Add four new schema tables (`jurisdiction_types`, `jurisdiction_relationship_types`, `jurisdictions`, `jurisdiction_relationships`), extend `entity_type` CHECK constraints on six existing tables to include `'jurisdiction'`, and seed lookup data and identifier types. Wire five read routes under `GET /api/v1/jurisdictions` behind standard `require_api_key` auth. No write path; no spatial geometry; no Cycles/Scenarios containers.

Design decisions (confirmed in #168 review):
- `jurisdictions.type_id` FK → `jurisdiction_types` (not free-text)
- `jurisdiction_relationship_types` has a `category` column; `exercises_concurrent_jurisdiction` excluded from MVP
- Bitemporal axes: `valid_from / valid_until` (validity) + `recorded_at / superseded_at` (transaction)
- Identifiers reuse existing `identifiers` table via `entity_identifier_types` extension (no separate `jurisdiction_identifiers` table)

## Tradeoffs / alternatives

- **Separate `jurisdiction_identifiers` table** — rejected (issue review); diverges from PM's polymorphic `identifiers` pattern; the `touch_parent_on_identifier_change` trigger would not fire.
- **Free-text `type` column** — rejected (issue review); no drift protection; usa-wa's `bill_types` resolution set the precedent for lookup tables.
- **Full stack in one issue** — deferred by user; write path ships as a follow-on once read endpoints are validated.

## Steps

1. **Schema — new tables + lookup seeds**

   Add to `schema.sql` (after existing lookup tables):
   - `jurisdiction_types` (id TEXT PK, slug TEXT UNIQUE, display_name TEXT, created_at); seed ~16 rows (`country`, `state`, `county`, `city`, `legislative_district_upper`, `legislative_district_lower`, `congressional_district`, `tribal`, `territory`, `special_district`, `school_district`, `judicial_district`, `metropolitan`, `borough`, `township`, `village`)
   - `jurisdiction_relationship_types` (id TEXT PK, slug TEXT UNIQUE, display_name TEXT, category TEXT NOT NULL CHECK (category IN ('spatial', 'governance', 'functional', 'lineage')), symmetric BOOLEAN NOT NULL DEFAULT FALSE, created_at); seed 11 rows (omit `exercises_concurrent_jurisdiction`)
   - `jurisdictions` (id TEXT PK, slug TEXT UNIQUE NOT NULL, name TEXT NOT NULL, type_id TEXT NOT NULL REFERENCES jurisdiction_types(id), valid_from DATE, valid_until DATE, recorded_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), superseded_at TIMESTAMPTZ, notes TEXT, created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), archived_at TIMESTAMPTZ)
   - `jurisdiction_relationships` (id TEXT PK, from_id TEXT NOT NULL REFERENCES jurisdictions(id), to_id TEXT NOT NULL REFERENCES jurisdictions(id), rel_type_id TEXT NOT NULL REFERENCES jurisdiction_relationship_types(id), valid_from DATE, valid_until DATE, recorded_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), superseded_at TIMESTAMPTZ, created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), CONSTRAINT chk_no_self_rel CHECK (from_id <> to_id))
   - `v_jurisdiction_display_names` view (jurisdiction_id, display_name, slug)

2. **Schema — extend entity_type CHECK constraints**

   Migration blocks (ALTER TABLE … DROP CONSTRAINT … ADD CONSTRAINT) to add `'jurisdiction'` to CHECK constraints on:
   - `entity_identifier_types.entity_type`
   - `entity_addresses.entity_type`
   - `links.entity_type`
   - `contact_methods.entity_type`
   - `import_provenance.entity_type`
   - `field_confidence.entity_type`
   - `deleted_entities.entity_type`

   Also seed three `entity_identifier_types` rows for jurisdiction identifiers: OCD (`ocd_id`), Census FIPS (`fips`), ISO 3166-2 (`iso_3166_2`).

3. **Pydantic schemas** (`src/api/public/schemas.py`)

   - `JurisdictionTypeResponse` (id, slug, display_name)
   - `JurisdictionIdentifierResponse` (scheme_slug, value)
   - `JurisdictionResponse` (id, slug, name, type, valid_from, valid_until, recorded_at, superseded_at, identifiers: list[JurisdictionIdentifierResponse], created_at, updated_at, archived_at)
   - `JurisdictionRelationshipTypeResponse` (id, slug, display_name, category, symmetric)
   - `JurisdictionRelationshipResponse` (id, from_id, to_id, rel_type, valid_from, valid_until, recorded_at, superseded_at)
   - `JurisdictionListResponse` (data: list[JurisdictionResponse], meta: standard pagination meta)

4. **Routes** (`src/api/public/jurisdictions.py`, registered in `router.py`)

   All behind `require_api_key`. Route order: `/resolve` before `/{id}` to prevent shadowing.

   | Route | Notes |
   |---|---|
   | `GET /jurisdictions` | Paginated list. Query params: `type` (slug filter), `archived` (bool, default false), `limit`/`offset`. |
   | `GET /jurisdictions/{id}` | Lookup by ULID or slug. 404 if not found. |
   | `GET /jurisdictions/{id}/relationships` | Edges involving this jurisdiction. Query params: `direction` (`from`/`to`/`both`, default `both`), `category` filter, `rel_type` (slug filter). Paginated. |
   | `GET /jurisdictions/{id}/lineage` | Walk `lineage`-category edges (`supersedes` / `succeeded_by` / `evolved_from`) recursively. Returns ordered list of jurisdictions in chain. `depth` param (default 10, max 50). |
   | `GET /jurisdictions/resolve` | Query params: `slug` xor (`scheme` + `value`). Returns single `JurisdictionResponse` or 404. |

5. **Tests** (`tests/api/public/test_jurisdictions.py`)

   Integration tests (require `TEST_DATABASE_URL`):
   - List: empty state, pagination (`limit`/`offset`/`has_more`), `type` filter, `archived` filter
   - Detail: by ULID, by slug, 404
   - Relationships: `direction` filter, `category` filter, empty state
   - Lineage: single-hop, multi-hop, cycle guard (depth cap), empty state
   - Resolve: by slug hit/miss, by identifier (OCD) hit/miss, missing params → 422
   - Auth: missing key → 401, invalid key → 401

## Open questions / risks

- **Relationship-type seed rows** — the 11 codes and their `category` assignments need confirmation before they're committed to the schema. Flagging in case usa-wa has opinions on specific codes.
- **Lineage cycle guard** — `jurisdiction_relationships` has no cycle-prevention constraint; the lineage endpoint relies on a depth cap. Acceptable for MVP; a proper cycle check (e.g., recursive CTE with `CYCLE` detection) can come later.
- **`v_jurisdiction_display_names` shape** — following the org/person view convention, but jurisdictions may want `slug` included in the view for URL construction. Including it provisionally.
