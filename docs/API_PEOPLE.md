# power-map Public API — People

Per-resource endpoint behaviour for **People**: filters, response shapes, and the
implicit rules this collection follows. Auth, pagination and conditional requests are in
`docs/PUBLIC_API.md`, the change feed in `docs/CHANGE_FEED.md`; write semantics in
`docs/OBSERVATIONS.md`; the other resources are indexed in `docs/API_ENTITIES.md`.

---

## People


### Endpoints

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `GET` | `/api/v1/people/search` | API key | Search by display name or identifier. Params: `q`, `identifier_type` + `identifier_value` (takes precedence over `q`), `include_archived`, `limit`, `offset`. |
| `GET` | `/api/v1/people/{id}` | API key | Detail by ULID. Returns public name variants, identifiers, `voice_embeddings_count`, `created_at`, and `updated_at`. ETag caching — see `docs/PUBLIC_API.md` § Caching — detail endpoints. |
| `GET` | `/api/v1/people/{id}/events` | API key | Paginated lifecycle events for a person. Params: `limit` (default 20, max 100), `offset`. Public-visibility and active events only. ETag caching — see the events response-shape section. |
| `POST` | `/api/v1/people/observations` | `observations:write` scope | Submit a person identity observation. |
| `POST` | `/api/v1/people/identify` | `voice_embeddings:read` scope | Identify a person by voice embedding similarity (open-set, global top-k). Returns top-k matches ordered by cosine similarity. Body: `{model_id, embedding, top_k?}`. Unknown model → empty matches; dimension mismatch or invalid embedding (zero vector, non-finite values) → 422. |
| `POST` | `/api/v1/people/verify` | `voice_embeddings:read` scope | Closed-set verification (#299): score the embedding against a declared candidate set instead of the global top-k. Body: `{model_id, embedding, person_ids}` (1–500 ids — legislature-scale rosters fit in one call, #310; duplicates deduped, first occurrence wins). Returns `{results: [{person_id, similarity, embedding_id, n_embeddings}]}` — one result per requested id, always, in request order. `similarity` = best (max cosine) across the person's active embeddings; `embedding_id` = the winning enrollment; a person with no active embeddings under the model (or an unknown/archived id) returns `similarity: null, embedding_id: null, n_embeddings: 0`, so absence is distinguishable from a low score. Exact scoring (no ANN index involved — a candidate can never be dropped by approximate recall). No server-side thresholding. Unlike identify, an unknown/non-queryable model → 422; also 422 on dimension mismatch or invalid embedding. |
| `POST` | `/api/v1/people/verify-batch` | `voice_embeddings:read` scope | Multi-embedding closed-set verification (#310): score N query embeddings against one declared candidate set in a single call — collapses the per-centroid verify loop for archival-scale jobs. Body: `{model_id, embeddings, person_ids}` (1–50 embeddings × 1–500 ids, bounding the exact scoring product at 25k pairs; duplicates deduped, first occurrence wins). Returns `{results: [{embedding_index, results: [...]}]}` — one group per query embedding, in `embeddings` request order, each carrying the full per-candidate result list with `/verify` semantics (request order, null = no enrollment, best enrollment wins, deterministic tiebreak, exact scoring, no thresholding). One SQL round-trip regardless of N. Note the response shape cost: a maximal call (50 × 500) returns 25k result rows ≈ 2–3 MB of JSON — size chunks accordingly. 422 on unknown/non-queryable model, or dimension mismatch / invalid embedding at any index (detail names the failing index). |
| `POST` | `/api/v1/people/embeddings/presence` | `voice_embeddings:read` scope | Bulk enrollment-presence query (#310): which of these person_ids have ≥1 active embedding under `model_id`. Body: `{model_id, person_ids}` (1–1000 ids; duplicates deduped, first occurrence wins). Returns `{results: [{person_id, n_embeddings}]}` in request order; unknown/archived ids or people with no active enrollments return `n_embeddings: 0` (no 404). Intended for pre-filtering verify candidate sets (once per launch + periodic refresh — enrollments can be pushed mid-job). Non-queryable model → 422, mirroring verify. |
| `POST` | `/api/v1/people/{id}/embeddings` | `voice_embeddings:write` scope | Write a voice embedding observation for a person. Idempotent on `(source_service, source_job_id, source_segment, person_id)` — duplicate against an *active* row returns 200 with the original row's ID. 404 if person is unknown or archived; 409 if the conflicting row is archived (restore or change provenance key first); 422 on dimension mismatch, unknown/write-disabled model, or invalid embedding (zero vector, non-finite values — #299; rejected at write time so stored vectors can never produce NaN similarity on reads). |
| `PATCH` | `/api/v1/people/{id}/embeddings/{eid}?model_id=` | `voice_embeddings:write` scope | Update mutable metadata on an active embedding. Patchable fields: `activity_ms`, `audio_sample_rate_hz`, `recorded_at`. The embedding vector, `model_id`, and provenance key fields are identity — not patchable. Returns all patchable fields after update. 404 if not found; 409 if archived (restore first); 422 for unknown model or empty body. |
| `DELETE` | `/api/v1/people/{id}/embeddings/{eid}?model_id=` | `voice_embeddings:write` scope | Soft-delete a single embedding (sets `archived_at`). Idempotent — re-deleting returns 200 with existing timestamp. 404 if not found; 422 for unknown model. |
| `DELETE` | `/api/v1/people/{id}/embeddings?model_id=&source_job_id=` | `voice_embeddings:write` scope | Batch soft-delete all active embeddings for a person matching `source_job_id`. Returns `{archived_count}`. 404 if person unknown or archived; 422 for unknown model. |
| `POST` | `/api/v1/people/{id}/embeddings/{eid}/restore?model_id=` | `voice_embeddings:write` scope | Restore a soft-deleted embedding (clears `archived_at`). 404 if not found; 409 if already active; 422 for unknown model. |
| `GET` | `/api/v1/people/{id}/embeddings?model_id=&include_archived=&source_job_id=&source_segment=&limit=&offset=` | `voice_embeddings:read` scope | Paginated listing of voice embeddings, newest first — ordered by `(created_at, id)` descending, a stable total order so offset pagination is complete even when rows share a `created_at`. `include_archived=true` includes archived rows. Optional `source_job_id` restricts to a single provenance job (index-backed; omit to enumerate the full set); optional `source_segment` (#299) narrows further to one provenance row — with `include_archived=true` this finds the archived row behind a write 409 in a single call. 404 if person unknown or archived; 422 for unknown model. |

### Observation write — `POST /people/observations`

Upserts a person by identifier using the same match-or-create semantics as the other observation write endpoints.

**Request fields:**

| Field | Required | Notes |
|-------|----------|-------|
| `identifier_type` | always | Must be a registered person identifier type slug (e.g. `person_wa_pdc`; `observo_speaker` for Observo operator-labeled voice speakers, value = an opaque Observo ULID; `person_wa_legislature_roster` (#456) for pre-1991 legislators from the WA Legislature's archival 1889–2025 roster — below the `person_wa_legislature_member_id` floor — value = `<name fold>:<first session year>`, e.g. `aeolson:1923`) |
| `identifier_value` | always | Value for the identifier |
| `names` | optional | List of `{name, name_type, is_canonical?}` — `name_type` must be a valid name type (e.g. `legal`, `preferred`); `is_canonical` defaults to `false`. Exact-match dedup: re-submitting the same name is a no-op. Canonical is scoped per `(person, name_type)` slot — a person may have one canonical `legal` and a separate canonical `preferred`. Unlike org names, person names do **not** auto-promote — a name is canonical only if explicitly submitted with `is_canonical: true`. The hint is ignored if a canonical already exists for that name's slot (never displaces). At most one entry per request may carry `is_canonical: true` (422 otherwise). |
| `personal_pronouns` | optional | Free-text pronouns string (e.g. `they/them`). Written only if the field is currently null; ignored if already set. |
| `role_assignments` | optional | List of `{role_id, start_date?, end_date?}`. Exact-match dedup on `(person_id, role_id, start_date, end_date)`. |
| `links` | optional | List of `{url, link_type_id XOR link_type_slug}` |
| `contact_methods` | optional | List of `{contact_type, value, display_label?}` — `contact_type` must be `email` or `phone`; `display_label` is an optional short human-readable label (e.g. `"Main Office"`, `"Committee Hotline"`) |
| `addresses` | optional | List of `{raw_input, address_type, valid_from?, valid_until?}` — `address_type` must be `mailing`, `physical`, or `other` (default `other`). `valid_from`/`valid_until` (`YYYY-MM-DD`, optional; `valid_from` ≤ `valid_until`, 422 otherwise) bound the address validity window; NULL/omitted = open-ended on that side. A **dateless** claim dedups against any existing window (never resurrects an admin-ended address); a **dated** claim dedups on the exact window and records a fresh row for a new one (#256). |
| `additional_identifiers` | optional | List of `{identifier_type_slug, identifier_value}` — for attaching secondary identifier schemes |
| `events` | optional | List of `{event_type_id XOR event_type_slug, pm_event_id?, op?, event_year?, event_month?, event_day?, event_hour?, event_minute?, event_second?, event_place_text?, event_place_address_id?, linked_entity_type?, linked_entity_id?, notes?, visibility?}`. `pm_event_id` refines in place; `op="retract"` archives it (#322) — both work on this embedded path too. See `docs/API_EVENTS.md` § Observation `events` surface for `pm_event_id`/`op` semantics. |

**Disposition semantics:**

| Disposition | Condition |
|-------------|-----------|
| `new` | Identifier not seen before; person created |
| `auto-attached` | Identifier already known; existing entity returned |
| `rejected` | Unknown identifier type; identifier belongs to a non-person entity; DB constraint violation. A human-readable `reason` string is always present on rejected responses. |

**When to include `display_label`:** Add a label when the contact method serves a specific named function — e.g. `"Scheduler"`, `"Committee Office"`, `"Main Switchboard"`. Omit it for generic personal numbers where the value alone is self-explanatory.
