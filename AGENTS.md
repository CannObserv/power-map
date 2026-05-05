# power-map — Agent Guidelines

Be terse. Prefer fragments over full sentences. Skip filler and preamble. Sacrifice grammar for density. Lead with the answer or action.

## Project Overview

Web service for mapping political and corporate power: people, organizations, roles, and their temporal relationships.

## Development Methodology

TDD required. Red → Green → Refactor. No production code without a failing test first.

## Environment & Tooling

Python ≥3.12, uv, pytest, ruff; Node ≥22, npm, vitest + ESLint + Prettier (JS only); pre-commit (git hooks)

## Code Exploration Policy

SocratiCode tools are deferred — schemas aren't loaded at session start. Call `ToolSearch` with this prefetch query before using any `codebase_*` tool: `select:mcp__plugin_socraticode_socraticode__codebase_search,mcp__plugin_socraticode_socraticode__codebase_symbol,mcp__plugin_socraticode_socraticode__codebase_symbols,mcp__plugin_socraticode_socraticode__codebase_flow,mcp__plugin_socraticode_socraticode__codebase_impact,mcp__plugin_socraticode_socraticode__codebase_graph_query,mcp__plugin_socraticode_socraticode__codebase_graph_circular,mcp__plugin_socraticode_socraticode__codebase_graph_stats,mcp__plugin_socraticode_socraticode__codebase_graph_visualize,mcp__plugin_socraticode_socraticode__codebase_status,mcp__plugin_socraticode_socraticode__codebase_context,mcp__plugin_socraticode_socraticode__codebase_context_search`

**Negative rule.** Broad semantic questions (feature location, architecture, what-uses-what) → SocratiCode. `grep`/`ripgrep` → exact string matches only. Explore subagent → path-pattern file walks only, not semantic search.

| Goal | Tool |
|------|------|
| Understand feature location | `codebase_search` (broad query) |
| Find specific function/type | `codebase_search` (exact name) |
| See imports and dependents | `codebase_graph_query` |
| What breaks if I change X? | `codebase_impact` |
| What does entry point do? | `codebase_flow` |
| Who calls function X? | `codebase_symbol` |
| List symbols in a file | `codebase_symbols` |
| Spot circular dependencies | `codebase_graph_circular` |
| Quantify coupling / structural issues | `codebase_graph_stats` |
| Visualize structure | `codebase_graph_visualize` |
| Verify index status | `codebase_status` |
| Surface available knowledge artifacts | `codebase_context` |
| Find schemas, endpoint contracts, infra configs | `codebase_context_search` |

## Project Layout

```
src/api/        — FastAPI app (ASGI, routes, auth, schemas)
  admin/        — Jinja2 + HTMX admin dashboard
  public/       — JSON API (X-API-Key auth, server-to-server)
src/core/       — Shared domain logic (db, schema.sql, normalizers, ingestion)
src/static/     — Static assets; vendor/ is SHA-pinned and excluded from linting
tests/          — Mirrors src/ structure; js/ for Vitest
docs/           — Reference docs (COMMANDS, STYLE, CONVENTIONS, SKILLS)
scripts/        — One-off operational scripts
infra/          — systemd unit file
```

## Admin Dashboard Key Rules

Full conventions → `docs/STYLE.md §32`

- Auth: `user: AdminUser = Depends(get_admin_user)` on every route — raises `HTTPException(307)` redirect when exe.dev headers absent
- Archive model: `archived_at TIMESTAMPTZ` — NULL = active, non-NULL = archived; hard delete requires archived (409 otherwise); archive/unarchive both return 409 if already in that state
- HTMX partials: `is_htmx(request)` from `src.api.admin.deps` (checks `HX-Request and not HX-Boosted`); always include `RedirectResponse` fallback
- Flash: `flash_trigger(level, body, extra=None)` on mutation routes; always `markupsafe.escape()` DB-derived values
- Dup counts: call `invalidate_dup_count_cache()` (from `org_dups` or `people_dups`) after any merge or dismiss

## Public API Key Rules

Full conventions → `docs/CONVENTIONS.md`

- Auth: `X-API-Key` → `require_api_key` dep; 403 on missing, 401 on invalid
- All routes: Pydantic `response_model` + `operation_id`; no `dict[str, Any]` returns
- Lists: `{"data": [...], "meta": {"limit", "offset", "count", "has_more"}}`; fetch `limit+1` rows for `has_more`
- Timestamps: `_fmt_ts()` from `schemas.py`; ISO 8601 with `Z` suffix

## DB Key Rules

Full conventions → `docs/CONVENTIONS.md`

- PKs: ULIDs via `generate_id()` from `src.core.db`
- `updated_at`: maintained by DB triggers — never set manually
- Display names: always use `v_org_display_names` / `v_person_display_names` views; never join name tables directly for display
- Person-name visibility rule (deadnames, hidden, legal-only): see `docs/CONVENTIONS.md` §"Person names — i18n & cultural awareness".
- Raw `person_names` access: AND-append `visibility='public'` or call `visible_names_filter()` from `src.core.db`. Lint enforces.
- Structured name parts: `person_name_parts` is a 1:0..1 sidecar to `person_names` (ON DELETE CASCADE). Never auto-parsed — populated only when an upstream source supplies structure. Edited via `/admin/people/{pid}/names/{nid}/parts/`. See `docs/CONVENTIONS.md` §"Structured parts (`person_name_parts` sidecar)".
- BCP 47 / ISO 15924 lookup tables (`bcp47_locales`, `iso15924_scripts`) are FK-validated on `person_names.locale` / `.script`. After `apply_schema` on a fresh DB, seed via `uv run --group seed scripts/seed_locales_scripts.py` (see `docs/COMMANDS.md`); `apply_schema` logs a WARNING when either table is empty.
- Integration tests: require `TEST_DATABASE_URL`; never run against the production DB

## Infrastructure

Single VM; port split:

| Port | Process | Managed by |
|---|---|---|
| 8000 | Production API (`--workers 2`) | systemd (`power-map.service`) |
| 8001 | Dev server (`--reload`) | manual, always from a worktree |

**All development work must be done in a git worktree** — never edit the main checkout directly. `brainstorming` is the entry point that triggers worktree setup via `using-git-worktrees`. After teardown, run `git worktree prune`.

exe.dev proxy: dev server at `https://power-map.exe.xyz:8001/`.

| Situation | Action |
|---|---|
| After code change (production) | `sudo systemctl restart power-map` |
| Worktree dev testing | kill+restart dev server on 8001 with `--reload` from worktree dir |
| Service debugging | `sudo journalctl -u power-map -f` |

Full command reference: `docs/COMMANDS.md`

### Environment Variables

| File | Owner | Contents |
|---|---|---|
| `/etc/power-map/.env` | root:exedev (640) | `DATABASE_URL`, `ADDRESS_VALIDATOR_API_KEY`, `ADDRESS_VALIDATOR_RUN_VALIDATION` |
| `.env` (repo, gitignored) | developer | `GH_TOKEN`, `TEST_DATABASE_URL` |

Load both: `export $(cat /etc/power-map/.env | xargs) 2>/dev/null && export $(cat .env | xargs) 2>/dev/null`

## Agent Skills & Tools

Skills in `skills/` (agentskills.io) and `.claude/skills/` (Claude Code). Reference: `docs/SKILLS.md`

### SocratiCode

See **Code Exploration Policy** above.

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

**Version bumps:** update `pyproject.toml` and `package.json` together — the `check-version-sync` pre-commit hook enforces this.

**General:**
- No inline module imports; all at file top
- Docstrings for public modules, classes, functions
- Test structure mirrors source (`src/foo.py` → `tests/test_foo.py`)
- Explicit imports only
- Small, focused functions
