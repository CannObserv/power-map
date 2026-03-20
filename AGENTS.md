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
    deps.py     — AdminUser dataclass, get_admin_user (exe.dev auth), check_auth helper, get_db
    router.py   — Mounts all admin sub-routers under /admin/
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

### DB conventions
- All PKs are ULIDs; generate with `generate_id()` from `src.core.db`
- `apply_schema(conn)` is idempotent (`IF NOT EXISTS` / `ON CONFLICT DO NOTHING`); wraps in a transaction
- `updated_at` is maintained automatically by DB triggers — never set it manually in application code
- Phone: normalize to E.164 via `PhoneNormalizer` from `src.core.normalizers.phone`
- Email: validate via `EmailNormalizer` from `src.core.normalizers.email`
- Integration tests (marked `integration`) require `DATABASE_URL` env var pointing to a dedicated test DB

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
