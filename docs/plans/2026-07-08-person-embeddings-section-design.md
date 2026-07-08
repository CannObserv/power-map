# Voice Embeddings Section on Person Detail — Design

**Issue:** #284
**Date:** 2026-07-08
**Status:** Approved

## Goal

Surface a person's stored voice embeddings in the admin Person detail view. Today
embeddings are only reachable through the public REST API
([`src/api/public/embeddings.py`](../../src/api/public/embeddings.py)); operators
have no visibility into them from the dashboard. Add a **read-only** section that
lists embeddings with copy / archive / restore / hard-delete actions.

## Approved approach

A new server-rendered `<section>` on the Person detail page, backed by a dedicated
admin module, aggregating rows across every registered embedding model.

### Data & queries

- Embeddings live in **per-model tables** (e.g.
  `person_embeddings_pyannote_community_1_embed`), registered in
  `embedding_model_registry` and loaded at startup into
  `app.state.embedding_registry`. Only one model exists today.
- The section **aggregates across all registered models**: loop
  `registry.all()`, run one SELECT per model table, tag each row with its
  `model_id`, merge, and sort by `created_at DESC`.
- The list query fetches only a **preview** of the vector
  (`left(embedding::text, 10)` → e.g. `[0.01234...`) — never the full 256-float
  vector in the page, keeping the DOM light.
- Selected columns: `id, source_service, source_job_id, source_segment,
  recorded_at, created_at, archived_at`.
- Table names come **only from the registry** (never user input) — same
  injection-safe pattern the public API uses.

### UI

New `<section class="entity-section">` "Voice Embeddings" in
`src/templates/admin/people/detail.html`.

Columns: **Model** · **Vector** (preview) · **Source**
(`service · job_id · segment`) · **Created** (date/time) · **Actions**.

- Active rows shown by default. A **"Show archived (N)"** toggle via a
  `?show_archived_embeddings=1` query param reveals archived rows (mirrors the
  Names section's `show_historical` full-page-reload pattern — no new HTMX
  section-swap machinery).
- **Active rows:** Copy + Delete (soft-archive).
- **Archived rows:** rendered dimmed, with Restore + Delete permanently.
- Empty state: "No embeddings".

### Routes — new module `src/api/admin/people_embeddings.py`

All routes: `Depends(get_admin_user)` + `Depends(get_db)`. `model_id` in the path
→ registry lookup → 404 if unknown.

- `GET  /admin/people/{pid}/embeddings/{model_id}/{eid}/vector/`
  → `PlainTextResponse` with the full vector literal. The Copy button fetches
  this then writes to the clipboard (avoids embedding 256 floats × N rows in the
  DOM).
- `DELETE /admin/people/{pid}/embeddings/{model_id}/{eid}/`
  → soft-archive (set `archived_at`). **409 if already archived.**
- `POST /admin/people/{pid}/embeddings/{model_id}/{eid}/restore/`
  → clear `archived_at`. **409 if already active.**
- `DELETE /admin/people/{pid}/embeddings/{model_id}/{eid}/permanent/`
  → hard delete. **Requires archived first — 409 if the row is still active**
  (mirrors the person danger-zone hard-delete gate).

Section data is loaded in the existing `person_detail` handler
(`src/api/admin/people.py`) and rendered server-side like the Identifiers
section. New partials: `partials/_embedding_row.html` (+ `_embedding_rows.html`
if warranted). Copy JS ships as a small static file loaded site-wide from
`base.html` (per the #237 hx-boost note — boosted responses strip `<head>`).

Mutation routes use `flash_trigger(...)` and `markupsafe.escape()` on any
DB-derived values, per admin conventions.

## Key decisions & rationale

| Decision | Rationale |
|---|---|
| **No manual create / paste-in** | The row has NOT-NULL provenance (`activity_ms`, `audio_sample_rate_hz`, `source_service`/`job_id`/`segment`, `recorded_at`) and `created_by_key_id` (FK → `api_keys`). Admin auth has no api_key, and synthesizing provenance for a pasted vector is dishonest. Dropped. |
| **Aggregate all registered models** | Future-proof: correct the moment a second model is registered. Cost is one extra query per registered table (one today). |
| **Vector preview, full vector on demand** | Rendering 256 floats × N rows bloats the page. Preview column + a fetch-on-copy endpoint keeps it light. |
| **Soft-archive on delete, hard-delete gated behind archived** | Matches the project-wide archive model and the public API's soft-delete; hard delete requires an already-archived row (409 otherwise). |
| **Copy = full vector literal** | The truncated column is a preview; operators need the whole vector to paste into external tools. |
| **`?show_archived_embeddings=1` full-page toggle** | Reuses the established Names `show_historical` pattern rather than introducing new HTMX section-swap plumbing. |

## Testing (TDD)

`tests/api/admin/test_people_embeddings.py` — integration (`db_pool` fixture;
seeds an api_key + person + embedding row):

- Section lists active rows only by default.
- `?show_archived_embeddings=1` reveals archived rows (dimmed).
- Delete soft-archives; second delete → 409.
- Restore clears `archived_at`; restore of an active row → 409.
- Hard delete of an archived row removes it; hard delete of an active row → 409.
- Copy endpoint returns the full vector literal.
- Unknown `model_id` → 404.
- Admin-auth redirect when exe.dev headers absent.

## Out of scope

- Manual create / paste-in of embeddings.
- Editing / patching embedding metadata.
- Similarity search (`identify`).

## Docs

Update `docs/STYLE.md` §32 (admin section note) alongside the implementation.
