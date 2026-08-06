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
- HTMX partials: `is_htmx(request)` from `src.api.admin.deps` (checks `HX-Request and not HX-Boosted`); always include `RedirectResponse` fallback, wrapped with `with_flash(url, key)` (`saved`/`removed`/`invalid`/`exists`) so non-HTMX mutations confirm on the target page (#351); CI-enforced by `test_mutation_fallback_sweep.py`
- Flash: `flash_trigger(level, body, extra=None)` on mutation routes; always `markupsafe.escape()` DB-derived values
- Dup counts: `await invalidate_dup_count_cache(db)` (from `org_dups` or `people_dups`) after any merge or dismiss; `db` must be the route's connection
- List status filters (#306): each `*_queries.py` declares `STATUS_PREDICATES` + `VALID_STATUSES` (incl. first-class `all`); unknown status → `active`, never no-filter. A search must never silently hide other-status matches — `query_*_rows` returns `hidden_matches` (grouped `count(*) FILTER` pass via `list_status.count_with_hidden_matches`) rendered as the "N more matches — Show all" affordance (`admin/_hidden_matches.html`; plain link, not hx-get). Full rules → `docs/STYLE.md` §32
- Citations indicator (#341): row-level active-citation counts come from the **row-fetch SQL** via `citation_count_lateral` (`_citations_shared.py`) — never a side template dict (side dicts go stale on single-row HTMX re-renders). Non-drawer rows render the `citation_indicator` macro; #319 Cite-drawer rows (names/events) hold the count in a `cite-count-<id>` span the citations factory OOB-refreshes on create/delete. Full rules → `docs/STYLE.md` §32

## Public API Key Rules

Full conventions → `docs/CONVENTIONS.md`

- Auth deps (all from `src.api.public.deps`): `require_api_key` (read-only, returns `user_id`); `require_key` (returns `AuthedKey(user_id, key_id)` — use when handler needs the key_id, e.g. subscription join); `require_scope("scope:id")` (write, enforces scope); 403 on missing/insufficient scope or absent header, 401 on invalid key
- All routes: Pydantic `response_model` + `operation_id`; no `dict[str, Any]` returns
- Lists: `{"data": [...], "meta": {"limit", "offset", "count", "has_more"}}`; fetch `limit+1` rows for `has_more`. Every paginated `ORDER BY` **must end with a unique column** (usually the PK) — otherwise offset windows over tied rows skip and duplicate (#297)
- Timestamps: `datetime` fields in response models + `@field_serializer` calling `fmt_ts()` from `schemas.py`; ISO 8601 with `Z` suffix; never pre-serialize as `str` in handlers
- Conditional GET (#292/#392): all of it lives in `src/api/public/etag.py`. A route calls `conditional_response(request, response, etag, last_modified)` and returns its result when non-None; it **never reads the `if-none-match` header itself**. Two validator shapes only — `collection_etag` (watermark) where the table has `updated_at`, `catalog_validator` (content hash) where no single one covers the rows; the aggregate must share the body's `WHERE`, and the validator is computed **before** the body. New ones declare `responses=NOT_MODIFIED` + join `_CONDITIONAL_GETS`. Three sweeps in `tests/api/public/test_conditional_get.py` enforce all of this. Full rules → `docs/CONVENTIONS.md` §"Public API"
- Assignment observations (#311): `(person, role, start_date)` is identity, not payload — PM-native (`pm_assignment_id`) observations **update in place** (`update_assignment_fields`: move start, explicit `end_date: null` clears, tri-state `is_current`); natural-key auto-attach applies only the open-tenure close, other deltas come back in `unapplied`. Provenance: `role_assignments.source_key_id` stamped on NEW, updates gated to same-or-NULL source. Dup cleanup: `scripts/audit_assignment_duplicates.py`. **Retract (#391):** `op="retract"` archives the `pm_assignment_id` tenure → new `retracted` disposition — the correction for a produced *artifact* (never happened), which closing can't express and un-producing only orphans. Always id-addressed (`invalid` otherwise); `person_id`/`role_id` must match (`identity_immutable`); same-or-NULL provenance (`source_key_mismatch`); payload + ancillary ignored; re-emit is a quiet `auto-attached` no-op (lookup deliberately **not** archived-filtered, unlike `update_assignment_fields`, and not routed through `resolve_entity`). **Authoritative** — both create doors skip an archived twin instead of resurrecting (`resolve_assignment` + the embedded `write_role_assignments`), so a re-emitting producer can't defeat the retract; that attach **writes nothing** (bound deltas *and* ancillary withheld, every name echoed in `unapplied` — reported even when the value equals what the archived row stores, unlike the active path); un-retract = admin unarchive only, no `archived:false` verb. Archiving cascades free: outbox emit + `staff_of` edges archive (#301). Full rules → `docs/CONVENTIONS.md` §"Assignment observations — update semantics & provenance".
- Event observations (#321): `(event_type, linked_entity)` is identity, `date`/`notes`/`place`/`visibility` mutable — a `pm_event_id` observation **refines in place** (immutable identity → `identity_immutable`; narrow diff-before-write no-op; `source_key_id` same-or-NULL gate → `provenance_conflict`). Embedded (`events: [...]`) is all-or-nothing; the event-native `POST /orgs/{id}/events/observations` is **partial-success** (per-event savepoint → per-event disposition + reason slug; one transient `linked_entity_unresolved` gives ordering-tolerance). `succeeded_by` = renamed-continuity link on the predecessor. **Retract (#322):** `op="retract"` archives the `pm_event_id` event (`archived_at`, never hard-delete) — the only correction for a dateless linked event, so a re-link is create-new + retract-old; always id-addressed, `event_type` must match (`identity_immutable`), same-or-NULL provenance, already-archived re-emit is a `auto-attached` no-op (no clock bump), archiving fires the outbox so subscribers drop the anchor. Full rules → `docs/CONVENTIONS.md` §"Event observations — refine-in-place, partial-success, `succeeded_by` & retract".

## DB Key Rules

Full conventions → `docs/CONVENTIONS.md`

- PKs: ULIDs via `generate_id()` from `src.core.db`
- `updated_at`: maintained by DB triggers — never set manually
- Route handlers acquire connections via `Depends(get_db)` only — never call `src.core.db.acquire()` directly; doing so escapes `app.dependency_overrides` and breaks test isolation. **Sole route exception (#343):** `GET /ready` probes the real pool via `db.check_ready()` (bounded acquire + `SELECT 1`) — a failing `Depends` would 500 instead of 503; tests patch `check_ready` itself
- Display names: always use `v_org_display_names` / `v_person_display_names` views; never join name tables directly for display
- Entity search (#316): every `search_tsv @@ …` predicate goes through `pm_prefix_tsquery(cfg, q)` (last-token prefix FTS), never bare `plainto_tsquery`; jurisdictions are the ILIKE exception. Full rules → `docs/CONVENTIONS.md` §"Entity search — last-token prefix FTS".
- Roles: a role's **structural fields** are `role_type_id` (FK `role_types`) + `jurisdiction_id` + `qualifier` (e.g. "Position 1"); a plain role leaves them NULL. A role with a jurisdiction needs a role type (`chk_role_jurisdiction_needs_role_type`). Uniqueness is split — `uq_role_structural` (role with a jurisdiction) vs `uq_role_org_title` (role without one). The `title` of a role that has a role type is **PM-synthesized** and optional on observations (`resolve_role` prefers the synthesized title over any supplied one, #267). The `role_types` catalog is publicly observable via `GET /api/v1/role-types`; `role_types.expects_jurisdiction` is an advisory hint (not enforced), #268. `role_types.requires_qualifier` **is** enforced (#273): a jurisdictional observation of a per-position office (e.g. `state_representative`) without a `qualifier` is rejected `qualifier_required` instead of minting a positionless seat — at the app layer (`resolve_role`) and, as a backstop for admin/direct-INSERT paths, at the DB layer (`trg_role_requires_qualifier` trigger). Full rules → `docs/CONVENTIONS.md` §"Unique Indexes".
- Role-type vocabulary is **governed** (#266): a slug earns a row only if you'd query "all of them" across orgs (the aggregation test) — otherwise it stays a free-text `roles.title`. Every non-jurisdictional slug is **domain-prefixed** by the org-kind it attaches to (`committee_`, `chamber_`, `legislature_`, `party_`; `org_*` reserved for #303); the two seat types (`state_representative`, `state_senator`) predate the convention and are grandfathered unprefixed. Types stay jurisdiction-neutral — WA labels live in `src/core/role_title.py`. Prefer **one coarse type + specific free-text title** over a slug per title (`legislature_staff`, `chamber_leader`). The coarse `member` was retired — split into `committee_member` + `party_member`. `resolve_role` **upgrades on match**: a typed observation fills a matched untyped role's `role_type_id` in place (never reclassifies), so ingest self-classifies. Full rules → `docs/CONVENTIONS.md` §"Role-type vocabulary — governance".
- Org lifespan bounds (#307): org end = `v_org_lifespan.ended_on` (derived from `dissolved`/`merged_with` entity events; latest date within event precision) — `active`/`archived_at` are **not** lifespans. Invariant: assignment window ⊆ org lifespan; `is_current=FALSE, end_date NULL` = *unknown end* (allowed, never invented; currency signal is `is_current`, not `end_date IS NULL`). Enforced app-layer only (`src.core.org_lifecycle.check_assignment_lifespan` on all admin assignment writes; observation path records freely, `scripts/audit_org_lifecycle_assignments.py` reconciles — `--execute` closes `current_on_ended` at `ended_on`). Full rules → `docs/CONVENTIONS.md` §"Org lifespan bounds on assignments".
- Org parent — authoritative reparent (#334): `organizations.parent_id` on the observation path is the org analog of the #311 assignment split. **PM-native (`pm_org_id`)** = the authoritative reparent channel — `write_org_parent(..., authoritative=True)` *replaces* the stored parent (fix for anchored-org reparent being a silent no-op), guarded `parent_not_found` / `parent_cycle` (self + `trg_no_org_cycle`) / `source_key_mismatch`. **Natural/external-identifier** match = write-if-null (never reparents). Provenance: `organizations.source_key_id` claimed via `COALESCE` on first parent write (lazy — never stamped at `_create_entity`); reparent needs `source_key_id IS NULL OR = caller` (NULL curator/pre-#334 parent claimable-once; no curator-precedence lock). Idempotent same-parent re-assert is a quiet no-op before the gate (no clock bump). Full rules → `docs/CONVENTIONS.md` §"Org parent — authoritative reparent & provenance".
- Merge dedup — role_assignment ancillary (#324): `links`/`contact_methods`/`field_confidence`/`identifiers` key on `(entity_type='role_assignment', entity_id)` with **no FK**. Every merge conflict-delete (`people_merge.py`, `orgs_roles.py::role_merge`, `orgs_merge.py` role-pair) must `rehome_conflicting_assignment_ancillary` (from `src.core.ancillary_migrate`) **before** the DELETE — re-point or dedup onto the survivor; a changed survivor is signalled — `links`/`contact_methods`/`identifiers` self-emit via their touch triggers (#327), `field_confidence`/`import_provenance` (trigger-less) via a gated manual emit. Daily guard: `power-map-ancillary-orphans.timer`. Existing-orphan recovery: `scripts/cleanup_role_assignment_ancillary_orphans.py` (heuristic, supervised `--execute`). Full rules → `docs/CONVENTIONS.md` §"Merge dedup — role_assignment ancillary re-homing".
- Ancillary `entity_changes` emit — DB touch triggers (#327): the parent 'updated' signal for polymorphic ancillary lives in a **DB touch-cascade trigger**, not app code, so every write path (admin CRUD / public observation / merge / direct SQL) signals uniformly. User-facing state = trigger, single source, **no manual emit**: `entity_addresses`, `contact_methods` (`trg_touch_entity_on_contact_change`, #327), `links` (`trg_touch_entity_on_link_change`, #327), `identifiers` (`trg_touch_entity_on_identifier_change` — now incl. `role_assignment`, #327), `citations` (`trg_touch_entity_on_citation_change`, #319 — indirects `person_name`→person, `entity_event`→owning entity), names/acronyms/affiliations. Triggers emit **per row**. Ingestion telemetry (`field_confidence`, `import_provenance`, written per-observation) stays **trigger-less** — a merge/cleanup re-home emits those manually, gated on `TRIGGERLESS_ANCILLARY_TABLES`. **Never** emit `entity_changes` for contacts/links/identifiers from app code (the trigger already fires → double-signal); #327 removed the old manual emits in `observation.py` + narrowed the merge `rehome_*` emits accordingly. Admin factories rely on the trigger (no app emit). Full rules → `docs/CONVENTIONS.md` §"Ancillary `entity_changes` emit — DB touch triggers".
- Citations — source provenance (#319): `citations` is a polymorphic no-FK ancillary (7 citable types: org/person/role/role_assignment/jurisdiction/person_name/entity_event) capturing human-checkable evidence (`url`/`title`/`excerpt`/`accessed_at`) for a fact — a fifth provenance axis beside `source_key_id`/`import_provenance`/`field_confidence`; supersedes the `role_assignments.notes` stopgap (#314/#318). Identity `(entity_type, entity_id, field_name, url)` `NULLS NOT DISTINCT` over active rows (one URL-less citation per (entity,field); `field_name` NULL = whole-entity, non-NULL validated against `CITABLE_FIELDS`); `title`/`excerpt`/`accessed_at` mutable. Observable/retractable like events (`src/core/citations.py`: natural-key refine-or-create, `pm_citation_id` refine, `op="retract"` archive + anti-resurrection, `source_key_id` same-or-NULL gate). Transports: embedded `citations:[]` on org/person observations (all-or-nothing) + native `POST /api/v1/citations/{entity_type}/{id}/observations` (partial-success), read `GET /api/v1/citations/{entity_type}/{id}`; scopes `citations:read`/`citations:write`. Merge re-homing via `migrate_citations`/`rehome_citations` (wired into the assignment/role re-home + person/org merge deletes); orphan audit `count_orphaned_citations` (`citation.*` scope). `.notes` migration: `scripts/migrate_notes_to_citations.py` (dry-run → `--execute`). Full rules → `docs/CONVENTIONS.md` §"Citations — source provenance". Design: `docs/plans/2026-07-29-citations-pattern-design.md`.
- Role-assignment relationships — RA→RA edges (#301): `role_assignment_relationships` is a **directional, temporal, FK-backed** edge (both endpoints `REFERENCES role_assignments ON DELETE CASCADE`) modelling staffer→principal — `from`=staffer's assignment `--staff_of-->` `to`=principal's seat assignment (typed via `role_assignment_relationship_types`, seed `staff_of`). Identity `(from,to,rel_type)` over active rows; `valid_from`/`valid_until`/`notes` mutable. **Temporal invariant** (edge window ⊆ both endpoint windows; dies when either endpoint ends) is enforced **app-layer only on admin/direct writes** (`check_edge_within_assignments`), observation path **records freely** (#307 pattern) — no blanket DB invariant trigger. **Cascade** (`cascade_assignment_relationships` on `role_assignments`, WHEN-gated on start/end/archived_at) auto-clamps/archives dependent edges on endpoint shrink/archive: `valid_from` clamps a *defined* start only (never invents an unknown one, #307), `valid_until` clamps down / closes a NULL-ongoing edge at a defined endpoint end, invert→archive; every mutation self-emits. **Own change-feed `entity_type`** `role_assignment_relationship` **+ touches both endpoints** (CHECKs extended on `entity_changes`/`deleted_entities`/`api_key_entity_subscriptions`). Observation `src/core/assignment_relationships.py` (pm-native, natural-key refine-or-create + `pm_relationship_id` refine + `op="retract"`, `source_key_id` gate); native `POST /api/v1/assignment-relationships/observations` (partial-success) + read `GET /api/v1/assignments/{pm_assignment_id}/relationships`; scopes `assignment_relationships:read`/`:write`; admin panel `role_assignments_relationships.py`. **Merge:** `rehome_assignment_relationships` re-points active edges onto the survivor **before** the assignment hard-delete (else FK CASCADE silently drops them) — wired into `people_merge`/`orgs_roles::role_merge`/`orgs_merge` (both role-pair sites); no orphan-audit scope (FK ⇒ no orphans). Window drift reconciled by `scripts/audit_assignment_relationship_windows.py` (shares the cascade clamp rule). Backfill `scripts/backfill_assignment_relationships.py` (3 rows, supervised). Full rules → `docs/CONVENTIONS.md` §"Role-assignment relationships". Design: `docs/plans/2026-08-01-assignment-relationships-design.md`.
- Person-name visibility rule (deadnames, hidden, legal-only): see `docs/CONVENTIONS.md` §"Person names — i18n & cultural awareness".
- Person **canonical/display** name (#308): `person_names.is_canonical` is the **display pointer** — `UNIQUE (person_id) WHERE is_canonical` (one per person) plus `CHECK (NOT is_canonical OR visibility='public')` (always displayable). Same model as `uq_org_canonical_name`. So `v_person_display_names` is a plain join — no `DISTINCT ON`, no priority ladder. A `deadname` can never be canonical (`NEVER_CANONICAL_NAME_TYPES`); client hints for one are ignored rather than raising. `write_names` auto-promotes one name per write when no hint is sent, and heals a canonical-less person on re-observation; `NO_AUTO_CANONICAL_NAME_TYPES` is never auto-promoted. Name families (a name and its furigana/romaji) are modelled by the `reading_of_id` FK, **not** by sharing a canonical slot. Every path that can strand a person without a display pointer repairs it: observation, name delete, and merge call `heal_person_canonical` — the shared best-effort repair (savepoint; swallows `PostgresError`; WARNING for anything but a lost race) — while the one-off `#308c` backfill runs its own SQL sharing the same ladder via `name_type_priority_sql()`. The org name-delete path has its own equivalent heal in `orgs_names.py`. Person names dedup on `(name, name_type, locale, script)`, never on `name` alone; merge additionally requires equal `visibility` and loosens `name_type` to collapse two *ordinary display* types carrying the same text, but never a `NO_AUTO_CANONICAL_NAME_TYPES` row. Admin validates canonical against `name_type` *and* the **effective** visibility, because `trg_deadname_visibility` rewrites visibility after submission. Full rules → `docs/CONVENTIONS.md` §"Canonical name = the display pointer".
- Raw `person_names` access: AND-append `visibility='public'` or call `visible_names_filter()` from `src.core.db`. Lint enforces.
- Structured name parts: `person_name_parts` is a 1:0..1 sidecar to `person_names` (ON DELETE CASCADE). Never auto-*written* — only populated when an upstream source supplies structure or a human confirms a suggestion. Assisted decomposition via `src.core.normalizers.person_name.suggest_parts(...)` is allowed (CSV triage / UI pre-fill); persistence still goes through the unified name-row form (`POST /admin/people/{pid}/names/{nid}/edit-row/`). See `docs/CONVENTIONS.md` §"Structured parts (`person_name_parts` sidecar)".
- BCP 47 / ISO 15924 lookup tables (`bcp47_locales`, `iso15924_scripts`) are FK-validated on `person_names.locale` / `.script`. After `apply_schema` on a fresh DB, seed via `uv run --group seed scripts/seed_locales_scripts.py --execute` (dry run without `--execute`, #402; see `docs/COMMANDS.md`); `apply_schema` logs a WARNING when either table is empty.
- Inline constraint drift (#307/#312/#315): `CREATE TABLE IF NOT EXISTS` no-ops on an existing table, so a `CHECK`/`FK`/`ON DELETE` modifier added inline after a table shipped never reaches a DB whose table predates it (FK `entity_events_event_place_address_id_fkey` sat at NO ACTION in prod while code said SET NULL). Every inline constraint change ships with an idempotent reconciliation `DO` block (ADD-when-absent for CHECKs; key on `confdeltype <> 'n'` for FK actions). Continuous guard: daily `power-map-schema-parity.timer` diffs full `pg_get_constraintdef` **+ `pg_get_functiondef` + `pg_get_triggerdef`** prod-vs-reference (exit 3; #331 extended constraints→functions/triggers for the `CREATE OR REPLACE` change-feed surface — extension-owned/internal objects excluded, version-sensitive kinds skipped on a PG-major mismatch). A fresh-DB-only unit guard can't replace it — 73 inline constraints have no reconciliation block by design. **Backfill-before-trigger (#392):** a reconciliation that backfills a column must appear **before** any `set_updated_at()` trigger on that table — file order is execution order and the trigger overwrites `NEW.updated_at` with NOW(), silently clobbering the backfill; fresh-DB tests can't catch it.
- Integration tests: require `TEST_DATABASE_URL`; never run against the production DB
- Integration test fixtures: acquire from `db_pool` (session-scoped), not `asyncpg.connect`. Full recipe + the `loop_scope="session"` gotcha → `docs/CONVENTIONS.md`. Reference: `tests/api/admin/test_people_names.py`.
- Endpoint-test client: prefer the lifespan-less rollback client (`AsyncClient` + `ASGITransport` + `get_db` override yielding a BEGIN/ROLLBACK `db_pool` connection) over per-test `with TestClient(app)` — no per-test `asyncpg.create_pool`, auto rollback, no manual teardown (#288). Pure-unit route tests (mocked `get_db`) use `TestClient(app)` without `with`. Full rationale + recipe → `docs/CONVENTIONS.md` §DB. Reference: `tests/api/admin/test_orgs.py`.
- A11y test tiers: **static lint** (`test_aria_labels.py`, template source) → **rendered lxml sweep** (`test_a11y_render.py`, #246, resolved DOM over every admin GET route, in-process rollback) → **real-browser axe sweep** (`test_a11y_browser.py`, #300, headless Chromium + axe-core full ruleset — contrast/roles/landmarks/focus — over full pages only). All three share one route enumeration + seed in `tests/api/admin/admin_routes.py` (never re-derive). The browser tier is marker-gated `-m browser`, excluded by default and **never in pre-commit**; run as a pre-release gate — one-time `uv sync --group browser && playwright install chromium`, then run alone (truncate-and-seeds the test DB; no `CREATEDB` on managed PG so no disposable DB). See `docs/COMMANDS.md` § Browser Testing.

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
| Outbox/tombstone TTL prune | daily `power-map-prune.timer` runs `scripts/prune_outbox.py --execute` (90-day window, `entity_changes` + `deleted_entities`); see `docs/COMMANDS.md` |
| Per-key API anomaly check | hourly `power-map-anomaly.timer` runs `scripts/check_api_anomalies.py` — journal WARNING + exit 3 per key ≥ `API_ANOMALY_HOURLY_THRESHOLD` req/hr (#294); human layer = Admin → Activity → API Requests per-key panel; see `docs/COMMANDS.md` |
| Schema-parity audit | daily `power-map-schema-parity.timer` runs `scripts/audit_schema_constraint_parity.py` — snapshots full `pg_get_constraintdef` + `pg_get_functiondef` + `pg_get_triggerdef` on reference (`PARITY_REFERENCE_URL`, default `TEST_DATABASE_URL`) vs prod, exit 3 on any missing/different object, per-kind breakdown `constraint.*`/`function.*`/`trigger.*` (#315 constraints + #331 functions/triggers; `CREATE TABLE IF NOT EXISTS` inline-drift + `CREATE OR REPLACE` body-drift; extension-owned/internal excluded; function/trigger diff skipped on a PG-major mismatch); see `docs/COMMANDS.md` |
| role / role_assignment / citation ancillary orphan audit | daily `power-map-ancillary-orphans.timer` runs `scripts/audit_ancillary_orphans.py` — anti-join count of no-FK polymorphic ancillary keyed on a non-existent parent, over **three** scopes: `role_assignment` (`links`/`contact_methods`/`field_confidence`/`identifiers`, #324), `role` (`links`/`contact_methods`, #326), and `citation` (all 7 citable entity types, #319); exit 3 on any orphan, breakdown namespaced `role.*`/`role_assignment.*`/`citation.*`. Recovery: `scripts/cleanup_role_assignment_ancillary_orphans.py` (heuristic re-home, dry-run → `--execute`; role_assignment-only — role/citation orphans go to manual triage); see `docs/COMMANDS.md` |
| assignment-relationship window audit | daily `power-map-assignment-rel-windows.timer` runs `scripts/audit_assignment_relationship_windows.py` — report-only reconcile of active `role_assignment_relationships` edges whose window drifted outside the intersection of both endpoint assignment windows (or whose endpoint archived); shares the `cascade_assignment_relationships` clamp rule (#301). Categories `clamp`/`inverted`/`archived_endpoint`; **exit 3 on any finding** (#363) so a drifted run surfaces in `systemctl --failed`. `--execute` clamps/archives and always exits 0 (supervised); see `docs/COMMANDS.md` |
| Weekly a11y sweep | weekly `power-map-a11y.timer` (Sun 04:00 UTC) runs `scripts/run-a11y-sweep.sh` — both a11y tiers (lxml `test_a11y_render.py -m integration` + Playwright/axe `test_a11y_browser.py -m browser`, #369) against the test DB, own uvicorn on an ephemeral port. **Chromium guard** exits 2 if Playwright's browser is absent (else the tier importorskips to a vacuous pass); one-time `uv sync --group browser && playwright install chromium`. Non-zero exit → `systemctl --failed`; on failure the runner opens-or-updates the `a11y-regression` GH issue (closes on recovery), and the `SessionStart` hook `.claude/hooks/a11y-status-reminder.sh` surfaces status when you open Claude on the VM. See `docs/COMMANDS.md` |

**Operational scripts — dry run by default (#402):** `DATABASE_URL` resolves to **production** from any directory, so a `scripts/` writer gates the write behind `--execute` and calls `echo_target()` (`scripts/_dsn.py`) before connecting — the gate stops an unintended write, the echo makes an intended one attributable. `redact_dsn()` returns `None` for a non-URL DSN and callers **never** fall back to the raw string (a libpq keyword/value DSN puts the password in `urlparse`'s `path`). Dry runs come in two shapes — read-only preview, or real work rolled back; the second must account for side effects that do *not* roll back (the importer parses addresses locally rather than spending validator quota). DDL is never implicit: `--apply-schema` is opt-in and requires `--execute`. `apply-schema.sh` keeps a duplicate copy of the redaction on purpose (`ExecStartPre` — an import failure = failed prod restart), pinned by a parity test. **#399** made this uniform across all 37 scripts: `add_dsn_args(parser)` + `resolve_dsn(args, parser)` give every script `--database-url` and `--test` (which **hard-errors** when `TEST_DATABASE_URL` is unset rather than falling back to production), and the echo carries a `(production|test|unknown — assume production)` label keyed on `(host, port, dbname)` — not the DSN string, since production is reached as two users. `tests/scripts/test_dsn_sweep.py` enforces all three rules by AST with **no allowlist**. Full rules → `docs/CONVENTIONS.md` §"Operational scripts — dry run by default & target echo".

**apply-schema guards (#398):** `apply-schema.sh` writes to **production** on the bare invocation and is `ExecStartPre` on the unit — so every guard it grows must be untrippable by the systemd shape (main checkout, no TTY, no flags), else a guard bug becomes a failed prod restart; that covers the diagnostics too (the target echo degrades to `(unparsed DSN — cannot redact)` rather than aborting, and never echoes a non-URL DSN — it would carry the password). Hard-fail (exit 2): linked worktree, declined TTY confirmation. Warn only: tracked modifications, branch ≠ `main`. The script `cd`s to its own repo root first, so the checkout it reports is the tree whose `schema.sql` it applies. `--test` targets `TEST_DATABASE_URL` and is the door for worktree schema work (`sync-schema-to-do.sh` delegates to it); `--yes` skips the guards; `--dry-run` stops after the target echo. Full rules → `docs/COMMANDS.md` § Deploy.

Full command reference: `docs/COMMANDS.md`

### Environment Variables

| File | Owner | Contents |
|---|---|---|
| `/etc/power-map/.env` | root:exedev (640) | `DATABASE_URL`, `MIGRATIONS_DATABASE_URL`, `TEST_DATABASE_URL`, `ADDRESS_VALIDATOR_API_KEY`, `ADDRESS_VALIDATOR_RUN_VALIDATION` (default false; true → calls `/validate` for validation status, false → `/standardize` only), `ADDRESS_VALIDATOR_BASE_URL` (optional; defaults to `https://address-validator.exe.xyz:8000`; override to point at dev server on port 8001), `DB_POOL_MIN_SIZE` (default 2), `DB_POOL_MAX_SIZE` (default 5; tune per DO tier connection limit), `API_REQUEST_LOG_MAX_PENDING` (optional; default 50; soft cap on in-flight fire-and-forget `api_request_log` capture writes before shedding — #290; tune relative to `DB_POOL_MAX_SIZE`; `0` disables capture entirely), `RATE_LIMIT_READ_PER_S` / `RATE_LIMIT_READ_BURST` / `RATE_LIMIT_WRITE_PER_S` / `RATE_LIMIT_WRITE_BURST` (optional; defaults 2/120/1/60; per-key token buckets on the public API — #292; read = GET/HEAD + read-semantic POSTs (`identify`, `verify`, `verify-batch`, `embeddings/presence`) — routes declare `openapi_extra={BUCKET_EXTRA_KEY: "read"}` and `src.api.main` installs the derived path set at app build (#310); per-worker, so effective ceiling ≈ workers × rate; refill ≤ 0 disables that bucket), `API_KEY_LAST_USED_DEBOUNCE_S` (optional; default 60; min seconds between `api_keys.last_used_at` stamps per worker — #292; `0` stamps every request), `API_ANOMALY_HOURLY_THRESHOLD` (optional; default 5000; requests per key per hour at/above which the hourly anomaly check WARNs — #294; deliberately below the ~14.4k/hr rate-limit ceiling so near-ceiling runaways are caught; `<= 0` disables the check and the admin hot-row highlighting), `PARITY_REFERENCE_URL` (optional; reference DB for the daily schema-parity audit — #315/#331; falls back to `TEST_DATABASE_URL`; point at a scratch DB freshly built from empty for a gold-standard run — and keep it on prod's PG major, else the function/trigger diff self-skips) |
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
