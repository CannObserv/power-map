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
docs/           — Reference docs (COMMANDS, STYLE, CONVENTIONS, SKILLS, PUBLIC_API)
                  split by subject since #407 — complete index at the end of this file
scripts/        — One-off operational scripts
infra/          — systemd units (API + prune timer) + terraform
```

## Admin Dashboard Key Rules

Full conventions → `docs/ADMIN.md`
Accessibility rules and their test tiers → `docs/ACCESSIBILITY.md`

- Auth: `user: AdminUser = Depends(get_admin_user)` on every route
- Archive model: `archived_at TIMESTAMPTZ` — NULL = active, non-NULL = archived; hard delete requires archived (409 otherwise)
- Every mutation route: HTMX partial via `is_htmx(request)` **plus** a `with_flash(url, key)` `RedirectResponse` fallback — CI-enforced by `test_mutation_fallback_sweep.py`
- Flash: `flash_trigger(level, body)`; always `markupsafe.escape()` DB-derived values
- Status filters (#306), dup-count invalidation, citation counts (#341): each carries a rule and a sweep test — read `docs/ADMIN.md` before touching a list or a merge path

## Public API Key Rules

Full conventions → `docs/CONVENTIONS.md`

- Auth deps (all from `src.api.public.deps`): `require_api_key` (read), `require_key` (read + `key_id`), `require_scope("scope:id")` (write); 403 missing/insufficient, 401 invalid
- All routes: Pydantic `response_model` + `operation_id`; no `dict[str, Any]` returns
- Lists: `{"data": [...], "meta": {...}}`, fetch `limit+1` for `has_more`; every paginated `ORDER BY` **must end with a unique column** (#297)
- Timestamps: `datetime` + `@field_serializer` calling `fmt_ts()`; never pre-serialize as `str` in handlers
- Conditional GET (#292/#392) lives entirely in `src/api/public/etag.py` — a route calls `conditional_response(...)` and **never** reads `if-none-match` itself
- Observation semantics — assignments (#311/#391), events (#321/#322), citations (#319): identity vs payload, refine-in-place, `op="retract"`, `source_key_id` same-or-NULL gate. Each is enforced by a sweep test; read `docs/CONVENTIONS.md` before changing one

## DB Key Rules

Full conventions → `docs/SCHEMA.md`

- PKs: ULIDs via `generate_id()` from `src.core.db`
- `updated_at`: maintained by DB triggers — never set manually
- Route handlers acquire connections via `Depends(get_db)` only — never `src.core.db.acquire()` (escapes `app.dependency_overrides`, breaks test isolation). Sole route exception: `GET /ready` (#343)
- Display names: always use `v_org_display_names` / `v_person_display_names` views; never join name tables directly for display
- Raw `person_names` access: AND-append `visibility='public'` or call `visible_names_filter()` from `src.core.db`. Lint enforces.
- Integration tests: require `TEST_DATABASE_URL`; never run against the production DB
- Integration test fixtures acquire from the session-scoped `db_pool`; endpoint tests use the lifespan-less rollback client (#288)
- Every inline `CHECK`/`FK`/`ON DELETE` change ships an idempotent reconciliation `DO` block, placed **before** any `set_updated_at()` trigger on that table (#307/#312/#315/#392); daily `power-map-schema-parity.timer` is the continuous guard
- Temporal and provenance invariants — org lifespan (#307), assignment/event/citation observations, org parent (#334), RA→RA edges (#301), canonical person name (#308), merge re-homing (#324/#327), entity search (#316), role-type vocabulary (#266) — each has exact rules in `docs/SCHEMA.md` and `docs/OBSERVATIONS.md`. Read them before changing any of them.

## Infrastructure

Single VM; port split:

| Port | Process | Managed by |
|---|---|---|
| 8000 | Production API (`--workers 2`) | systemd (`power-map.service`) |
| 8001 | Dev server (`--reload`) | manual, always from a worktree |

**All development work must be done in a git worktree** — never edit the main checkout directly. `brainstorming` is the entry point that triggers worktree setup via `using-git-worktrees`. After teardown, run `git worktree prune`.

**Worktree `.env` setup (required after creation):** The `.env` file is gitignored and not copied into worktrees automatically. `TEST_DATABASE_URL` now lives in `/etc/power-map/.env` (written by `scripts/write-db-secrets.sh`), but the repo `.env` is still needed for `GH_TOKEN`. After `worktree-create.sh`, always symlink it:
```bash
ln -s "$(git rev-parse --show-toplevel)/.env" <worktree-path>/.env
```

exe.dev proxy: dev server at `https://power-map.exe.xyz:8001/`.

| Situation | Action |
|---|---|
| After code change (production) | `sudo systemctl restart power-map` — also applies any schema changes |
| After schema change only (no restart) | `bash scripts/apply-schema.sh` — **targets PRODUCTION**; main checkout only (#398) |
| Applying a schema change from a worktree | `bash scripts/apply-schema.sh --test` — the bare command refuses in a linked worktree (exit 2) |
| Worktree dev testing | kill+restart dev server on 8001 with `--reload --log-config src/core/log_config.json` from worktree dir (see README / `docs/COMMANDS.md` for the full command) |
| Service debugging | `sudo journalctl -u power-map -f` |
| Quick prod health check | `curl -fsS localhost:8000/health && curl -fsS localhost:8000/ready` — unauthenticated probes (#343); `/ready` 503 reason: `no_pool`/`pool_timeout`/`db_error` |
| DB unreachable / `pool_timeout` | Egress IP likely rotated out of DO Trusted Sources — full triage in `docs/RUNBOOKS.md` § Database unreachable (#410) |

Scheduled timers (outbox prune · API anomaly · schema parity · ancillary orphans · assignment-relationship windows · weekly a11y · 2-min readiness guard · 5-min egress-IP drift) all surface failure through `systemctl --failed` — see `docs/COMMANDS.md` § Scheduled timers.

**Operational scripts are dry run by default (#402/#399):** `DATABASE_URL` resolves to **production** from any directory, so a `scripts/` writer gates the write behind `--execute` and calls `echo_target()` (`scripts/_dsn.py`) before connecting. `add_dsn_args`/`resolve_dsn` give every script `--database-url` and `--test`; `tests/scripts/test_dsn_sweep.py` enforces gate, echo, and resolver by AST with no allowlist. `apply-schema.sh` writes to production on the bare invocation and refuses in a linked worktree (#398) — use `--test` from a worktree. Full rules → `docs/COMMANDS.md` § Operational script safety.

Full command reference: `docs/COMMANDS.md`

### Environment files

Load both via uv's dotenv parser (gated on existence — uv errors hard on a missing `--env-file`):
```bash
env_args=()
[ -f /etc/power-map/.env ] && env_args+=(--env-file /etc/power-map/.env)
[ -f .env ] && env_args+=(--env-file .env)
uv run "${env_args[@]}" <cmd>
```
See `docs/COMMANDS.md` § Environment.

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

## Detail Docs

Each line says what a task would need the doc for — load the one that matches, not the tree.

**Domain & data**

- [docs/SCHEMA.md](docs/SCHEMA.md) — tables, display-name views, temporal validity windows, and the unique indexes that encode entity identity
- [docs/OBSERVATIONS.md](docs/OBSERVATIONS.md) — observation write semantics: identity vs payload, refine-in-place, `op="retract"`, the `source_key_id` gate, merge re-homing
- [docs/NAMES.md](docs/NAMES.md) — person and org names: the canonical/display pointer, visibility rules, structured parts, readings, locale/script tables

**Public API**

- [docs/PUBLIC_API.md](docs/PUBLIC_API.md) — auth, scopes, rate limits, pagination, conditional requests, subscriptions, the change feed
- [docs/API_ENTITIES.md](docs/API_ENTITIES.md) — per-resource endpoint behaviour: filters, response shapes, collection quirks
- [docs/CONVENTIONS.md](docs/CONVENTIONS.md) — request/response contracts every route follows, the API request log, ingestion, operational-script dry-run rules

**Admin dashboard**

- [docs/ADMIN.md](docs/ADMIN.md) — server side: auth, archive model, HTMX partial responses, flash, per-panel rules
- [docs/HTMX.md](docs/HTMX.md) — interaction patterns: swaps, redirects, flash, pagination, inline edit, guarded deletes, live header sync
- [docs/UI.md](docs/UI.md) — components and table/list conventions: buttons, badges, modals, page headers, empty states, the row-key contract
- [docs/FORMS.md](docs/FORMS.md) — the three hand-built composite controls: typeahead, address confirm, paired dates
- [docs/MERGE.md](docs/MERGE.md) — duplicate detection and the merge-bar pattern across people, orgs and roles
- [docs/STYLE.md](docs/STYLE.md) — visual system: brand, colour, dark mode, CSS tokens, layout, breakpoints, i18n, performance
- [docs/ACCESSIBILITY.md](docs/ACCESSIBILITY.md) — WCAG 2.1 AA markup rules and the three a11y test tiers

**Operating it**

- [docs/COMMANDS.md](docs/COMMANDS.md) — everyday commands: setup, env files, provisioning, deploy, the dev loop, linting, scheduled timers
- [docs/TESTING.md](docs/TESTING.md) — how to run each test tier, the integration marker, Vitest conventions, the browser a11y sweep
- [docs/RUNBOOKS.md](docs/RUNBOOKS.md) — one-off and scheduled data operations: importer, seeds, backfills, vocabulary migrations, audits
- [docs/RUNBOOK_DB_MIGRATION.md](docs/RUNBOOK_DB_MIGRATION.md) — DB cutover checklist, maintenance window, rollback
- [docs/SKILLS.md](docs/SKILLS.md) — vendored skill inventory, submodule refresh, SocratiCode MCP tools
