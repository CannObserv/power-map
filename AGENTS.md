# power-map — Agent Guidelines

Be terse. Prefer fragments over full sentences. Skip filler and preamble. Sacrifice grammar for density. Lead with the answer or action.

## Project Overview

Web service for mapping political and corporate power: people, organizations, roles, and their temporal relationships.

## Development Methodology

TDD required. Red → Green → Refactor. No production code without a failing test first.

## Environment & Tooling

Python ≥3.12, uv, pytest, ruff; Node ≥22, npm, vitest + ESLint + Prettier (JS only); bats + shellcheck (shell); pre-commit (git hooks)

<!-- BEGIN socraticode-policy -->
## Code Exploration Policy

SocratiCode is the preferred semantic-search tool here (local Qdrant store +
on-disk graph; manifest `.socraticodecontextartifacts.json`). Its MCP tools are
**deferred** — schemas load only after the `ToolSearch` prefetch that
`.claude/hooks/socraticode-reminder.sh` prints each session.

**Negative rule.** Use SocratiCode MCP tools first for semantic questions
("where is X", "how does Y work", "what depends on Z"). Reach for `grep`/`rg`
only on exact strings (error messages, log lines, known symbols). Reserve the
Explore subagent for path-pattern walks (`*.py` under `src/api/`), not semantic
search.

| Goal | Tool |
|------|------|
| Where is X defined / how does Y work / what touches Z | `codebase_search` |
| Exact string or regex (errors, log lines, known symbols) | `grep` / `rg` |
| Imports/dependents of a file · blast radius of a change | `codebase_graph_query` / `codebase_impact` |

Full tool table, prefetch query, per-tool guidance: [`docs/SOCRATICODE.md`](docs/SOCRATICODE.md).
<!-- END socraticode-policy -->

## Project Layout

```
src/api/        — FastAPI app (ASGI, routes, auth, schemas)
  admin/        — Jinja2 + HTMX admin dashboard
  public/       — JSON API (X-API-Key auth, server-to-server)
src/core/       — Shared domain logic (db, schema.sql, normalizers, ingestion); `ingestion/mapping/` is the dbt-duckdb project, the only place usa-wa ontology lives (#497)
src/static/     — Static assets; vendor/ is SHA-pinned, excluded from linting
tests/          — Mirrors src/; js/ for Vitest
docs/           — Reference docs, by subject; index at the end of this file
scripts/        — One-off operational scripts
infra/          — systemd units + terraform
```

## Admin Dashboard Key Rules

Full conventions → `docs/ADMIN.md`
Accessibility rules and their test tiers → `docs/ACCESSIBILITY.md`

- **JS required (#287)**: the admin is an HTMX app, not progressively enhanced. Never add `method="post" action=…` to an `hx-post` control to "support no-JS" — the 303 fallbacks serve non-HTMX *clients*, not browsers. Guard: `test_js_required_policy.py`
- Auth: `user: AdminUser = Depends(get_admin_user)` on every route
- Archive model: `archived_at TIMESTAMPTZ` — NULL = active, non-NULL = archived; hard delete requires archived (409 otherwise). Unarchive is **fallible** on `roles` / `role_assignments` (#424) — a freed slot can be reoccupied, so savepoint + warning flash, never a bare `UPDATE`
- Every mutation route: HTMX partial via `is_htmx(request)` **plus** a `with_flash(url, key)` `RedirectResponse` fallback — CI-enforced by `test_mutation_fallback_sweep.py`
- Flash: `flash_trigger(level, body)`; always `markupsafe.escape()` DB-derived values
- Status filters (#306), dup-count invalidation, citation counts (#341): each carries a rule and a sweep test — read `docs/ADMIN.md` before touching a list or a merge path

## Public API Key Rules

Full conventions → `docs/CONVENTIONS.md`

- Auth deps from `src.api.public.deps` only: `require_api_key` / `require_key` (read), `require_scope("scope:id")` (write); 403 missing/insufficient, 401 invalid. Write txns open via `stamped_transaction(db, key_id)`, never bare `db.transaction()` (#491, sweep-enforced)
- All routes: Pydantic `response_model` + `operation_id`; no `dict[str, Any]` returns
- Lists: `{"data": [...], "meta": {...}}`, fetch `limit+1` for `has_more`; every paginated `ORDER BY` **must end with a unique column** (#297)
- Timestamps: `datetime` + `@field_serializer` → `TimestampStr` via `fmt_ts()`, never hand-built (#440)
- Conditional GET (#292/#392) lives entirely in `src/api/public/etag.py` — a route calls `conditional_response(...)` and **never** reads `if-none-match` itself
- Observation semantics — assignments (#311/#391), events (#321/#322), citations (#319): identity vs payload, refine-in-place, `op="retract"`, `source_key_id` same-or-NULL gate. Each is enforced by a sweep test; read `docs/OBSERVATIONS.md` before changing one

## DB Key Rules

Full conventions → `docs/SCHEMA.md`

- PKs: ULIDs via `generate_id()` from `src.core.db`
- `updated_at`: maintained by DB triggers — never set manually
- Route handlers acquire connections via `Depends(get_db)` only — never `src.core.db.acquire()` (breaks test isolation). Sole route exception: `GET /ready` (#343)
- Display names: always use `v_org_display_names` / `v_person_display_names` views; never join name tables directly for display
- Raw `person_names` access: AND-append `visibility='public'` or call `visible_names_filter()` from `src.core.db`. Lint enforces.
- Integration tests: require `TEST_DATABASE_URL`; never run against the production DB
- Integration test fixtures acquire from the session-scoped `db_pool`; endpoint tests use the lifespan-less rollback client (#288)
- Every inline `CHECK`/`FK`/`ON DELETE` change ships an idempotent reconciliation `DO` block, placed **before** any `set_updated_at()` trigger on that table (#307/#312/#315/#392); daily `power-map-schema-parity.timer` guards it. Seeds of a UNIQUE-natural-key lookup stage rows in `_seed_<table>` + `reconcile_seeded_slugs()` first (#458)
- Temporal and provenance invariants — org lifespan (#307), assignment/event/citation observations, org parent (#334), RA→RA edges (#301), canonical person name (#308), merge identity & signals (#324/#327/#467), entity search (#316), role-type vocabulary (#266) — each has exact rules in the `docs/SCHEMA*.md` family, `docs/OBSERVATIONS.md`, `docs/API_ASSIGNMENTS.md`, or `docs/MERGE.md`. Read them before changing any of them.

## Infrastructure

Single VM; port split:

| Port | Process | Managed by |
|---|---|---|
| 8000 | Production API (`--workers 2`) | systemd (`power-map.service`) |
| 8001 | Dev server (`--reload`) | manual, always from a worktree |

**All development work must be done in a git worktree** — never edit the main checkout directly. `brainstorming` is the entry point that triggers worktree setup via `using-git-worktrees`. After teardown, run `git worktree prune`. Sole exemption (#505): a **spike** — a read-only or throwaway probe answering a feasibility question, keeping no code. Keeping anything re-classifies the task, and that lands in a worktree.

**Worktree setup (required after creation, #450):** run
```bash
bash scripts/worktree-setup.sh <worktree-path>
```
Gives the worktree its own `.venv`, initialises the `skills-vendor/` submodules, and symlinks the gitignored `.env` and `data/cannabis_observer`; refuses (exit 2) against the main checkout. **Never share a venv with the main checkout** — that is production's working directory, and its units' `uv run` / `ExecStartPre=uv sync` rewrite a shared venv mid-suite, taking the browser tier with it. Full rules → `docs/COMMANDS.md` § Worktree setup.

exe.dev proxy: dev server at `https://power-map.exe.xyz:8001/`.

Only the hazards are here; every command itself is in `docs/COMMANDS.md`.

- `bash scripts/apply-schema.sh` **targets PRODUCTION from any directory** — main checkout only, and it refuses in a linked worktree (exit 2, #398). From a worktree, `--test`.
- `sudo systemctl restart power-map` also applies any pending schema change.
- The dev server on 8001 always runs from a worktree, never the main checkout.
- `/health` + `/ready` are unauthenticated probes (#343); a `pool_timeout` from `/ready` means the egress IP likely rotated out of DO Trusted Sources → `docs/RUNBOOK_DB_TRIAGE.md` (#410).

Scheduled timers all surface failure through `systemctl --failed` — roster, cadences and scripts in `docs/COMMANDS.md` § Scheduled timers.

**Operational scripts are dry run by default (#402/#399):** `DATABASE_URL` resolves to **production** from any directory, so a `scripts/` writer gates its write behind `--execute` and echoes its target first. Uniform flags, the `scripts/_dsn.py` resolver, and the AST sweep enforcing them → `docs/RUNBOOKS.md` § Operational scripts.

Full command reference: `docs/COMMANDS.md`

### Environment files

`/etc/power-map/.env` then `.env`, later winning — loaded via uv's dotenv parser and gated on existence, because uv errors hard on a missing `--env-file`. The `env_args` idiom every command below uses → `docs/COMMANDS.md` § Environment.

## Agent Skills & Tools

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
- ISO 8601 on the API wire (#440 guards it; JSON logs use `+00:00`): `YYYY-MM-DDTHH:MM:SS.ffffffZ`, `YYYY-MM-DD` dates

**Version bumps:** update `pyproject.toml` and `package.json` together — the `check-version-sync` pre-commit hook enforces this.

**General:**
- Imports explicit and at file top — never inline in a function
- Docstrings for public modules, classes, functions
- Test structure mirrors source (`src/foo.py` → `tests/test_foo.py`)
- Small, focused functions

## Detail Docs

Each line says what a task would need the doc for — load the one that matches, not the tree.

**Domain & data**

- [docs/SCHEMA.md](docs/SCHEMA.md) — tables, column conventions, display-name views, links schema; routes on to [SCHEMA_INDEXES](docs/SCHEMA_INDEXES.md) and [SCHEMA_VALIDITY](docs/SCHEMA_VALIDITY.md)
- [docs/OBSERVATIONS.md](docs/OBSERVATIONS.md) — identifier types; writes: identity vs payload, refine-in-place, `op="retract"`, `source_key_id` gate; routes on to [ANCILLARY](docs/ANCILLARY.md)
- [docs/NAMES.md](docs/NAMES.md) — person and org names: the canonical/display pointer, visibility rules, structured parts, readings, locale/script tables, org display-identity invariants

**Public API**

- [docs/PUBLIC_API.md](docs/PUBLIC_API.md) — auth, scopes, rate limits, pagination, conditional requests; routes to [CHANGE_FEED](docs/CHANGE_FEED.md)
- [docs/API_ENTITIES.md](docs/API_ENTITIES.md) — one-table index over the resource docs below; load one, not the set
  - [API_PEOPLE](docs/API_PEOPLE.md) — people, identify, name reads
  - [API_ORGS](docs/API_ORGS.md) — orgs, hierarchy, lifespan
  - [API_ROLES](docs/API_ROLES.md) — roles and the role-type catalog
  - [API_ASSIGNMENTS](docs/API_ASSIGNMENTS.md) — assignments, RA→RA relationships
  - [API_EVENTS](docs/API_EVENTS.md) — entity events
  - [API_JURISDICTIONS](docs/API_JURISDICTIONS.md) — jurisdictions and districts
- [docs/CONVENTIONS.md](docs/CONVENTIONS.md) — request/response contracts every route follows, the API request log, ingestion

**Admin dashboard**

- [docs/ADMIN.md](docs/ADMIN.md) — server side: auth, archive model, HTMX partials, flash, status filters; routes on to [ADMIN_PANELS](docs/ADMIN_PANELS.md) and [ADMIN_NAMES](docs/ADMIN_NAMES.md)
- [docs/ADMIN_OVERLAY.md](docs/ADMIN_OVERLAY.md) — the curation overlay: editing a field a dataset owns, and what survives a re-apply
- [docs/HTMX.md](docs/HTMX.md) — interaction patterns: swaps, redirects, flash, pagination, inline edit, guarded deletes, live header sync
- [docs/UI.md](docs/UI.md) — components and table/list conventions: buttons, badges, modals, page headers, empty states, the row-key contract
- [docs/FORMS.md](docs/FORMS.md) — the hand-built composite controls: typeahead, address confirm, paired dates
- [docs/MERGE.md](docs/MERGE.md) — the server-side merge data contract, duplicate detection, and the merge-bar pattern across people, orgs and roles
- [docs/STYLE.md](docs/STYLE.md) — visual system: brand, colour, dark mode, CSS tokens, layout, breakpoints, i18n, performance
- [docs/ACCESSIBILITY.md](docs/ACCESSIBILITY.md) — WCAG 2.1 AA markup rules and the a11y test tiers

**Operating it**

- [docs/COMMANDS.md](docs/COMMANDS.md) — everyday commands: setup, env files, provisioning, deploy, the dev loop, linting, timers, the ship gate
- [docs/TESTING.md](docs/TESTING.md) — each test tier, the integration marker, the endpoint-test client, Vitest, the browser a11y sweep, the ship gate
- [docs/RUNBOOKS.md](docs/RUNBOOKS.md) — data operations: importer, seeds, role sweep, TTL prune, operational-script dry-run rules
- [docs/RUNBOOK_DESIRED_STATE.md](docs/RUNBOOK_DESIRED_STATE.md) — the dataset-subscription chain: pull, build, apply, and what a nightly run blocks on
- [docs/AUDITS.md](docs/AUDITS.md) — the recurring integrity audits, and which of them carry systemd timers
- [docs/RUNBOOK_DB_TRIAGE.md](docs/RUNBOOK_DB_TRIAGE.md) — DB unreachable: `/ready` reasons, egress-IP drift
- [docs/RUNBOOK_DB_MIGRATION.md](docs/RUNBOOK_DB_MIGRATION.md) — DB cutover checklist, maintenance window, rollback
- [docs/SKILLS.md](docs/SKILLS.md) — vendored skill inventory, submodule refresh, hook command form, index health (the daily `unresolved %` line is not a defect)
- [docs/SOCRATICODE.md](docs/SOCRATICODE.md) — the exploration policy's other half: full tool table, prefetch string, per-tool notes, graph health
- [docs/CONTEXT.md](docs/CONTEXT.md) — the rules this file obeys: its token budget, index lines that stay pointers, why a count carries a command or no number
