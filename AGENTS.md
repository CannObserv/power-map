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
scripts/        — One-off operational scripts
infra/          — systemd units (API + prune timer) + terraform
```

## Admin Dashboard Key Rules

Full conventions → `docs/STYLE.md §32`

- Auth: `user: AdminUser = Depends(get_admin_user)` on every route — raises `HTTPException(307)` redirect when exe.dev headers absent
- Archive model: `archived_at TIMESTAMPTZ` — NULL = active, non-NULL = archived; hard delete requires archived (409 otherwise); archive/unarchive both return 409 if already in that state
- HTMX partials: `is_htmx(request)` from `src.api.admin.deps` (checks `HX-Request and not HX-Boosted`); always include `RedirectResponse` fallback
- Flash: `flash_trigger(level, body, extra=None)` on mutation routes; always `markupsafe.escape()` DB-derived values
- Dup counts: `await invalidate_dup_count_cache(db)` (from `org_dups` or `people_dups`) after any merge or dismiss; `db` must be the route's connection
- List status filters (#306): each `*_queries.py` declares `STATUS_PREDICATES` + `VALID_STATUSES` (incl. first-class `all`); unknown status → `active`, never no-filter. A search must never silently hide other-status matches — `query_*_rows` returns `hidden_matches` (grouped `count(*) FILTER` pass via `list_status.count_with_hidden_matches`) rendered as the "N more matches — Show all" affordance (`admin/_hidden_matches.html`; plain link, not hx-get). Full rules → `docs/STYLE.md` §32

## Public API Key Rules

Full conventions → `docs/CONVENTIONS.md`

- Auth deps (all from `src.api.public.deps`): `require_api_key` (read-only, returns `user_id`); `require_key` (returns `AuthedKey(user_id, key_id)` — use when handler needs the key_id, e.g. subscription join); `require_scope("scope:id")` (write, enforces scope); 403 on missing/insufficient scope or absent header, 401 on invalid key
- All routes: Pydantic `response_model` + `operation_id`; no `dict[str, Any]` returns
- Lists: `{"data": [...], "meta": {"limit", "offset", "count", "has_more"}}`; fetch `limit+1` rows for `has_more`. Every paginated `ORDER BY` **must end with a unique column** (usually the PK) — otherwise offset windows over tied rows skip and duplicate (#297)
- Timestamps: `datetime` fields in response models + `@field_serializer` calling `fmt_ts()` from `schemas.py`; ISO 8601 with `Z` suffix; never pre-serialize as `str` in handlers

## DB Key Rules

Full conventions → `docs/CONVENTIONS.md`

- PKs: ULIDs via `generate_id()` from `src.core.db`
- `updated_at`: maintained by DB triggers — never set manually
- Route handlers acquire connections via `Depends(get_db)` only — never call `src.core.db.acquire()` directly; doing so escapes `app.dependency_overrides` and breaks test isolation
- Display names: always use `v_org_display_names` / `v_person_display_names` views; never join name tables directly for display
- Roles: a role's **structural fields** are `role_type_id` (FK `role_types`) + `jurisdiction_id` + `qualifier` (e.g. "Position 1"); a plain role leaves them NULL. A role with a jurisdiction needs a role type (`chk_role_jurisdiction_needs_role_type`). Uniqueness is split — `uq_role_structural` (role with a jurisdiction) vs `uq_role_org_title` (role without one). The `title` of a role that has a role type is **PM-synthesized** and optional on observations (`resolve_role` prefers the synthesized title over any supplied one, #267). The `role_types` catalog is publicly observable via `GET /api/v1/role-types`; `role_types.expects_jurisdiction` is an advisory hint (not enforced), #268. `role_types.requires_qualifier` **is** enforced (#273): a jurisdictional observation of a per-position office (e.g. `state_representative`) without a `qualifier` is rejected `qualifier_required` instead of minting a positionless seat — at the app layer (`resolve_role`) and, as a backstop for admin/direct-INSERT paths, at the DB layer (`trg_role_requires_qualifier` trigger). Full rules → `docs/CONVENTIONS.md` §"Unique Indexes".
- Org lifespan bounds (#307): org end = `v_org_lifespan.ended_on` (derived from `dissolved`/`merged_with` entity events; latest date within event precision) — `active`/`archived_at` are **not** lifespans. Invariant: assignment window ⊆ org lifespan; `is_current=FALSE, end_date NULL` = *unknown end* (allowed, never invented; currency signal is `is_current`, not `end_date IS NULL`). Enforced app-layer only (`src.core.org_lifecycle.check_assignment_lifespan` on all admin assignment writes; observation path records freely, `scripts/audit_org_lifecycle_assignments.py` reconciles — `--execute` closes `current_on_ended` at `ended_on`). Full rules → `docs/CONVENTIONS.md` §"Org lifespan bounds on assignments".
- Person-name visibility rule (deadnames, hidden, legal-only): see `docs/CONVENTIONS.md` §"Person names — i18n & cultural awareness".
- Person **canonical/display** name (#308): `person_names.is_canonical` is the **display pointer** — `UNIQUE (person_id) WHERE is_canonical` (one per person) plus `CHECK (NOT is_canonical OR visibility='public')` (always displayable). Same model as `uq_org_canonical_name`. So `v_person_display_names` is a plain join — no `DISTINCT ON`, no priority ladder. A `deadname` can never be canonical (`NEVER_CANONICAL_NAME_TYPES`); client hints for one are ignored rather than raising. `write_names` auto-promotes one name per write when no hint is sent, and heals a canonical-less person on re-observation; `NO_AUTO_CANONICAL_NAME_TYPES` is never auto-promoted. Name families (a name and its furigana/romaji) are modelled by the `reading_of_id` FK, **not** by sharing a canonical slot. Every path that can strand a person without a display pointer repairs it: observation, name delete, and merge call `heal_person_canonical` — the shared best-effort repair (savepoint; swallows `PostgresError`; WARNING for anything but a lost race) — while the one-off `#308c` backfill runs its own SQL sharing the same ladder via `name_type_priority_sql()`. The org name-delete path has its own equivalent heal in `orgs_names.py`. Person names dedup on `(name, name_type, locale, script)`, never on `name` alone; merge additionally requires equal `visibility` and loosens `name_type` to collapse two *ordinary display* types carrying the same text, but never a `NO_AUTO_CANONICAL_NAME_TYPES` row. Admin validates canonical against `name_type` *and* the **effective** visibility, because `trg_deadname_visibility` rewrites visibility after submission. Full rules → `docs/CONVENTIONS.md` §"Canonical name = the display pointer".
- Raw `person_names` access: AND-append `visibility='public'` or call `visible_names_filter()` from `src.core.db`. Lint enforces.
- Structured name parts: `person_name_parts` is a 1:0..1 sidecar to `person_names` (ON DELETE CASCADE). Never auto-*written* — only populated when an upstream source supplies structure or a human confirms a suggestion. Assisted decomposition via `src.core.normalizers.person_name.suggest_parts(...)` is allowed (CSV triage / UI pre-fill); persistence still goes through the unified name-row form (`POST /admin/people/{pid}/names/{nid}/edit-row/`). See `docs/CONVENTIONS.md` §"Structured parts (`person_name_parts` sidecar)".
- BCP 47 / ISO 15924 lookup tables (`bcp47_locales`, `iso15924_scripts`) are FK-validated on `person_names.locale` / `.script`. After `apply_schema` on a fresh DB, seed via `uv run --group seed scripts/seed_locales_scripts.py` (see `docs/COMMANDS.md`); `apply_schema` logs a WARNING when either table is empty.
- Integration tests: require `TEST_DATABASE_URL`; never run against the production DB
- Integration test fixtures: acquire from `db_pool` (session-scoped), not `asyncpg.connect`. Full recipe + the `loop_scope="session"` gotcha → `docs/CONVENTIONS.md`. Reference: `tests/api/admin/test_people_names.py`.
- Endpoint-test client: prefer the lifespan-less rollback client (`AsyncClient` + `ASGITransport` + `get_db` override yielding a BEGIN/ROLLBACK `db_pool` connection) over per-test `with TestClient(app)` — no per-test `asyncpg.create_pool`, auto rollback, no manual teardown (#288). Pure-unit route tests (mocked `get_db`) use `TestClient(app)` without `with`. Full rationale + recipe → `docs/CONVENTIONS.md` §DB. Reference: `tests/api/admin/test_orgs.py`.

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
| After schema change only (no restart) | `bash scripts/apply-schema.sh` |
| Worktree dev testing | kill+restart dev server on 8001 with `--reload` from worktree dir |
| Service debugging | `sudo journalctl -u power-map -f` |
| Outbox/tombstone TTL prune | daily `power-map-prune.timer` runs `scripts/prune_outbox.py --execute` (90-day window, `entity_changes` + `deleted_entities`); see `docs/COMMANDS.md` |
| Per-key API anomaly check | hourly `power-map-anomaly.timer` runs `scripts/check_api_anomalies.py` — journal WARNING + exit 3 per key ≥ `API_ANOMALY_HOURLY_THRESHOLD` req/hr (#294); human layer = Admin → Activity → API Requests per-key panel; see `docs/COMMANDS.md` |

Full command reference: `docs/COMMANDS.md`

### Environment Variables

| File | Owner | Contents |
|---|---|---|
| `/etc/power-map/.env` | root:exedev (640) | `DATABASE_URL`, `MIGRATIONS_DATABASE_URL`, `TEST_DATABASE_URL`, `ADDRESS_VALIDATOR_API_KEY`, `ADDRESS_VALIDATOR_RUN_VALIDATION` (default false; true → calls `/validate` for validation status, false → `/standardize` only), `ADDRESS_VALIDATOR_BASE_URL` (optional; defaults to `https://address-validator.exe.xyz:8000`; override to point at dev server on port 8001), `DB_POOL_MIN_SIZE` (default 2), `DB_POOL_MAX_SIZE` (default 5; tune per DO tier connection limit), `API_REQUEST_LOG_MAX_PENDING` (optional; default 50; soft cap on in-flight fire-and-forget `api_request_log` capture writes before shedding — #290; tune relative to `DB_POOL_MAX_SIZE`; `0` disables capture entirely), `RATE_LIMIT_READ_PER_S` / `RATE_LIMIT_READ_BURST` / `RATE_LIMIT_WRITE_PER_S` / `RATE_LIMIT_WRITE_BURST` (optional; defaults 2/120/1/60; per-key token buckets on the public API — #292; per-worker, so effective ceiling ≈ workers × rate; refill ≤ 0 disables that bucket), `API_KEY_LAST_USED_DEBOUNCE_S` (optional; default 60; min seconds between `api_keys.last_used_at` stamps per worker — #292; `0` stamps every request), `API_ANOMALY_HOURLY_THRESHOLD` (optional; default 5000; requests per key per hour at/above which the hourly anomaly check WARNs — #294; deliberately below the ~14.4k/hr rate-limit ceiling so near-ceiling runaways are caught; `<= 0` disables the check and the admin hot-row highlighting) |
| `.env` (repo, gitignored) | developer | `GH_TOKEN` |

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
