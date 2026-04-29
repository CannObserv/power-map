# power-map — Agent Guidelines

Be terse. Prefer fragments over full sentences. Skip filler and preamble. Sacrifice grammar for density. Lead with the answer or action.

## Project Overview

Web service for mapping political and corporate power: people, organizations, roles, and their temporal relationships.

## Development Methodology

TDD required. Red → Green → Refactor. No production code without a failing test first.

## Environment & Tooling

Python ≥3.12, uv, pytest, ruff; Node ≥22, npm, vitest + ESLint + Prettier (JS only); pre-commit (git hooks)

## Project Layout

```
src/api/        — FastAPI app (ASGI, routes, auth, schemas)
  deps.py       — Shared FastAPI deps (get_db); imported by both admin and public sub-packages
  admin/        — Jinja2 + HTMX admin dashboard (entities, people, orgs, roles, role_assignments, settings, imports)
    deps.py     — AdminUser dataclass, get_admin_user (exe.dev auth, raises HTTPException 307), re-exports get_db, is_htmx, flash_trigger, resolve_query_flash, escape_like, org_header_extra, person_header_extra
    org_dups.py    — Org-duplicate detection: CANDIDATE_WHERE SQL, TTL cache, count_org_duplicates, get_org_dup_count dep, invalidate_dup_count_cache
    people_dups.py — People-duplicate detection: CANDIDATE_WHERE SQL, TTL cache, count_person_duplicates, get_person_dup_count dep, invalidate_dup_count_cache
    dashboard.py        — Admin dashboard landing page (record counts, dup badge counts)
    people.py   — Person list, create (form.html), detail, inline notes/pronouns, archive/unarchive/delete, search typeahead
    people_merge.py     — Person merge, dismiss-duplicate, duplicates review
    people_names.py     — Inline CRUD for person_names (row-level HTMX swap); last-identity guard on delete; canonical edit guard (rejects un-canonicalizing the sole canonical name); auto-promote sole name; emits updatePersonHeader on mutations
    people_contacts.py  — Inline CRUD for contact_methods (row-level HTMX swap); delegates to _contacts_shared factory
    people_addresses.py — Inline CRUD for addresses + entity_addresses (row-level HTMX swap); normalizer confirm flow; country-format endpoint
    people_links.py     — Inline CRUD for links + link_types (row-level HTMX swap); delegates to _links_shared factory
    people_identifiers.py — Inline CRUD for identifiers filtered to entity_type='person' (row-level HTMX swap); delegates to _identifiers_shared factory
    people_assignments.py — Inline CRUD for person → role_assignments (row-level HTMX swap); archive-only (no hard delete inline)
    role_assignments.py — Role assignment list, create, detail, archive, delete; inline editing for is_current/dates/notes (row-level HTMX swap)
    router.py   — Mounts all admin sub-routers under /admin/
    orgs.py     — Org list, detail, search typeahead, inline active/notes/parent editing, children CRUD, archive/unarchive/delete
    orgs_merge.py       — Org merge (duplicates-review flow + manual merge-with from detail page), dismiss-duplicate, duplicates review; merge-search/merge-target-search/merge-preview modal routes; shared _execute_merge helper
    orgs_names.py       — Inline CRUD for organization_names (row-level HTMX swap); last-identity guard on delete; canonical edit guard (rejects un-canonicalizing the sole canonical name); auto-promote sole name; emits updateOrgHeader on mutations
    orgs_acronyms.py    — Inline CRUD for organization_acronyms (row-level HTMX swap)
    orgs_addresses.py   — Inline CRUD for addresses + entity_addresses (row-level HTMX swap)
    orgs_contacts.py    — Inline CRUD for contact_methods (row-level HTMX swap); delegates to _contacts_shared factory
    orgs_links.py       — Inline CRUD for links + link_types (row-level HTMX swap); delegates to _links_shared factory
    orgs_identifiers.py — Inline CRUD for identifiers (row-level HTMX swap); delegates to _identifiers_shared factory
    orgs_roles.py       — Inline role create and merge on org detail (new-row GET, create POST, merge POST)
    roles_shared.py     — Shared helpers: _get_role (fetch + 404), _parse_date, _check_assignment_within_bounds; imported by roles_detail and roles_assignments_inline to avoid circular deps
    roles_detail.py     — Inline editing for role detail fields: org, title, notes, boundary dates (established_on / abolished_on)
    roles_assignments_inline.py — Inline assignment CRUD on role detail (create/read-row/edit-row/archive); archive-only (no hard delete inline); fetch_role_assignments helper
    entities.py         — Entities landing page (card-grid overview with record counts); templates in src/templates/admin/entities/
    settings.py         — Settings landing page; templates in src/templates/admin/settings/
    settings_link_types.py      — Inline CRUD for link_types (row-level HTMX swap)
    settings_identifier_types.py — Inline CRUD for entity_identifier_types (row-level HTMX swap)
    activity.py         — Activity landing page (card-grid overview of import batches); templates in src/templates/admin/activity/
    _contacts_shared.py   — Factory make_contacts_router(entity_type, ...) — shared CRUD logic for org and person contact methods; email/phone normalization with inline error re-render on validation failure
    _links_shared.py      — Factory make_links_router(entity_type, ...) — shared CRUD logic for org and person links
    _identifiers_shared.py — Factory make_identifiers_router(entity_type, ...) — shared CRUD logic for org and person identifiers
    _names_shared.py      — Factory make_names_router(...) — shared CRUD logic for org and person names; accepts entity-specific promote/guard/header callbacks
  public/       — JSON API (X-API-Key auth, server-to-server; no HTMX)
    deps.py     — require_api_key dep: validates X-API-Key header, updates last_used_at; returns 403 (missing) or 401 (invalid)
    orgs.py     — GET /api/v1/orgs/search?q= (name/acronym/variant search, include_archived, limit≤50, offset); GET /api/v1/orgs/{id} (full record: names[], acronyms[], identifiers[]); slug derived as lower(canonical_acronym)
    router.py   — Mounts public sub-routers under /api/v1/; health check at GET /api/v1/
    schemas.py  — Pydantic response models: OrgSearchResult, OrgSearchMeta, OrgSearchResponse, OrgDetail, OrgName, OrgAcronym, OrgIdentifier
src/core/       — Shared domain logic
  db.py         — Connection pool, apply_schema, generate_id
  schema.sql    — Canonical DDL (tables, indexes, triggers, seed data); source of truth
  normalizers/  — Field normalizers: phone, email, url, identifier, address
  ingestion/    — EVTL pipeline: base types, CSV sources (org/person/role), pipeline coordinator
src/static/admin/vendor/ — Vendored third-party JS (htmx, etc.); pinned by SHA-256 in tests/api/admin/test_js.py; excluded from ESLint/Prettier and marked linguist-vendored
tests/          — Mirrors src/ structure
  js/           — Vitest behavioral tests for admin JS (role-merge.js, …)
docs/           — Reference docs (API, COMMANDS, SKILLS)
scripts/        — One-off operational scripts (import_cannabis_observer.py, deduplicate_roles.py)
```

### Admin dashboard conventions
- Auth: exe.dev proxy injects `X-ExeDev-UserID` + `X-ExeDev-Email` headers; missing headers → redirect to `/__exe.dev/login?redirect=<url-encoded path+query>`
- Archive model: `archived_at TIMESTAMPTZ` — NULL = active, non-NULL = archived.
  - Hard delete gated on `archived_at IS NOT NULL` (returns 409 if not archived).
  - Unarchive via `POST /{id}/unarchive/` sets `archived_at = NULL` and preserves prior `active` state (returns 409 if not archived). Redirects to detail page with `?flash=unarchived` for all entity types (orgs, people, role-assignments).
  - Archive is strictly guarded across all entity types — returns 409 if already archived (orgs, people, roles, role-assignments including both inline routes on role/person detail and the RA detail page).
  - Flash on detail pages: `org_detail`, `person_detail`, and `ra_detail` all accept a `?flash=` query param and render via `resolve_query_flash`. Add new flash keys to the module-level `_FLASH_MESSAGES` dict in each module.
- Auth dependency: `user: AdminUser = Depends(get_admin_user)` on every route handler — `get_admin_user` raises `HTTPException(307)` with `Location: /__exe.dev/login?redirect=<url>` when headers are absent; FastAPI propagates the redirect automatically.
- HTMX partial responses: use `is_htmx(request)` from `src.api.admin.deps` to select partial templates — checks `HX-Request and not HX-Boosted` (boost sends both; omitting the guard causes boosted sidebar nav to receive bare fragments instead of full page layouts).
- hx-boost re-execution: HTMX re-runs all `<script src>` tags found in `<body>` on every boosted navigation (via its `executeScripts` mechanism). Scripts with persistent `document.addEventListener` calls must live in `<head>` (`admin-modal.js`, `flash.js`, `dark-mode.js`). For unavoidable inline body scripts, use the remove/re-assign/add guard pattern: `document.removeEventListener(evt, document.__pmKey); document.__pmKey = fn; document.addEventListener(evt, document.__pmKey)` — see `base.html` aria-busy and `__pmNavKeydown` as examples.
- Flash notifications: use `flash_trigger(level, body, extra=None)` from `src.api.admin.deps` on HTMX mutation routes — sets `HX-Trigger: {"showFlash": {...}}` response header; `flash.js` listener injects the flash into `#flash-region`. Pass as `headers=flash_trigger(level, body)` to `TemplateResponse`. For inline (non-HTMX) flash, use `message(level, body)` from `admin/macros/flash.html`. Levels: `success`, `info`, `warning`, `error`. Always escape DB-derived values with `markupsafe.escape()` before interpolating into `body` HTML strings. Pass `extra` to co-emit additional HX-Trigger events in the same header (all keys merged into one JSON object): `flash_trigger("success", "Saved.", extra={"myEvent": {...}})`.
- Page header sync: on any HTMX mutation route that may change the org's canonical name or canonical acronym, pass `extra=await org_header_extra(org_id, db)` to `flash_trigger`. `org_header_extra` (from `src.api.admin.deps`) queries `v_org_display_names` and returns `{"updateOrgHeader": {"display": ...}}`. The `org-detail.js` listener on the detail page handles this event and updates `#page-heading` (`<h1>`), `#breadcrumb-current`, and `document.title` in-place.
- Page-specific head scripts: use `{% block extra_head %}{% endblock %}` (defined in `base.html`) to inject a `<script src defer>` tag from a detail template. Scripts in this block live in `<head>` so hx-boost never re-executes them; using `defer` ensures they run after DOM parse and HTMX is available. Do not put inline `<script>` blocks here — extract to a file in `src/static/admin/` instead.
- Mutation routes returning HTMX partials: preserve a non-HTMX `RedirectResponse` fallback for graceful degradation (e.g. direct form POST without JS).
- Dup count cache: `count_org_duplicates(db)` in `src.api.admin.org_dups` and `count_person_duplicates(db)` in `src.api.admin.people_dups` are TTL-cached (5 min, process-local). Call `invalidate_dup_count_cache()` from the appropriate module after any merge or dismiss to keep counts accurate. All people and org routes inject both `org_dup_count` and `person_dup_count` via their respective deps — the dashboard calls the count functions directly (FastAPI dep resolution runs before auth). Sidebar badges use these template vars directly (no HTMX XHR). **Caveat:** cache is not shared across gunicorn workers — counts may lag by up to 5 min per worker under multi-process deployments.

### Public API conventions
- Auth: `X-API-Key` header → `require_api_key` dep (in `src.api.public.deps`); 403 on missing, 401 on invalid; updates `api_keys.last_used_at`.
- Versioning: path-based (`/api/v1/`). Bump the prefix when introducing breaking changes.
- Response models: all public routes must declare a Pydantic `response_model` (in `schemas.py`) and an explicit `operation_id`. `dict[str, Any]` return types are not allowed — OpenAPI schema must be typed.
- List endpoints: return `{"data": [...], "meta": {"limit", "offset", "count", "has_more"}}`. Fetch `limit + 1` rows to compute `has_more` without a `COUNT(*)` query; return only `limit` rows in `data`.
- Single-resource endpoints: return the resource object directly (no envelope).
- Timestamps: serialize with `_fmt_ts()` from `schemas.py` — ISO 8601 with `Z` suffix. Raw `datetime` objects are passed through to Pydantic models; the `field_serializer` on `OrgSearchResult.archived_at` formats them.
- CORS: not required — the public API is server-to-server only (no browser callers).

### DB conventions
- All PKs are ULIDs; generate with `generate_id()` from `src.core.db`
- `apply_schema(conn)` is idempotent (`IF NOT EXISTS` / `ON CONFLICT DO NOTHING`); wraps in a transaction
- `updated_at` is maintained automatically by DB triggers — never set it manually in application code
- Phone: normalize to E.164 via `PhoneNormalizer` from `src.core.normalizers.phone`
- Email: validate via `EmailNormalizer` from `src.core.normalizers.email`
- Integration tests (marked `integration`) require `TEST_DATABASE_URL` env var; `tests/conftest.py` redirects `DATABASE_URL` → `TEST_DATABASE_URL` and skips all integration tests when `TEST_DATABASE_URL` is absent — tests will never run against the production DB
- Org display names: use `v_org_display_names` (view in `schema.sql`) for all admin queries that display an org name — formats as "Name (Acronym)" when a canonical acronym exists, otherwise just "Name". Never join `organization_names` or `organization_acronyms` directly for display; use the view.
- Person display names: use `v_person_display_names` (view in `schema.sql`) for all admin queries that display a person name — returns the canonical `person_names` row. Never join `person_names` directly for display; always go through this view.
- Acronyms are stored in `organization_acronyms` (separate table); `organization_names` holds legal/dba/former names only. Each table has exactly one canonical row per org via a partial unique index.
- Auto-promote invariant: every **delete** route on `organization_names` must call `_maybe_promote_sole_name(org_id, db)` inside its transaction (from `src.api.admin.orgs_names`). It promotes the sole remaining non-canonical name to canonical, keeping `v_org_display_names.display_name` non-NULL. Edit routes do not need this call — the canonical edit guard (§28a) prevents the route from completing when it would leave zero canonical names, so auto-promote is never needed. The equivalent for acronyms is `_maybe_promote_sole_acronym(org_id, db)` (from `src.api.admin.orgs_acronyms`) — call it inside the transaction of every **delete** route on `organization_acronyms`.
- Last-identity guard: `name_delete` blocks when the org has exactly one name and no canonical acronym; `acronym_delete` blocks symmetrically. HTMX: return HTTP 200 with `flash_trigger("error", ...)` and empty body. Non-HTMX (curl/API only — browsers can't send DELETE without JS): raise `HTTPException(409)`.
- Links: `link_types` table (slug, display_name, is_social) replaces the old `url_types` + `platforms` tables. `links` table (entity_type, entity_id, url, link_type_id, is_active) replaces `urls` + `social_links`. Social links: `JOIN link_types WHERE is_social = TRUE`.
- Row-level HTMX editing pattern: GET `/{id}/edit-row/` → edit form partial; POST `/{id}/edit-row/` → read partial (hx-swap="outerHTML"); GET `/{id}/read-row/` → read partial (Cancel on edit form, hx-swap="outerHTML"); GET `/new-row/` → blank form row (Cancel: `onclick="this.closest('tr').remove()"`, no server round-trip). → `docs/STYLE.md §15` (entity card subsection), `§20` (toggle in form rows).
- Row form input styling: inputs inside table form rows must be wrapped in `<div class="form-group" style="margin-bottom:0">` to activate `.form-group input` CSS rules. Use `flex:1` on the primary input, `margin-left:auto` on the button group to right-align Save/Cancel.
- Auto-saving toggle pattern: put `hx-post` directly on `<input type="checkbox">` with `hx-include="this"`. Unchecked = no value submitted = `Form("")` = falsy; checked = `value="true"` = `True`. Always add `disabled` when `entity.archived_at IS NOT NULL` — archiving is a separate action; the toggle is for active/inactive only. See `_active_toggle.html` as canonical example. → `docs/STYLE.md §15` (auto-save), `§20` (non-auto-save form-row variant).
- Notes inline edit pattern: GET `/inline/notes/` → read partial; GET `/inline/notes/edit/` → form partial; POST `/inline/notes/` → read partial. Both target `id="notes-field"`. Form partial: `<form>` wraps the whole partial; header row (`display:flex; justify-content:space-between`) holds `<label for="notes-textarea">` (not `<h3>`) on the left and Save/Cancel buttons on the right — same structure as the read partial's `[Notes h3] [Edit]` header, so no layout shift on toggle. No `form-actions` div. Save `notes.strip() or None` (whitespace → NULL). → `docs/STYLE.md §15` for full pattern.
- Contact form inline errors: on validation failure, re-render the form partial with HTTP 200, passing `error` (user-facing message string) and `value_input` (raw submitted value for repopulation). Use `{% if error %}<div class="alert alert--error" role="alert">{{ error }}</div>{% endif %}` at the top of the form. HTTP 200 is required — HTMX only swaps on 2xx by default. Non-HTMX path: redirect to the entity detail page.
- Child org search uses a scoped endpoint `GET /{org_id}/children/search/?q=...` (not the generic `/search/`) — excludes the current org and orgs already linked as children (`parent_id = org_id`). Use the scoped endpoint for any context where existing relationships must be filtered from typeahead results. → `docs/STYLE.md §18` for the full typeahead/combobox pattern.
- Subsection re-sort after create: when a create route must return sorted table content (not just the new row), return a `_*_rows.html` tbody-replacement partial and target `#{table-id} tbody` with `hx-swap="innerHTML"`. Non-HTMX path still uses `RedirectResponse` (full page reload preserves sort).
- HTMX attribute inheritance: `hx-swap`, `hx-target`, and other HTMX attributes inherit from parent elements unless overridden. Search inputs inside a `<form>` that carries `hx-swap="outerHTML"` will inherit that swap and replace their target element entirely rather than updating its contents. Always add `hx-swap="innerHTML"` explicitly on typeahead search inputs to override the form's swap.

### Ingestion conventions
- EVTL pattern: Extract (CSV read) → Validate (Pydantic) → Transform (normalize fields) → Load (DB insert)
- `RowResult` envelope: `errors` = fatal (entity skipped), `warnings` = non-fatal (field skipped)
- `field_confidence` is append-only; query latest assessment with `ORDER BY assessed_at DESC LIMIT 1`
- `import_batches.file_hash` is unique; re-running with the same files reuses the existing batch
- Address standardization uses the external address-validator service when `ADDRESS_VALIDATOR_API_KEY` is set; falls back to local `usaddress` parsing otherwise
- `VALIDATE_ADDRESSES=true` (or `--validate-addresses` CLI flag) enables the `/validate` endpoint
- `role_index` is pre-populated from the DB at pipeline startup (Pass 3) so re-runs are idempotent across batches

### Unique indexes (requires PostgreSQL 15+)
- `uq_role_org_title` — `roles(organization_id, lower(title)) WHERE archived_at IS NULL`
- `uq_role_assignment_person_role_start` — `role_assignments(person_id, role_id, start_date) NULLS NOT DISTINCT WHERE archived_at IS NULL`
- Both indexes are created inside `DO … EXCEPTION WHEN unique_violation` blocks so `apply_schema` is safe even on a DB with existing duplicates (it logs a WARNING instead of raising).
- **Bootstrap sequence for a dirty DB:** (1) run `scripts/deduplicate_roles.py --execute` to collapse duplicates, (2) re-run `apply_schema` (or restart the service) to create the indexes, (3) verify with `\d roles` / `\d role_assignments` in psql.
- Schema tests for `uq_role_org_title` are guarded by a `require_uq_role_org_title` fixture that skips when the index is absent (expected on the production DB until the bootstrap sequence above is run).
- **`pg_trgm` extension:** enabled via `CREATE EXTENSION IF NOT EXISTS pg_trgm` in `apply_schema`. Required for org duplicate detection (`similarity()` function). Re-run `apply_schema` (or restart the service) to install on existing databases. Gracefully degrades to `org_dup_count = 0` if extension is not yet installed.

## Infrastructure

Single VM running both production service and development. Port split prevents collision:

| Port | Process | Managed by |
|---|---|---|
| 8000 | Production API (`--workers 2`) | systemd (`power-map.service`) |
| 8001 | Dev server (`--reload`) | manual, always from a worktree |

exe.dev proxy: dev server accessible at `https://power-map.exe.xyz:8001/`.

**All development work must be done in a git worktree** — never edit the main checkout directly. `brainstorming` is the entry point that triggers worktree setup via `using-git-worktrees`. After teardown, run `git worktree prune` to remove stale entries.

### Server Lifecycle

| Situation | Action |
|---|---|
| After code change (production) | `sudo systemctl restart power-map` |
| Worktree dev testing | kill+restart dev server on 8001 with `--reload` from worktree dir |
| Env var change | restart service (env read at startup) |
| Service debugging | `sudo journalctl -u power-map -f` |
| New deployment | install `infra/power-map.service` → see `docs/COMMANDS.md` |

### Environment Variables

| File | Owner | Contents |
|---|---|---|
| `/etc/power-map/.env` | root:exedev (640) | `DATABASE_URL`, `ADDRESS_VALIDATOR_API_KEY`, `ADDRESS_VALIDATOR_RUN_VALIDATION` |
| `.env` (repo, gitignored) | developer | `GH_TOKEN`, `TEST_DATABASE_URL` |

Load both before running any command that needs env vars:

```bash
export $(cat /etc/power-map/.env | xargs) 2>/dev/null
export $(cat .env | xargs) 2>/dev/null
```

## Services

| Service | Framework | Port |
|---|---|---|
| API | FastAPI | 8000 (prod) / 8001 (dev) |

Production runs under systemd (`infra/power-map.service`). After any code change: `sudo systemctl restart power-map`.

## Common Commands

Full reference: `docs/COMMANDS.md`

## Agent Skills

Skills in `skills/` (agentskills.io) and `.claude/skills/` (Claude Code). Reference: `docs/SKILLS.md`

## Conventions

**Commit Messages:**
```
#<number> [type]: <description>      # with issue
[type]: <description>                # without issue
```
Types: feat, fix, refactor, docs, test, chore

**Logging:**
```python
from src.core.logging import get_logger
logger = get_logger(__name__)
```
Entry points only: call `configure_logging()` once.

**Date & Time:**
- All UTC
- ISO 8601: `YYYY-MM-DDTHH:MM:SS.ffffffZ` (timestamps), `YYYY-MM-DD` (dates)

**General:**
- No inline module imports; all at file top
- Docstrings for public modules, classes, functions
- Test structure mirrors source (`src/foo.py` → `tests/test_foo.py`)
- Explicit imports only
- Small, focused functions
