---
title: P3 observation / upsert endpoint
date: 2026-05-29
status: implemented
---

# P3 observation / upsert endpoint

## Problem

External scrapers (starting with usa-wa) need a programmatic way to push identity observations to power-map: "I saw a WSL member named 'Sen. Jane Doe' at `person_wa_legislature_member_id=26142` — attach to a known Person or create one." No write endpoint exists today. Without it, usa-wa's P3 milestone (automated identity push) is blocked, and all scraped identity data must be loaded manually via CSV import.

## Approach

Add `POST /api/v1/observations` behind a new `observations:write` API key scope. The endpoint accepts an identifier pair (type + value) plus attribute claims (names, links, contact methods, addresses, org acronyms, role assignments, org parent, pronouns). It performs an exact identifier lookup: match found → `auto-attached`; no match → new entity created → `new`; validation failure → `rejected`. Attributes are written append-only with exact-match dedup per surface; nothing is ever overwritten without human action. The merge workflow surfaces new entity candidates out of band — no separate human review queue. Scopes are managed via two new lookup tables (`api_key_scope_types`, `api_key_scopes`) and a minimal admin UI. A `GET /api/v1/link-types` endpoint lets clients discover available link type slugs/IDs without hardcoding.

## Tradeoffs / alternatives

- **Fuzzy name match as fallback** — rejected; false-merge risk is high and hard to undo on a power-mapping system. Deferred to a separate issue (#167).
- **`queued-for-review` disposition + admin queue** — rejected; introduces a separate human review loop that the design explicitly avoids. Merge workflow is the editorial surface.
- **Freeform scope list on `api_keys`** — rejected; lookup table gives precise, auditable scope definitions and prevents typo-driven access grants.
- **Document link types in `PUBLIC_API.md`** — rejected; invites drift as the vocabulary evolves. Live endpoint is the authoritative source.

## Steps

1. **Schema** — add `api_key_scope_types` and `api_key_scopes` tables to `schema.sql`; seed `observations:write` scope type. Existing `api_keys` rows get no scopes (read-only behaviour preserved).

2. **Admin — scope management** — add scope grant/revoke to the API key detail page so `observations:write` can be assigned to a key before the endpoint is exercised in any test.

3. **`GET /api/v1/link-types`** — unpaginated list: `{"data": [{id, slug, display_name, is_social}, …]}`. Standard `require_api_key` auth (read-only; no scope required).

4. **Scope enforcement dep** — `require_scope(scope_id: str)` factory that wraps `require_api_key` and raises `403` if the authenticated key lacks the requested scope. Used as `Depends(require_scope("observations:write"))` on the observation route.

5. **Request / response Pydantic schemas** — `ObservationRequest` (identifier pair + optional attribute claims per surface); `ObservationResponse` (disposition, entity_id, entity_type). Enforce XOR constraints: `link_type_id` xor `link_type_slug`; `organization_parent_id` xor `organization_parent_name` xor `organization_parent_acronym`.

6. **Core match / create logic** — exact lookup in `identifiers` by `(entity_identifier_type_id, value)`; on hit return existing `entity_id`; on miss create entity row + identifier row, return new `entity_id` + `new` disposition.

7. **Per-surface attribute writers** — one function per surface, each implementing the agreed policy:

   | Surface | Policy |
   |---|---|
   | names | append if no exact string match; `visibility='public'`; record `source_key_id`; attach supplied `person_name_parts` to new name rows; write-if-null on existing name rows with no parts |
   | links | append; dedup on `(entity_type, entity_id, url, link_type_id)`; resolve slug→id if needed |
   | contact methods | E.164/email normalise first → `rejected` on failure; dedup on `(type, value)` |
   | addresses | call address validation API synchronously; dedup on `(standardised_form, address_type)`; API failure → `rejected` |
   | org acronyms | append; exact-string dedup; never set `is_canonical` |
   | role assignments | accept `role_id` (power-map ULID); no-op if open assignment exists for same role; else append |
   | org parent | `organization_parent_id` xor `organization_parent_name` xor `organization_parent_acronym`; name/acronym resolve canonical-only, active-only; zero or multiple matches → `rejected` (use `organization_parent_id` for certainty); write-if-null |
   | pronouns | write-if-null |
   | additional identifiers | conflict on same type, different value → abort; fall back to new entity |

8. **Route wiring** — `POST /api/v1/observations`: call match/create, then each attribute writer in a single DB transaction; return `ObservationResponse`.

9. **Tests** — integration tests covering: auto-attached, new, rejected (each validation path), identifier conflict fallback, each attribute surface's dedup/append/no-op behaviour, scope enforcement (missing scope → 403, wrong key → 401).

## Open questions / risks

- **Role assignment scope for usa-wa P3** — `role_id` is resolved as the reference mechanism, but confirm whether usa-wa's initial P3 payload actually includes role assignments. If not, the role assignment surface in step 7 can be deferred to a follow-on without blocking the endpoint launch.
- **`person_name_parts` distinction in CONVENTIONS.md** — accepted when upstream-supplied (pre-parsed, not auto-decomposed). CONVENTIONS.md currently says "never auto-written" without distinguishing these cases; update the relevant paragraph during implementation to make the upstream-supplied exception explicit.
