---
title: "#170 entity_events — implementation plan"
date: 2026-06-02
status: approved
---

# #170 entity_events — implementation plan

## Problem

Power Map has no canonical store for lifecycle events (birth, death, founding, dissolution, marriage, merger, etc.) on Persons and Organizations. Sibling scrapers (usa-wa, future OCD consumers) drop or locally duplicate this data. The observation endpoint (#168) has no surface for pushing events programmatically. Design approved in `docs/plans/2026-06-02-entity-events-design.md`.

## Approach

Implement in eight sequential steps, each independently verifiable: schema first (prerequisite), then address precision audit/backfill, then public API read endpoints (TDD), then admin HTMX UI, then observation endpoint writer (TDD), then docs. Admin UI for `entity_event_types` management is deferred per design — seed data only this workstream.

## Tradeoffs / alternatives

- **Implement admin UI before public API** — rejected; public API endpoints are faster to write, covered by integration tests, and unblock usa-wa consumption without requiring the admin UI to be complete.
- **Single events endpoint for all entity types** — rejected per design; per-entity endpoints (`/people/{id}/events`, `/organizations/{id}/events`) are consistent with the rest of the public API surface.
- **Fold address precision backfill into `apply_schema`** — rejected; the audit step requires human review of output before any data is written.

## Steps

1. **Schema** — Add `entity_event_types` and `entity_events` tables to `schema.sql`; seed 11 initial event types via `INSERT … ON CONFLICT DO NOTHING`; add `addresses.precision` column; add `updated_at` trigger for `entity_events`. Apply to dev DB via `uv run … python -m src.core.db apply_schema`.

2. **Address precision audit + backfill** — Write `scripts/audit_address_precision.py` with dry-run (default) and `--execute` modes; run audit on dev DB; confirm output; run `--execute` to backfill existing rows.

3. **Public API — `GET /api/v1/entity-event-types`** — Red test → unpaginated list endpoint, standard `require_api_key` auth, no scope; green. Mirrors `GET /api/v1/link-types`.

4. **Public API — `/people/{id}/events` and `/organizations/{id}/events`** — Red tests → paginated list endpoints; filter `visibility = 'public'` and `archived_at IS NULL`; inline resolved `event_type` object; partial date as structured object; green.

5. **Admin — person events section** — HTMX partial on person detail page: list (partial date rendered, place, linked entity, visibility badge), add form (type selector filtered by `applies_to`, partial date fields, place text + address search, linked entity search gated on `requires_linked_entity`), edit, archive/unarchive, delete (gated on archived). Flash on every mutation. Invalidate no dup-count caches (not applicable here).

6. **Admin — org events section** — Same pattern as step 5; share template partials for the event form and list row where possible.

7. **Observation endpoint — `events` surface** — Add `ObservationEventItem` to `ObservationRequest`; write `_write_entity_events()` observation writer with application-layer dedup (key: `event_type_id + partial date components + linked_entity_id`, NULLs equal); wire into both `POST /people/observations` and `POST /orgs/observations`; red integration tests covering dedup, conflict (both land), type validation, `requires_year` / `requires_linked_entity` enforcement, `applies_to` mismatch → rejected; green.

8. **Docs** — Update `PUBLIC_API.md` with the three new endpoints; update `CONVENTIONS.md` if any new patterns were established (e.g., partial date rendering, event-place usage).

## Open questions / risks

- **`/people/{id}/events` filter params** — Deferred. v1 ships simple paginated list only; usa-wa to request via GH issue if filtering is needed.
- **`applies_to` enforcement on observation writer** — Confirmed: `applies_to` mismatch returns disposition `rejected`.
