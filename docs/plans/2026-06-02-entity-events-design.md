---
title: entity_events — polymorphic lifecycle events for Persons and Organizations
date: 2026-06-02
status: approved
issue: 165
---

# entity_events — polymorphic lifecycle events for Persons and Organizations

## Problem

Power Map has no place to land lifecycle events (birth, death, founding, dissolution, marriage, merger, etc.) for Persons and Organizations. Sibling services (usa-wa, future scrapers consuming OCD/uscongress) either drop this data or duplicate it locally. Power Map should be the canonical store: capture once, expose to any consumer via public API and observation endpoint.

Date + place matter together. A birth date without a birth place strips context already present in many primary sources.

## Approved approach

### Core schema — `entity_event_types`

Seeded lookup table (mirrors `entity_identifier_types`). Admins can add new types without schema bumps. New *kinds* of constraint require a schema bump, but that's rare.

```sql
CREATE TABLE IF NOT EXISTS entity_event_types (
    id                      TEXT PRIMARY KEY,
    slug                    TEXT UNIQUE NOT NULL,
    display_name            TEXT NOT NULL,
    applies_to              TEXT NOT NULL
                            CHECK (applies_to IN ('person', 'organization', 'both')),
    requires_year           BOOLEAN NOT NULL DEFAULT FALSE,
    requires_linked_entity  BOOLEAN NOT NULL DEFAULT FALSE,
    constraints             JSONB,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

Initial seed:

| slug | applies_to | requires_year | requires_linked_entity |
|---|---|---|---|
| `birth` | person | ✓ | |
| `death` | person | ✓ | |
| `marriage` | person | | ✓ |
| `divorce` | person | | ✓ |
| `naturalization` | person | | |
| `founded` | organization | ✓ | |
| `dissolved` | organization | ✓ | |
| `merged_with` | organization | | ✓ |
| `split_from` | organization | | ✓ |
| `renamed` | organization | | |
| `other` | both | | |

### Core schema — `entity_events`

```sql
CREATE TABLE IF NOT EXISTS entity_events (
    id                      TEXT PRIMARY KEY,
    entity_type             TEXT NOT NULL CHECK (entity_type IN ('person', 'organization')),
    entity_id               TEXT NOT NULL,
    event_type_id           TEXT NOT NULL REFERENCES entity_event_types(id),

    -- Partial date/time: each component implies coarser ones are present
    event_year              INTEGER,
    event_month             INTEGER CHECK (event_month BETWEEN 1 AND 12),
    event_day               INTEGER CHECK (event_day BETWEEN 1 AND 31),
    event_hour              INTEGER CHECK (event_hour BETWEEN 0 AND 23),
    event_minute            INTEGER CHECK (event_minute BETWEEN 0 AND 59),
    event_second            INTEGER CHECK (event_second BETWEEN 0 AND 59),
    event_at                TIMESTAMPTZ,  -- denormalized; populated when full date known

    -- Place
    event_place_text        TEXT,
    event_place_address_id  TEXT REFERENCES addresses(id),

    -- Linked entity (marriage partner, merger target, etc.)
    linked_entity_type      TEXT CHECK (linked_entity_type IN ('person', 'organization')),
    linked_entity_id        TEXT,

    notes                   TEXT,
    visibility              TEXT NOT NULL DEFAULT 'public'
                            CHECK (visibility IN ('public', 'legal_only', 'hidden')),
    source_key_id           TEXT REFERENCES api_keys(id) ON DELETE SET NULL,
    verified_at             TIMESTAMPTZ,
    archived_at             TIMESTAMPTZ,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    -- Precision hierarchy: finer granularity implies coarser is known
    CONSTRAINT chk_month_requires_year    CHECK (event_month  IS NULL OR event_year   IS NOT NULL),
    CONSTRAINT chk_day_requires_month     CHECK (event_day    IS NULL OR event_month  IS NOT NULL),
    CONSTRAINT chk_hour_requires_day      CHECK (event_hour   IS NULL OR event_day    IS NOT NULL),
    CONSTRAINT chk_minute_requires_hour   CHECK (event_minute IS NULL OR event_hour   IS NOT NULL),
    CONSTRAINT chk_second_requires_minute CHECK (event_second IS NULL OR event_minute IS NOT NULL),
    CONSTRAINT chk_linked_entity_pair     CHECK (
        (linked_entity_type IS NULL) = (linked_entity_id IS NULL)
    )
);

CREATE INDEX IF NOT EXISTS idx_entity_events_entity
    ON entity_events(entity_type, entity_id);

CREATE INDEX IF NOT EXISTS idx_entity_events_type
    ON entity_events(event_type_id);
```

`event_at TIMESTAMPTZ` is computed by the application from partial columns and stored when day-or-finer precision is known. Date-only facts are stored as `YYYY-MM-DD 00:00:00Z`; the partial columns are the authoritative precision signal.

`event_place_text` and `event_place_address_id` are independent and both optional. Historical place names ("Berlin, Germany (pre-1990 borders)") use text; structured addresses use the FK. Both may be set simultaneously.

`archived_at` follows the standard admin archive model: NULL = active, non-NULL = archived. Hard delete requires archived state first (409 otherwise).

### Schema migration — `addresses.precision`

Add column:

```sql
ALTER TABLE addresses
    ADD COLUMN IF NOT EXISTS precision TEXT
        CHECK (precision IN ('country', 'region', 'city', 'postal', 'street'));
```

**Two-phase migration for existing rows:**

Phase 1 — audit (`scripts/audit_address_precision.py`): classify existing rows by populated columns:

| Tier | Condition |
|---|---|
| `street` | `address_line_1 IS NOT NULL` |
| `postal` | `postal_code IS NOT NULL AND address_line_1 IS NULL` |
| `city` | `city IS NOT NULL AND postal_code IS NULL AND address_line_1 IS NULL` |
| `region` | `region IS NOT NULL AND city IS NULL AND postal_code IS NULL AND address_line_1 IS NULL` |
| `country` | `country` only |
| unclassifiable | none of the above (raw_input only) |

Output: counts per tier + IDs of any unclassifiable rows. Human reviews before any data is written.

Phase 2 — backfill (`--execute` flag): sets `precision` from the same logic; unclassifiable rows stay NULL and require manual triage.

### Role/Role Assignment dates — out of scope

`role_assignments.start_date` and `end_date` remain as-is. Role assignment dates are membership temporal ranges (bounded periods), not discrete events — the conceptual model differs from life events. If partial date support or source attribution on role dates becomes a concrete need, that's a separate enhancement to `role_assignments` directly.

### Admin UI

Events section on person and org detail pages, consistent with Names/Links/Identifiers pattern:

- **List:** event type display name, intelligently rendered partial date (`1942` / `May 1942` / `12 May 1942 14:30`), place text or address, linked entity name, visibility badge
- **Add:** event type selector filtered by `applies_to`; partial date fields; place text + optional address search; linked entity search shown only when `requires_linked_entity = TRUE`
- **Edit / archive / unarchive / delete** (delete gated on archived state)
- `entity_event_types` management (add/edit types) deferred — seed data only this workstream

### Public API

Three new read endpoints under standard `require_api_key` (no write scope required):

```
GET  /api/v1/people/{id}/events
GET  /api/v1/organizations/{id}/events
GET  /api/v1/entity-event-types
```

`/events`:
- Standard `{"data": [...], "meta": {limit, offset, count, has_more}}` pagination
- Filters to `visibility = 'public'` and `archived_at IS NULL`
- Resolved `event_type` object inlined (slug, display_name) — no second round-trip needed
- Partial date as structured object: `{"year": 1942, "month": null, "day": null, "at": null}`

`/entity-event-types`: unpaginated `{"data": [{id, slug, display_name, applies_to, requires_year, requires_linked_entity}]}` — mirrors `GET /api/v1/link-types`.

### Observation endpoint integration

`ObservationRequest` (both `POST /people/observations` and `POST /orgs/observations`) gains an optional `events` list:

```python
class ObservationEventItem(BaseModel):
    event_type_id: str | None = None
    event_type_slug: str | None = None   # slug xor id (same pattern as link_type)
    event_year: int | None = None
    event_month: int | None = None
    event_day: int | None = None
    event_hour: int | None = None
    event_minute: int | None = None
    event_second: int | None = None
    event_place_text: str | None = None
    linked_entity_type: str | None = None
    linked_entity_id: str | None = None
    notes: str | None = None
    visibility: str = "public"
```

**Dedup:** application-layer (not a DB UNIQUE constraint — `linked_entity_id` makes NULLS NOT DISTINCT unwieldy and we want legible code). Key: `(event_type_id, event_year, event_month, event_day, event_hour, event_minute, event_second, linked_entity_id)` with NULLs treated as equal. Same event type + same partial date = skip. Different date precision from two sources = both land (human resolves).

**Conflict:** append-only. Two sources with conflicting years both land; no ingestion blocking. Human resolves via admin UI.

## Key decisions

- `entity_event_types` lookup table (not open text, not hard enum) — growable without code changes; typo-safe
- `applies_to` column controls which entity types may use each event type; enforced at application layer
- `requires_year` and `requires_linked_entity` columns on `entity_event_types` encode per-type validation rules — single source of truth, readable via SQL
- `event_at TIMESTAMPTZ` as denormalized convenience; partial columns are authoritative
- `event_place_text` + optional `event_place_address_id` — freeform and structured coexist
- `addresses.precision` explicitly signals place specificity; backfilled via two-phase audit/execute script
- `archived_at` follows standard admin archive model
- Role assignment dates stay on `role_assignments` — different conceptual model (membership range vs. discrete event)
- Dedup in observation writer is app-layer for legibility; conflict is append-only

## Out of scope

- `entity_event_types` admin management UI (seed data only)
- Temporal dimension (`valid_from`/`valid_to`) on `entity_addresses` — separate design
- Partial date support on `role_assignments`
- Fuzzy/probabilistic conflict resolution
- `jurisdiction` as an entity type for events
