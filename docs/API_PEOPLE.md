# power-map Public API — People

Per-resource endpoint behaviour for **People**: filters, response shapes, and the
implicit rules this collection follows. Auth, pagination and conditional requests are in
`docs/PUBLIC_API.md`; write semantics in
`docs/OBSERVATIONS.md`; the other resources are indexed in `docs/API_ENTITIES.md`.

---

## People


### Endpoints

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `GET` | `/api/v1/people/search` | API key | Search by display name or identifier. Params: `q`, `identifier_type` + `identifier_value` (takes precedence over `q`; returns every holder, ordered by `id`, #811), `include_archived`, `limit`, `offset`. |
| `GET` | `/api/v1/people/{id}` | API key | Detail by ULID. Returns public names, each with its `name_type` (a `misspelling` is a search key: never display it), identifiers, `voice_embeddings_count`, `created_at`, and `updated_at`. ETag caching — see `docs/PUBLIC_API.md` § Caching — detail endpoints. Merged/deleted id → `410` + `merged_into` (§ Retired ids). |
| `GET` | `/api/v1/people/{id}/events` | API key | Paginated lifecycle events for a person. Params: `limit` (default 20, max 100), `offset`. Public-visibility and active events only. ETag caching — see the events response-shape section. |
| `POST` | `/api/v1/people/observations` | `observations:write` scope | Submit a person identity observation. |
| `GET` | `/api/v1/person-name-types` | API key | The person name-type reference (#857): per type, `meaning`, `use_when`, `not_for`, `example`, and how PM treats it — `display_rank`, `auto_promote`, `never_canonical`, `landing_visibility`, `renders_row`, `merge_distinct`, `decomposable`, `presentable` (may a consumer show it as a name the person goes by). Unpaginated, registry order; ETag (content hash, [Conditional requests](PUBLIC_API.md#conditional-requests)). Read it instead of hardcoding the set. |
| `POST` | `/api/v1/people/identify` | `voice_embeddings:read` scope | Identify a person by voice embedding similarity (open-set, global top-k). Returns top-k matches ordered by cosine similarity — `top_k` of them whenever that many active embeddings exist: the HNSW scan runs with `hnsw.iterative_scan = strict_order`, a pool startup parameter (`POOL_SERVER_SETTINGS`, #664), so archived rows' dead index entries can't shorten the result until more than `hnsw.max_scan_tuples` (default 20,000) entries have been visited. Recall stays approximate. Body: `{model_id, embedding, top_k?}`. Unknown model → empty matches; dimension mismatch or invalid embedding (zero vector, non-finite values) → 422. A match's `similarity` is therefore never null: a NaN cosine needs a zero-norm vector, and both doors reject one (#299, #618). |
| `POST` | `/api/v1/people/identify-batch` | `voice_embeddings:read` scope | Multi-embedding identify (#638): the open-set top-k for N query embeddings in one call and **one read-bucket token** — collapses the per-centroid identify loop. Body: `{model_id, embeddings, top_k?}` (1–50 embeddings; `top_k` clamped to 1–25 like identify, so ≤ 1,250 matches per call). Returns `{results: [{embedding_index, matches: [IdentifyMatch]}]}` — one group per query embedding, in `embeddings` request order; key on `embedding_index`, not list position. Each group's `matches` is what `/identify` returns for that embedding alone: same fields, same nullability (`person_name` nullable, `similarity` never null), same ANN index scan; exact-distance ties (identical enrollments), which `/identify` leaves in index order, are ordered by `embedding_id`. Identify semantics, not verify's: unknown/non-queryable model → every group `matches: []`. Invalid embedding (zero vector, non-finite values) or dimension mismatch at any index → 422 for the whole call (detail names the failing index for dimension). Every request field applies to the whole batch — per-batch context hints (observo#235), if added, sit beside them, not per embedding. |
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
| `identifier_type` | always | Must be a registered person identifier type slug — see **Person identifier types** below |
| `identifier_value` | always | Value for the identifier |
| `names` | optional | List of `{name, name_type?, locale?, script?, sort_as?, parts?, is_canonical?, op?, citations?}`. `name_type` defaults to `legal`; each type's meaning and behaviour is in `docs/NAME_TYPES.md`, served by `GET /person-name-types`. A spelling the person does not use (a transcript's guess, a typo) is `misspelling`, not `variant` (#857). **Dedup** is exact on `(name, name_type, locale, script)`, case- and whitespace-sensitive (names are stored verbatim): re-sending a name is a no-op. **Canonical** is the one displayed name per person (#308). `is_canonical: true` claims the slot only when the person has none, never displacing one; at most one entry per request may carry it (422 otherwise). With no hint, PM promotes the payload's best-ranked name when the person has none — never a `variant`, `misspelling`, `deadname`, `mrz`, `reading` or `romanization` (#845, #857). A hint on a `deadname` or `misspelling` is ignored. **`op: "retract"`** (#845) deletes the public row of that identity which the calling key added. **`citations`** (#845) attaches citation items to the name row in the same call. Each name answers in the response's `names[]` — see **Name observations** below. |
| `personal_pronouns` | optional | Free-text pronouns string (e.g. `they/them`). Written only if the field is currently null; ignored if already set. |
| `role_assignments` | optional | List of `{role_id, start_date?, end_date?}`. An unknown or archived `role_id` rejects the whole observation `role_not_found: '<id>'` (#602), as `POST /assignments/observations` does. An item is a no-op when the person already holds an open (no `end_date`) tenure on that role, or when a retracted twin `(person, role, start_date)` exists (#391 — never resurrected). Otherwise a tenure is created; no per-item results are returned — see `docs/API_ASSIGNMENTS.md`. |
| `links` | optional | List of `{url, link_type_id XOR link_type_slug}` |
| `contact_methods` | optional | List of `{contact_type, value, display_label?}` — `contact_type` must be `email` or `phone`; `display_label` is an optional short human-readable label (e.g. `"Main Office"`, `"Committee Hotline"`) |
| `addresses` | optional | List of `{raw_input, address_type, valid_from?, valid_until?}` — `address_type` must be `mailing`, `physical`, or `other` (default `other`). `valid_from`/`valid_until` (`YYYY-MM-DD`, optional; `valid_from` ≤ `valid_until`, 422 otherwise) bound the address validity window; NULL/omitted = open-ended on that side. A **dateless** claim dedups against any existing window (never resurrects an admin-ended address); a **dated** claim dedups on the exact window and records a fresh row for a new one (#256). |
| `additional_identifiers` | optional | List of `{identifier_type_slug, identifier_value}` — for attaching secondary identifier schemes. **Internal (`pm_*`) types are refused here** (`rejected`, `Internal identifier type … cannot be assigned via observations`) — they address the entity via `identifier_type`, they are not attachable. **A type for another entity type is refused** (`identifier_type_mismatch: '<slug>'`, #617). **One value per type per entity:** re-sending a value the person already holds (in any of its rows of that type) is a no-op, a *different* value for a type the person already carries rejects the whole observation (`reason: identifier_conflict: '<slug>'`). **One holder per site ULID:** a `*_cannabis_observer` value another entity holds `identifier_taken: '<slug>' held by [archived ]'<id>'` (#812, `docs/OBSERVATIONS.md` § Identifier types). **Locked types (#710):** on a person usa-wa anchors, `person_wa_legislature_roster` and `person_wa_legislature_member_id` come only from usa-wa — a value the person does not already hold rejects the whole observation (`identifier_locked: '<slug>'`). **Replace (#848):** an item may add `replaces: <the value held>` to swap a `*_cannabis_observer` value in one write — `replaces_not_held: '<slug>'` when the entity holds neither value, `replaces_unsupported: '<slug>'` on any other type (`docs/ANCILLARY.md` § Replacing). |
| `events` | optional | List of `{event_type_id XOR event_type_slug, pm_event_id?, op?, event_year?, event_month?, event_day?, event_hour?, event_minute?, event_second?, event_place_text?, event_place_address_id?, linked_entity_type?, linked_entity_id?, notes?, visibility?}`. `pm_event_id` refines in place; `op="retract"` archives it (#322) — both work on this embedded path too. See `docs/API_EVENTS.md` § Observation `events` surface for `pm_event_id`/`op` semantics. |

**Person identifier types:** the registered set is a live catalog — query
`GET /api/v1/entity-identifier-types` (filter `entity_type == "person"` client-side)
rather than hardcoding slugs, since admin curates the table. Value conventions per
slug — `person_wa_legislature_roster`'s `<name fold>:<first session year>` and the
rest — are in [OBSERVATIONS.md](OBSERVATIONS.md) §"Identifier types", the single home
for them; the catalog carries no value-format column.

**Disposition semantics:**

| Disposition | Condition |
|-------------|-----------|
| `new` | Identifier not seen before; person created |
| `auto-attached` | Identifier already known; existing entity returned |
| `rejected` | Unknown identifier type; identifier belongs to a non-person entity (`entity_type_mismatch: '<type>'`); identifier belongs to an archived person (`person_archived: '<id>'`, #481); an unknown identifier whose entity type cannot be created (`entity_type_not_creatable: 'role_assignment'`, #604); a `role_assignments` item naming an unknown or archived role (`role_not_found: '<id>'`, #602 — the check `POST /assignments/observations` makes); an internal type in `additional_identifiers`; an `additional_identifiers` type for another entity type (`identifier_type_mismatch: '<slug>'`, #617); a conflicting `additional_identifiers` value (`identifier_conflict: '<slug>'`); a site ULID another person holds (`identifier_taken: '<slug>' held by '<id>'`, #812); a locked type's new value on a usa-wa person (`identifier_locked: '<slug>'`, #710); DB constraint violation. A human-readable `reason` string is always present on rejected responses, and **nothing is written** — see `docs/CONVENTIONS.md` § "Observation atomicity (#456)". |

#### Name observations — results, citations, retract (#845)

`src/core/person_name_observations.py` is the person-name half of `POST /people/observations`. `write_names` still decides what a claim inserts and promotes; this module reports each claim, attaches a claim's own citations, and lets a key take back a name it added. Embedded only, so **all-or-nothing**: a rejected retract or name citation rejects the whole observation.

**Per-name results.** `ObservationResponse.names[]`, one per name in request order (`null` when none were sent, and on `rejected`). `name_id` is the `person_name` entity of the citations API (`docs/PUBLIC_API.md`):

| `disposition` | Meaning | `name_id` |
|---|---|---|
| `new` | the name was added | the new row |
| `auto-attached` | the person already held it | the held row |
| `retracted` | `op="retract"` | the deleted row; null when no public row of that identity was there (nothing removed) |
| `not-public` | accepted, but the row is not public | null |

**What a non-public row reports.** A claim can resolve to a non-public row: a `deadname` (forced `legal_only` on insert), or a name a curator hid. It answers `not-public`: no id, no citation results. For a `deadname` that is the answer whether the row was new or already held, so the response does not say which. For any other type a new row lands public, so `not-public` **does** tell the caller that the person already holds that exact name, non-publicly. The dedup probe is visibility-blind, so that inference predates #845 (observe, then see whether the name appears in a read); it is now one call. It takes `observations:write` and a correct guess of name and type.

**Citations on a name.** `names[].citations` takes the usual citation items (`field_name`: `name` or omitted) and applies them to the row the claim resolved to — new, already held, or not public — under `observations:write`, as an observation's own `citations[]` are. A rejected one (`citable_field_unknown`, …) rejects the observation.

**Retract** deletes one row. It follows the other retractable paths (`docs/OBSERVATIONS.md`) where a name allows, and differs in three places:

| | Assignments, events, citations, relationships | Names |
|---|---|---|
| Addressed by | the row's `pm_*_id` | the identity `(name, name_type, locale, script)` on the addressed person — the dedup key, and exact |
| Provenance gate | same key, or a NULL `source_key_id` | **same key only**: every curated, imported and dataset-applied name has a NULL stamp, so same-or-NULL would let any `observations:write` key delete them |
| Effect | archive (`archived_at`) | **delete** the row and its citations, as the admin delete does — `person_names` has no archive state |

- So there is **no anti-resurrection**: a later observe adds the name again.
- **A public row only.** A row a curator hid is theirs — hiding is the durable "no", since the dedup probe is visibility-blind and a re-send stays a no-op. Deleting the hidden row would forget that. No public row of the identity (a re-emitted retract, a row a curator retyped or hid) is a quiet no-op: `retracted`, null `name_id`.
- Every field on a retract item but the identity is ignored. One identity both observed and retracted in a request is a 422.
- **Retracts are applied first**, then the observes and the heal, whatever the list order. A retract is judged against the person as the request found them: the heal cannot promote the row about to go, and a name added alongside does not lift `name_is_last`.

| Reason | When |
|---|---|
| `name_provenance_conflict` | the public row was written by another key, or carries no stamp |
| `name_is_canonical` | it is the displayed name, however it became so: a curator promoted it, a hint claimed the slot, or PM auto-promoted a name the key added to a person who had none. Also the answer when a promote commits while the retract runs |
| `name_is_last` | it is the person's only **public** name — the guard answers from what a read shows, so a hidden name neither permits the delete nor is revealed by it |
| `name_has_foreign_citations` | a citation on it was written by another key or a curator (a retracted one counts): the citations are deleted with the row, and those are not the caller's |
| `name_has_readings` | a `reading_of_id` child points at it, of any visibility; the `ON DELETE CASCADE` would take rows the key may not have written. This one can reveal that a hidden reading exists — the alternative is deleting a curator's row unseen |

- **The very key that added it.** A replacement key cannot retract its predecessor's names, and `source_key_id` is `ON DELETE SET NULL`, so deleting a key leaves its names unretractable through the API.
- Two public rows of one identity can exist (no unique index; a merge or a race). A retract deletes the caller's; the name still reads, and a re-sent retract answers `name_provenance_conflict`, because what remains is another writer's.

Not built: a retract for org names, and a native (partial-success) transport.

**When to include `display_label`:** Add a label when the contact method serves a specific named function — e.g. `"Scheduler"`, `"Committee Office"`, `"Main Switchboard"`. Omit it for generic personal numbers where the value alone is self-explanatory.
