# power-map — Agent Guidelines

Be terse. Prefer fragments over full sentences. Skip filler and preamble. Sacrifice grammar for density. Lead with the answer or action.

## Project Overview

Web service for mapping political and corporate power: people, organizations, roles, and their temporal relationships.

## Development Methodology

TDD required. Red → Green → Refactor. No production code without a failing test first.

## Environment & Tooling

Python ≥3.12, uv, pytest, ruff

## Project Layout

```
src/api/        — FastAPI app (ASGI, routes, auth, schemas)
  admin/        — Jinja2 + HTMX admin dashboard (people, orgs, roles, role_assignments, lookups, imports)
    deps.py     — AdminUser dataclass, get_admin_user (exe.dev auth), check_auth helper, get_db, is_htmx
    org_dups.py — Org-duplicate detection: CANDIDATE_WHERE SQL, TTL cache, count_org_duplicates, get_org_dup_count dep, invalidate_dup_count_cache
    router.py   — Mounts all admin sub-routers under /admin/
    orgs.py     — Org list, detail, search typeahead, inline active/notes/parent editing, children CRUD, archive/delete
    orgs_names.py       — Inline CRUD for organization_names (row-level HTMX swap)
    orgs_acronyms.py    — Inline CRUD for organization_acronyms (row-level HTMX swap)
    orgs_addresses.py   — Inline CRUD for addresses + entity_addresses (row-level HTMX swap)
    orgs_contacts.py    — Inline CRUD for contact_methods (row-level HTMX swap)
    orgs_links.py       — Inline CRUD for links + link_types (row-level HTMX swap)
    orgs_identifiers.py — Inline CRUD for identifiers (row-level HTMX swap)
src/core/       — Shared domain logic
  db.py         — Connection pool, apply_schema, generate_id
  schema.sql    — Canonical DDL (tables, indexes, triggers, seed data); source of truth
  normalizers/  — Field normalizers: phone, email, url, identifier, address
  ingestion/    — EVTL pipeline: base types, CSV sources (org/person/role), pipeline coordinator
tests/          — Mirrors src/ structure
docs/           — Reference docs (API, COMMANDS, SKILLS)
scripts/        — One-off operational scripts (import_cannabis_observer.py, deduplicate_roles.py)
```

### Admin dashboard conventions
- Auth: exe.dev proxy injects `X-ExeDev-UserID` + `X-ExeDev-Email` headers; missing headers → redirect to `/__exe.dev/login?redirect=<url-encoded path+query>`
- Archive model: `archived_at TIMESTAMPTZ` — NULL = active, non-NULL = archived; hard delete gated on `archived_at IS NOT NULL` (returns 409 if not archived)
- `check_auth(user)` from `src.api.admin.deps` — call at top of every route handler; returns `(redirect_response, user)` tuple
- HTMX partial responses: use `is_htmx(request)` from `src.api.admin.deps` to select partial templates — checks `HX-Request and not HX-Boosted` (boost sends both; omitting the guard causes boosted sidebar nav to receive bare fragments instead of full page layouts).
- Flash notifications: use `admin/macros/flash.html` — `message(level, body)` for inline, `oob(level, body)` for OOB injection into `#flash-region` from HTMX partial responses. Levels: `success`, `info`, `warning`, `error`. Always escape DB-derived values with `markupsafe.escape()` before interpolating into `body` HTML strings passed to these macros.
- Mutation routes returning HTMX partials: preserve a non-HTMX `RedirectResponse` fallback for graceful degradation (e.g. direct form POST without JS).
- Dup count cache: `count_org_duplicates(db)` in `src.api.admin.org_dups` is TTL-cached (5 min, process-local). Call `invalidate_dup_count_cache()` after any merge or dismiss to keep count accurate. All routes (except the dashboard) inject `org_dup_count` via `get_org_dup_count` dep — the dashboard calls `count_org_duplicates(db)` directly inside its `pool.acquire()` block because FastAPI dep resolution runs before the handler body and would raise before auth completes. Sidebar badge uses `org_dup_count` template var directly (no HTMX XHR). **Caveat:** cache is not shared across gunicorn workers — counts may lag by up to 5 min per worker under multi-process deployments.

### DB conventions
- All PKs are ULIDs; generate with `generate_id()` from `src.core.db`
- `apply_schema(conn)` is idempotent (`IF NOT EXISTS` / `ON CONFLICT DO NOTHING`); wraps in a transaction
- `updated_at` is maintained automatically by DB triggers — never set it manually in application code
- Phone: normalize to E.164 via `PhoneNormalizer` from `src.core.normalizers.phone`
- Email: validate via `EmailNormalizer` from `src.core.normalizers.email`
- Integration tests (marked `integration`) require `TEST_DATABASE_URL` env var; `tests/conftest.py` automatically redirects `DATABASE_URL` → `TEST_DATABASE_URL` at session start so tests never touch the production DB when the standard `env` file is loaded
- Org display names: use `v_org_display_names` (view in `schema.sql`) for all admin queries that display an org name — formats as "Name (Acronym)" when a canonical acronym exists, otherwise just "Name". Never join `organization_names` or `organization_acronyms` directly for display; use the view.
- Acronyms are stored in `organization_acronyms` (separate table); `organization_names` holds legal/dba/former names only. Each table has exactly one canonical row per org via a partial unique index.
- Links: `link_types` table (slug, display_name, is_social) replaces the old `url_types` + `platforms` tables. `links` table (entity_type, entity_id, url, link_type_id, is_active, is_canonical) replaces `urls` + `social_links`. Social links: `JOIN link_types WHERE is_social = TRUE`. Unique canonical per entity enforced by `uq_link_canonical` partial index; use a transaction to clear sibling canonicals before setting a new one.
- Row-level HTMX editing pattern: GET `/{id}/edit-row/` → edit form partial; POST `/{id}/edit-row/` → read partial (hx-swap="outerHTML"); GET `/{id}/read-row/` → read partial (Cancel on edit form, hx-swap="outerHTML"); GET `/new-row/` → blank form row (Cancel: `onclick="this.closest('tr').remove()"`, no server round-trip).
- Row form input styling: inputs inside table form rows must be wrapped in `<div class="form-group" style="margin-bottom:0">` to activate `.form-group input` CSS rules. Use `flex:1` on the primary input, `margin-left:auto` on the button group to right-align Save/Cancel.
- Auto-saving toggle pattern: put `hx-post` directly on `<input type="checkbox">` with `hx-include="this"`. Unchecked = no value submitted = `Form("")` = falsy; checked = `value="true"` = `True`. Always add `disabled` when `entity.archived_at IS NOT NULL` — archiving is a separate action; the toggle is for active/inactive only. See `_active_toggle.html` as canonical example.
- Notes inline edit pattern: GET `/inline/notes/` → read partial; GET `/inline/notes/edit/` → form partial; POST `/inline/notes/` → read partial. Both target `id="notes-field"`. Form partial uses `<label for="notes-textarea">` (not `<h3>`) for screen reader association. Save `notes.strip() or None` (whitespace → NULL). See `docs/STYLE.md §15` for full pattern.
- Subsection re-sort after create: when a create route must return sorted table content (not just the new row), return a `_*_rows.html` tbody-replacement partial and target `#{table-id} tbody` with `hx-swap="innerHTML"`. Non-HTMX path still uses `RedirectResponse` (full page reload preserves sort).

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

## Services

| Service | Framework | Port |
|---|---|---|
| API | FastAPI | 8000 |

```bash
# FastAPI dev server
uv run uvicorn src.api.main:app --host 0.0.0.0 --port 8000 --reload
```

After any code change in production deployments, restart uvicorn/gunicorn — they do not auto-reload.

## Secrets

`env` (git-ignored): API keys and tokens. Never commit secrets.

Load before running any command that needs env vars (e.g. `gh`, `DATABASE_URL`):

```bash
export $(cat env | xargs)
```

Currently defined:
- `GH_TOKEN` — GitHub personal access token (used by `gh` CLI)
- `DATABASE_URL` — PostgreSQL DSN

Production secrets (from `/etc/power-map/env`):
- `ADDRESS_VALIDATOR_API_KEY` — required for external address standardization

## Common Commands

```bash
# Install dependencies
uv sync

# Run tests
uv run pytest

# Run linter
uv run ruff check .

# FastAPI dev server
uv run uvicorn src.api.main:app --host 0.0.0.0 --port 8000 --reload
```

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
